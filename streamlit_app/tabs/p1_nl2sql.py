"""Tab P1: 自然语言 → SQL → 结果。"""

import uuid

import streamlit as st

from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.llm.langfuse_feedback import submit_user_feedback
from streamlit_app.components.chart_block import render_chart_block
from streamlit_app.components.dataframe_block import render_dataframe_block
from streamlit_app.components.sql_block import render_sql_block

_SESSION_KEY = "p1_last_result"
_AGENT_KEY = "p1_agent"
_FEEDBACK_KEY = "p1_last_feedback"  # {trace_id: "1"|"0"} 记住本会话已反馈的 trace


def _get_agent() -> P1NL2SQLAgent:
    if _AGENT_KEY not in st.session_state:
        st.session_state[_AGENT_KEY] = P1NL2SQLAgent(top_k=4)
    return st.session_state[_AGENT_KEY]


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

    render_sql_block(result.sql)
    df = render_dataframe_block(result.rows)
    render_chart_block(df, key="p1")
    st.caption(f"尝试次数 {result.attempts} | 耗时 {result.total_latency_ms} ms")

    _render_feedback(result)


def _render_feedback(result) -> None:
    """👍/👎 反馈 → Langfuse score(user_feedback)。仅在 SQL 执行成功且有 trace_id 时显示。

    生产同域 Q-SQL pool 的数据源：夜间 cron 跑
    `python scripts/bootstrap_prod_pool.py --source langfuse`，会把这里 👍 过的
    (question, sql) 拉进 data/example_pool_prod.jsonl。
    """
    if result.error_class is not None or not result.sql:
        return
    if not result.trace_id:
        st.caption("💡 Langfuse trace_id 缺失，反馈按钮不可用（检查 Langfuse 是否已配置）")
        return

    feedback_map: dict = st.session_state.setdefault(_FEEDBACK_KEY, {})
    already = feedback_map.get(result.trace_id)
    if already:
        st.caption(
            "👍 已标记（进入 pool 候选）" if already == "1" else "👎 已标记（进入回归集）"
        )
        return

    st.markdown("**这条结果对你有帮助吗？**")
    col_up, col_down, _ = st.columns([1, 1, 6])
    with col_up:
        if st.button("👍 有用", key=f"p1_thumb_up_{result.trace_id}"):
            if submit_user_feedback(result.trace_id, value=1.0, comment="ui p1 thumbs up"):
                feedback_map[result.trace_id] = "1"
                st.success("反馈已记录，将进入生产 pool 候选")
                st.rerun()
            else:
                st.warning("反馈提交失败（Langfuse 未就绪？）")
    with col_down:
        if st.button("👎 不对", key=f"p1_thumb_down_{result.trace_id}"):
            if submit_user_feedback(result.trace_id, value=0.0, comment="ui p1 thumbs down"):
                feedback_map[result.trace_id] = "0"
                st.success("反馈已记录，将纳入回归测试集")
                st.rerun()
            else:
                st.warning("反馈提交失败（Langfuse 未就绪？）")
