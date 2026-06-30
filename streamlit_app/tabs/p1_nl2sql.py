"""Tab P1: 自然语言 → SQL → 结果。"""

import uuid

import streamlit as st

from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from streamlit_app.components.chart_block import render_chart_block
from streamlit_app.components.dataframe_block import render_dataframe_block
from streamlit_app.components.sql_block import render_sql_block

_SESSION_KEY = "p1_last_result"
_AGENT_KEY = "p1_agent"


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
