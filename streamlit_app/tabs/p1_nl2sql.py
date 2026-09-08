"""Tab P1: 自然语言 → SQL → 结果。"""

import uuid

import streamlit as st

from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog
from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.p1.wiring import (
    IDENTITIES,
    build_metric_router_if_available,
    build_p1_agent,
    build_retriever_if_available,
    session_props_for,
)
from streamlit_app.components.chart_block import render_chart_block
from streamlit_app.components.dataframe_block import render_dataframe_block
from streamlit_app.components.feedback_block import render_feedback_block
from streamlit_app.components.guide_block import render_guide_block
from streamlit_app.components.sql_block import render_sql_block

_SESSION_KEY = "p1_last_result"
_AGENT_KEY = "p1_agent"
_IDENTITY_KEY = "p1_identity"

# 生产接线（池路径、指标目录、身份名单）统一由 chat_bi_agent.agents.p1.wiring 定义，
# Streamlit 与 MCP server 共用同一份——见 tests/p1/test_p1_wiring_shared.py 的同一性断言。
# 下面四个模块级别名是为了让本模块的既有调用点与测试打桩点保持原样。
_build_retriever_if_available = build_retriever_if_available
_build_metric_router_if_available = build_metric_router_if_available
_session_props_for = session_props_for


def _metric_label(catalog: MetricCatalog | None, metric_id: str) -> str:
    """给用户看业务名而非 metric_id；查不到就原样返回。"""
    if catalog is None or not metric_id:
        return metric_id
    try:
        return catalog.get(metric_id).display_name
    except Exception:
        return metric_id


def _metric_has_row_policies(catalog: MetricCatalog | None, metric_id: str) -> bool:
    if catalog is None or not metric_id:
        return False
    try:
        return bool(catalog.get(metric_id).row_policies)
    except Exception:
        return False


def _get_agent() -> P1NL2SQLAgent:
    if _AGENT_KEY not in st.session_state:
        agent, retriever, router = build_p1_agent()
        st.session_state[_AGENT_KEY] = agent
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
    if route == "metric_denied":
        # 拒绝就是拒绝：不给 SQL、不给数，也不偷偷换 NL2SQL 重答一遍
        st.error(
            "当前身份无权查询该指标——语义层已拒绝渲染。换一个有权限的身份，或让管理员补齐会话属性。"
        )
        return
    if route != "metric":
        return
    catalog = st.session_state.get("p1_metric_catalog")
    label = _metric_label(catalog, result.metric_id or "")
    st.success(f"命中语义层指标：**{label}** — 模板 SQL，口径固定、结果可复现")
    if _metric_has_row_policies(catalog, result.metric_id or ""):
        st.caption(
            f"该指标受行级权限管控，本次以「{st.session_state.get(_IDENTITY_KEY, '')}」"
            f"的身份查询——权限条件在渲染期注入，模型看不到它。"
        )
    # 身份值在库里探不到：数照出，但要说清楚这个 0 可能不是业务事实
    policy_warning = getattr(result, "metric_policy_warning", None)
    if policy_warning:
        st.warning(policy_warning)

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
    render_guide_block("p1", input_key="p1_question_input")

    identity = st.selectbox(
        "当前登录身份",
        list(IDENTITIES),
        key=_IDENTITY_KEY,
        help=(
            "行级权限（RLAC）在 SQL 渲染期注入，模型看不到权限条件。"
            "分行身份只能看到本行的数据；换个身份问同一个问题，数会变。"
        ),
    )

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
                    session_props=_session_props_for(identity),
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
        if result.metric_fail_reason == "rlac_denied":
            # 这条只可能出现在「身份属性给漏了」时；用户看得懂才报得上来
            caption_parts.append("语义层拒绝渲染（当前身份缺少所需权限属性），已回退 NL2SQL")
        else:
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
