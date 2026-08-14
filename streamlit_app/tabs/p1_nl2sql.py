"""Tab P1: 自然语言 → SQL → 结果。"""

import uuid
from pathlib import Path

import streamlit as st

from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog, MetricRouter
from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.shared.example_retriever import (
    ExamplePool,
    ExampleRetriever,
)
from chat_bi_agent.agents.shared.sql_executor import SQLExecutor
from chat_bi_agent.llm import qwen_client
from streamlit_app.components.chart_block import render_chart_block
from streamlit_app.components.dataframe_block import render_dataframe_block
from streamlit_app.components.feedback_block import render_feedback_block
from streamlit_app.components.sql_block import render_sql_block

_SESSION_KEY = "p1_last_result"
_AGENT_KEY = "p1_agent"

# 生产 pool 路径（相对 repo 根）。bootstrap_prod_pool.py 落这个文件；
# 反馈闭环夜间任务往这个文件追加。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_POOL_PATH = _REPO_ROOT / "data" / "example_pool_prod.jsonl"

# 生产用同域池：min_sim 0.7 比 BIRD 跨库的 0.55 严格（宁缺毋滥），
# k=3 上限；池子小的时候大部分调用会返回空 list，行为等价 few-shot off。
_PROD_MIN_SIM = 0.7
_PROD_TOP_K = 3

# 语义层指标目录。命中即走 governed 模板 SQL（结果可复现、口径可审计），
# 未命中回退原 NL2SQL——2026-08-13 A/B 判定绿灯，见 DESIGN_DECISIONS.md#adr-013。
_METRICS_CATALOG_PATH = _REPO_ROOT / "config" / "metrics.yaml"


def _build_retriever_if_available() -> ExampleRetriever | None:
    """池子文件存在且非空才挂 retriever；否则完全跳过（不影响 P1 原有行为）。"""
    if not _PROD_POOL_PATH.exists():
        return None
    pool = ExamplePool.load(_PROD_POOL_PATH)
    if len(pool) == 0:
        return None
    return ExampleRetriever(
        pool=pool,
        dialect="postgres",
        embed_fn=qwen_client.embed,
        min_similarity=_PROD_MIN_SIM,
        max_k=_PROD_TOP_K,
    )


def _build_metric_router_if_available() -> MetricRouter | None:
    """目录存在才挂路由；任何失败都降级成 None。

    语义层是增强项，不能把主路径带崩：构造要 embed 全部 alias（当前 86 条），
    DashScope 抖动或目录写坏时，宁可少一个增强项也不能让整个 P1 tab 不可用。
    阈值用 MetricRouter 的默认值（0.63，34 题标尺实测选出），不在这里硬编码。
    """
    if not _METRICS_CATALOG_PATH.exists():
        return None
    try:
        catalog = MetricCatalog.from_yaml(_METRICS_CATALOG_PATH)
        if not catalog.metrics:
            return None
        return MetricRouter(
            catalog=catalog,
            embed_fn=qwen_client.embed,
            # probe_fn 必须给：string filter 塞错值时 SQL 依然合法，
            # 只会静默返回空结果——这是唯一能拦住它的闸
            probe_fn=SQLExecutor().execute,
        )
    except Exception:
        return None


def _metric_label(catalog: MetricCatalog | None, metric_id: str) -> str:
    """给用户看业务名而非 metric_id；查不到就原样返回。"""
    if catalog is None or not metric_id:
        return metric_id
    try:
        return catalog.get(metric_id).display_name
    except Exception:
        return metric_id


def _get_agent() -> P1NL2SQLAgent:
    if _AGENT_KEY not in st.session_state:
        retriever = _build_retriever_if_available()
        router = _build_metric_router_if_available()
        st.session_state[_AGENT_KEY] = P1NL2SQLAgent(
            top_k=4, example_retriever=retriever, metric_router=router
        )
        st.session_state["p1_pool_size"] = len(retriever.pool) if retriever is not None else 0
        # 缓存 catalog 供展示层把 metric_id 翻成业务名
        st.session_state["p1_metric_catalog"] = router.catalog if router is not None else None
    return st.session_state[_AGENT_KEY]


def _render_route_block(result) -> None:
    """命中语义层时告诉用户"这条查询是模板出的"，并摊开它的理解。

    这是治理特性的兑现点：模板 SQL 口径固定、结果可复现，用户有权知道
    这次走的是哪条路；摊开 spec 也让"识别错了"能被当场发现，而不是等到
    看见一个似是而非的数字。
    """
    route = getattr(result, "route", "nl2sql")
    if route != "metric":
        return
    catalog = st.session_state.get("p1_metric_catalog")
    label = _metric_label(catalog, result.metric_id or "")
    st.success(f"命中语义层指标：**{label}** — 模板 SQL，口径固定、结果可复现")

    spec = result.metric_spec or {}
    with st.expander("语义层是怎么理解这个问题的"):
        rows = [("指标", label)]
        if spec.get("dims"):
            rows.append(("分组维度", "、".join(spec["dims"])))
        for f in spec.get("filters") or []:
            val = f.get("val")
            val = "、".join(str(v) for v in val) if isinstance(val, list) else str(val)
            rows.append((f"过滤 {f.get('col')}", f"{f.get('op', '=')} {val}"))
        tw = spec.get("time_window")
        if tw:
            rows.append(("时间窗", f"{tw.get('start')} ~ {tw.get('end')}"))
        for k, v in rows:
            st.markdown(f"- **{k}**：{v}")
        st.caption("对不上就说明识别有偏差——把问题说得更具体些重试，或反馈 👎 让这条进入改进池。")


def render_p1_tab(call_counter: dict) -> None:
    st.subheader("P1：自然语言 → SQL")
    st.caption("输入业务问题，自动生成并执行 SQL，返回结果数据与图表。")

    question = st.text_area(
        "问题",
        height=100,
        placeholder="例：2026 年 1 月各渠道存款余额合计",
        key="p1_question_input",
    )

    if st.button("执行", key="p1_run_btn", type="primary"):
        if not question.strip():
            st.warning("请输入问题")
            return
        with st.spinner("P1 NL2SQL 执行中..."):
            try:
                result = _get_agent().run(
                    question_id=f"ui_p1_{uuid.uuid4().hex[:8]}",
                    question=question.strip(),
                )
                call_counter["count"] = call_counter.get("count", 0) + 1
                st.session_state[_SESSION_KEY] = result
            except Exception as e:
                st.error(f"Agent 执行失败：{type(e).__name__}: {e}")
                with st.expander("详细错误"):
                    st.exception(e)
                return

    result = st.session_state.get(_SESSION_KEY)
    if result is None:
        st.info("尚无结果，提交一个问题试试")
        return

    if result.error_class is not None:
        st.error(f"SQL 执行失败：{result.error_class.value}，尝试 {result.attempts} 次")

    _render_route_block(result)
    render_sql_block(result.sql)
    df = render_dataframe_block(result.rows)
    render_chart_block(df, key="p1")
    caption_parts = [
        f"尝试次数 {result.attempts}",
        f"耗时 {result.total_latency_ms} ms",
    ]
    if getattr(result, "route", "nl2sql") == "metric_then_nl2sql":
        # 低调提示：语义层试过但没走通，已安全回退，答案质量不受影响
        caption_parts.append(f"语义层未采用（{result.metric_fail_reason}），已回退 NL2SQL")
    pool_size = st.session_state.get("p1_pool_size", 0)
    if pool_size > 0:
        n_used = len(getattr(result, "retrieved_example_ids", []) or [])
        caption_parts.append(f"few-shot pool={pool_size}，本次引用 {n_used} 条")
    st.caption(" | ".join(caption_parts))

    render_feedback_block(
        trace_id=result.trace_id,
        tab_key="p1",
        is_valid=(result.error_class is None and bool(result.sql)),
        invalid_hint="SQL 执行失败，无法反馈",
    )
