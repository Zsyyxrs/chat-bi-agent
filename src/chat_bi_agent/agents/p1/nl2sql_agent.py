"""P1 NL2SQL Agent：编排 SchemaLinker → SQLGenerator → SQLValidator → SQLExecutor，
失败时由 Reflector 决策是否重试。整个 run 是一条 Langfuse trace。
"""

import time
from dataclasses import dataclass, field

from langfuse import get_client, observe

from chat_bi_agent.agents.p1.metric_resolver import MetricRouter
from chat_bi_agent.agents.p1.reflector import ReflectAction, Reflector
from chat_bi_agent.agents.p1.sql_generator import InvalidJsonError, SQLGenerator
from chat_bi_agent.agents.p1.sql_validator import SQLValidator
from chat_bi_agent.agents.shared.example_retriever import ExampleRetriever
from chat_bi_agent.agents.shared.schema_linker import SchemaLinker
from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass, SQLExecutor
from chat_bi_agent.config import PG_STATEMENT_TIMEOUT_MS, TOP_K_NL2SQL
from chat_bi_agent.schema.loader import SchemaLoader

# ADR-006 决定「Reflector 只做 1 次重试」，Alternatives 里明确否决了「多次重试
# （e.g. 3 次）」。但这里长期写的是 3，配合 range(1, MAX_ATTEMPTS + 1) 实际跑
# 1 初次 + 2 次重试——正是被否决的那个方案，实现没跟上决策。
#
# 2026-08-15 翻全部 results/ 产物统计，证据支持 ADR 的判断：
#   attempts=2（第 1 次重试）：出现 23 次，最终成功 13 次（57%）
#   attempts=3（第 2 次重试）：出现 27 次，最终成功  0 次（0%）
# 第二次重试一次都没救回来过。改回 2 对分数零影响（那 27 题少跑一次仍然失败，
# 结果相同），省下的是 27 次白打的 LLM 调用与对应延迟。
MAX_ATTEMPTS = 2


@dataclass
class P1AgentResult:
    question_id: str
    sql: str | None
    rows: list[dict] | None
    execution_error: str | None
    error_class: SQLErrorClass | None
    schema_link_top_k: list[str]
    thought: str
    attempts: int
    total_latency_ms: int
    reflect_history: list[dict] = field(default_factory=list)
    retrieved_example_ids: list[str] = field(default_factory=list)
    trace_id: str | None = None
    # 语义层前置路由字段（ADR-013 集成，metric_router=None 时全 None/"nl2sql"）
    route: str = "nl2sql"  # "nl2sql" | "metric" | "metric_then_nl2sql"
    metric_id: str | None = None
    prefilter_cosine: float | None = None
    metric_spec: dict | None = None  # 命中且 resolve 成功时落 MetricSpec 的 dict 形式
    metric_fail_reason: str | None = None


class P1NL2SQLAgent:
    """构造时一次性加载 schema + 构建 embedding 索引。"""

    def __init__(
        self,
        top_k: int = TOP_K_NL2SQL,
        statement_timeout_ms: int = PG_STATEMENT_TIMEOUT_MS,
        dialect: str = "postgres",
        example_retriever: ExampleRetriever | None = None,
        metric_router: MetricRouter | None = None,
        tag_route_on_trace: bool = False,
    ):
        self.dialect = dialect
        self.loader = SchemaLoader()
        self.loader.load()
        self.loader.build_index()
        self.schema_linker = SchemaLinker(loader=self.loader, top_k=top_k)
        self.sql_generator = SQLGenerator(dialect=dialect)
        self.sql_validator = SQLValidator(dialect=dialect)
        self.sql_executor = SQLExecutor(statement_timeout_ms=statement_timeout_ms)
        self.reflector = Reflector(max_attempts=MAX_ATTEMPTS, dialect=dialect)
        self.example_retriever = example_retriever
        self.metric_router = metric_router
        # 把 route 以 tag 形式落到 trace 上——Langfuse 的 metrics 聚合层不认 metadata
        # （按 `metadata.route` 分组实测返回 400，合法维度只有 id/name/tags/userId/
        # sessionId/release/version/environment/timestampMonth），不打 tag 就画不出
        # metric_hit_rate。
        # 默认关：写 tags 是**覆盖**语义，只有当这次 run 自成一条 root trace 时才安全。
        # 6 个调用点里只有 P1 Streamlit tab 满足；run_p1_eval 里逐题 run 是嵌套在
        # p1_eval_batch 下的（实测一条 batch trace 274 个 observation），打上去会冲掉
        # 批次的 arm:* 标签，A/B 就失去按臂筛选的能力。P2/P3 内部调 P1 同理，
        # 而且一条 trace 里 P1 被调多次，route 只会剩最后一次，读数是错的。
        # 破坏性操作让调用方显式开，不靠默认值兜。
        self.tag_route_on_trace = tag_route_on_trace

    @observe(name="p1_nl2sql_run")
    def run(self, question_id: str, question: str) -> P1AgentResult:
        start = time.perf_counter()

        # 抓当前 langfuse trace_id → 传回 UI 层，供用户 👍/👎 反馈 attach 到同一条 trace
        try:
            trace_id: str | None = get_client().get_current_trace_id()
        except Exception:
            trace_id = None

        # ---- 语义层前置路由（metric_router=None 时短路） ----
        route: str = "nl2sql"
        r_metric_id: str | None = None
        r_prefilter_cosine: float | None = None
        r_metric_spec_dict: dict | None = None
        r_metric_fail_reason: str | None = None

        if self.metric_router is not None:
            from dataclasses import asdict as _asdict

            rr = self.metric_router.try_route(question)
            r_prefilter_cosine = rr.cosine
            if rr.prefilter_hit:
                r_metric_id = rr.metric_id
                if rr.sql is not None and rr.spec is not None:
                    # 命中且 resolve 成功：跑 template SQL 完整链
                    template_sql = rr.sql
                    val = self.sql_validator.validate(template_sql)
                    if not val.ok:
                        route = "metric_then_nl2sql"
                        r_metric_fail_reason = "validator_fail"
                    else:
                        rows, exec_err = self.sql_executor.execute(template_sql)
                        if exec_err is None:
                            elapsed_ms = max(1, int((time.perf_counter() - start) * 1000))
                            self._tag_trace(
                                [],
                                None,
                                [],
                                route="metric",
                                metric_id=rr.metric_id,
                                prefilter_cosine=rr.cosine,
                                metric_fail_reason=None,
                                tag_route=self.tag_route_on_trace,
                            )
                            return P1AgentResult(
                                question_id=question_id,
                                sql=template_sql,
                                rows=rows,
                                execution_error=None,
                                error_class=None,
                                schema_link_top_k=[],
                                thought="",
                                attempts=1,
                                total_latency_ms=elapsed_ms,
                                reflect_history=[],
                                retrieved_example_ids=[],
                                trace_id=trace_id,
                                route="metric",
                                metric_id=rr.metric_id,
                                prefilter_cosine=rr.cosine,
                                metric_spec=_asdict(rr.spec),
                                metric_fail_reason=None,
                            )
                        # executor failed
                        route = "metric_then_nl2sql"
                        r_metric_fail_reason = "executor_fail"
                else:
                    # resolve 失败
                    route = "metric_then_nl2sql"
                    r_metric_fail_reason = rr.fail_reason
                # 命中但没跑通：保留 spec 以便 A/B 事后审计
                if rr.spec is not None:
                    r_metric_spec_dict = _asdict(rr.spec)
            # prefilter miss：route 保持 "nl2sql"，仅 prefilter_cosine 记录

        matches = self.schema_linker.link(question)
        if not matches:
            raise RuntimeError(f"SchemaLinker 未召回任何表，question: {question!r}")
        top_names = [m.name for m in matches]
        schema_ddl = "\n\n".join(self.loader.get_ddl_text(name) for name in top_names)

        # Q-SQL few-shot 检索：一次调用，供本次 run 里所有 generate() 复用
        few_shot_pairs: list[tuple[str, str]] = []
        retrieved_example_ids: list[str] = []
        if self.example_retriever is not None:
            hits = self.example_retriever.retrieve(question, exclude_question_texts={question})
            few_shot_pairs = [(ex.question, ex.sql) for ex, _ in hits]
            retrieved_example_ids = [ex.example_id for ex, _ in hits]

        hint: str | None = None
        reflect_history: list[dict] = []

        last_sql: str | None = None
        last_thought: str = ""
        last_error_msg: str | None = None
        last_err_class: SQLErrorClass | None = None
        attempt = 0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            err_class: SQLErrorClass | None = None
            err_msg: str = ""
            prev_sql_for_reflect: str | None = None

            try:
                gen = self.sql_generator.generate(
                    question=question,
                    schema_ddl=schema_ddl,
                    repair_hint=hint,
                    few_shot_examples=few_shot_pairs or None,
                )
            except InvalidJsonError as e:
                err_class = SQLErrorClass.INVALID_JSON
                err_msg = str(e)
                prev_sql_for_reflect = None
                last_thought = ""
                last_sql = None
            else:
                last_sql = gen.sql
                last_thought = gen.thought

                val = self.sql_validator.validate(gen.sql)
                if not val.ok:
                    err_class = SQLErrorClass.VALIDATOR_FAIL
                    err_msg = val.error or ""
                    prev_sql_for_reflect = gen.sql
                else:
                    rows, exec_err = self.sql_executor.execute(gen.sql)
                    if exec_err is None:
                        elapsed_ms = max(1, int((time.perf_counter() - start) * 1000))
                        self._tag_trace(
                            reflect_history,
                            None,
                            retrieved_example_ids,
                            route=route,
                            metric_id=r_metric_id,
                            prefilter_cosine=r_prefilter_cosine,
                            metric_fail_reason=r_metric_fail_reason,
                            tag_route=self.tag_route_on_trace,
                        )
                        return P1AgentResult(
                            question_id=question_id,
                            sql=gen.sql,
                            rows=rows,
                            execution_error=None,
                            error_class=None,
                            schema_link_top_k=top_names,
                            thought=gen.thought,
                            attempts=attempt,
                            total_latency_ms=elapsed_ms,
                            reflect_history=reflect_history,
                            retrieved_example_ids=retrieved_example_ids,
                            trace_id=trace_id,
                            route=route,
                            metric_id=r_metric_id,
                            prefilter_cosine=r_prefilter_cosine,
                            metric_spec=r_metric_spec_dict,
                            metric_fail_reason=r_metric_fail_reason,
                        )
                    err_class = self.sql_executor.classify_error(exec_err)
                    err_msg = exec_err
                    prev_sql_for_reflect = gen.sql

            # 走到这里说明本轮失败，找 Reflector 决策
            last_err_class = err_class
            last_error_msg = err_msg
            decision = self.reflector.reflect(
                err_class=err_class,
                err_msg=err_msg,
                prev_sql=prev_sql_for_reflect,
                top_k_tables=top_names,
                attempt=attempt,
            )
            reflect_history.append(
                {
                    "attempt": attempt,
                    "err_class": err_class.value,
                    "action": decision.action.value,
                    "effective_err_class": (
                        decision.effective_err_class.value
                        if decision.effective_err_class is not None
                        else None
                    ),
                }
            )
            if decision.action == ReflectAction.GIVE_UP:
                break
            hint = decision.repair_hint

        elapsed_ms = max(1, int((time.perf_counter() - start) * 1000))
        self._tag_trace(
            reflect_history,
            last_err_class,
            retrieved_example_ids,
            route=route,
            metric_id=r_metric_id,
            prefilter_cosine=r_prefilter_cosine,
            metric_fail_reason=r_metric_fail_reason,
            tag_route=self.tag_route_on_trace,
        )

        return P1AgentResult(
            question_id=question_id,
            sql=last_sql,
            rows=None,  # 失败路径无 rows
            execution_error=last_error_msg,
            error_class=last_err_class,
            schema_link_top_k=top_names,
            thought=last_thought,
            attempts=attempt,
            total_latency_ms=elapsed_ms,
            reflect_history=reflect_history,
            retrieved_example_ids=retrieved_example_ids,
            trace_id=trace_id,
            route=route,
            metric_id=r_metric_id,
            prefilter_cosine=r_prefilter_cosine,
            metric_spec=r_metric_spec_dict,
            metric_fail_reason=r_metric_fail_reason,
        )

    @staticmethod
    def _tag_trace(
        reflect_history: list[dict],
        error_class: SQLErrorClass | None,
        retrieved_example_ids: list[str] | None = None,
        *,
        route: str = "nl2sql",
        metric_id: str | None = None,
        prefilter_cosine: float | None = None,
        metric_fail_reason: str | None = None,
        tag_route: bool = False,
    ) -> None:
        """把 reflect_history / error_class / metric router 字段写到 langfuse trace。

        `tag_route=True` 时额外把 route 打成 tag，让 Langfuse 能按它聚合出
        metric_hit_rate。默认 False：覆盖 tags 是破坏性的（见 __init__ 注释）。
        """
        try:
            client = get_client()
            kwargs: dict = {
                "metadata": {
                    "reflect_history": reflect_history,
                    "error_class": error_class.value if error_class else None,
                    "retrieved_example_ids": retrieved_example_ids or [],
                    "route": route,
                    "metric_id": metric_id,
                    "prefilter_cosine": prefilter_cosine,
                    "metric_fail_reason": metric_fail_reason,
                },
            }
            if tag_route:
                kwargs["tags"] = [f"route:{route}"]
            client.update_current_trace(**kwargs)
        except Exception:
            # Langfuse 未配置或 client 失败不应阻塞 agent
            pass
