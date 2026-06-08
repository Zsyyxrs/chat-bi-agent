"""P1 NL2SQL Agent：编排 SchemaLinker → SQLGenerator → SQLValidator → SQLExecutor，
失败时由 Reflector 决策是否重试。整个 run 是一条 Langfuse trace。
"""

import time
from dataclasses import dataclass, field

from langfuse import get_client, observe

from chat_bi_agent.agents.reflector import ReflectAction, Reflector
from chat_bi_agent.agents.schema_linker import SchemaLinker
from chat_bi_agent.agents.sql_executor import SQLErrorClass, SQLExecutor
from chat_bi_agent.agents.sql_generator import InvalidJsonError, SQLGenerator
from chat_bi_agent.agents.sql_validator import SQLValidator
from chat_bi_agent.schema.loader import SchemaLoader

MAX_ATTEMPTS = 3


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


class P1NL2SQLAgent:
    """构造时一次性加载 schema + 构建 embedding 索引。"""

    def __init__(self, top_k: int = 4, statement_timeout_ms: int = 10_000):
        self.loader = SchemaLoader()
        self.loader.load()
        self.loader.build_index()
        self.schema_linker = SchemaLinker(loader=self.loader, top_k=top_k)
        self.sql_generator = SQLGenerator()
        self.sql_validator = SQLValidator()
        self.sql_executor = SQLExecutor(statement_timeout_ms=statement_timeout_ms)
        self.reflector = Reflector(max_attempts=MAX_ATTEMPTS)

    @observe(name="p1_nl2sql_run")
    def run(self, question_id: str, question: str) -> P1AgentResult:
        start = time.perf_counter()

        matches = self.schema_linker.link(question)
        if not matches:
            raise RuntimeError(f"SchemaLinker 未召回任何表，question: {question!r}")
        top_names = [m.name for m in matches]
        schema_ddl = "\n\n".join(self.loader.get_ddl_text(name) for name in top_names)

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
                        self._tag_trace(reflect_history, None)
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
            reflect_history.append({
                "attempt": attempt,
                "err_class": err_class.value,
                "action": decision.action.value,
            })
            if decision.action == ReflectAction.GIVE_UP:
                break
            hint = decision.repair_hint

        elapsed_ms = max(1, int((time.perf_counter() - start) * 1000))
        self._tag_trace(reflect_history, last_err_class)

        return P1AgentResult(
            question_id=question_id,
            sql=last_sql,
            rows=None,   # 失败路径无 rows
            execution_error=last_error_msg,
            error_class=last_err_class,
            schema_link_top_k=top_names,
            thought=last_thought,
            attempts=attempt,
            total_latency_ms=elapsed_ms,
            reflect_history=reflect_history,
        )

    @staticmethod
    def _tag_trace(reflect_history: list[dict], error_class: SQLErrorClass | None) -> None:
        """把 reflect_history / error_class 写到当前 langfuse trace 的 metadata。"""
        try:
            client = get_client()
            client.update_current_trace(
                metadata={
                    "reflect_history": reflect_history,
                    "error_class": error_class.value if error_class else None,
                },
            )
        except Exception:
            # Langfuse 未配置或 client 失败不应阻塞 agent
            pass
