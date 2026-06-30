"""Tab P2: 多步分析（计划 → 分步执行 → 报告）。"""

import uuid

import streamlit as st

from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.p2 import P2MultiStepAnalysisAgent
from streamlit_app.components.chart_block import render_chart_block
from streamlit_app.components.dataframe_block import render_dataframe_block
from streamlit_app.components.insight_block import render_insight_block
from streamlit_app.components.sql_block import render_sql_block

_SESSION_KEY = "p2_last_result"
_AGENT_KEY = "p2_agent"


def _get_agent() -> P2MultiStepAnalysisAgent:
    if _AGENT_KEY not in st.session_state:
        p1 = P1NL2SQLAgent(top_k=4)
        st.session_state[_AGENT_KEY] = P2MultiStepAnalysisAgent(
            p1_agent=p1,
            schema_linker=p1.schema_linker,
            loader=p1.loader,
            top_k=8,
        )
    return st.session_state[_AGENT_KEY]


def render_p2_tab(call_counter: dict) -> None:
    st.subheader("P2：多步分析")
    st.caption("把复杂问题拆解为多步 SQL 执行，最后汇总成分析报告。")

    question = st.text_area(
        "问题",
        height=100,
        placeholder="例：分析 2026 年 Q1 三个月各渠道存款变化趋势及其与客户结构的关系",
        key="p2_question_input",
    )

    if st.button("执行", key="p2_run_btn", type="primary"):
        if not question.strip():
            st.warning("请输入问题")
            return
        with st.spinner("P2 多步分析执行中（可能耗时 10-30s）..."):
            try:
                report = _get_agent().run(
                    question_id=f"ui_p2_{uuid.uuid4().hex[:8]}",
                    question=question.strip(),
                )
                call_counter["count"] = call_counter.get("count", 0) + 1
                st.session_state[_SESSION_KEY] = report
            except Exception as e:
                st.error(f"Agent 执行失败：{type(e).__name__}: {e}")
                with st.expander("详细错误"):
                    st.exception(e)
                return

    report = st.session_state.get(_SESSION_KEY)
    if report is None:
        st.info("尚无结果，提交一个问题试试")
        return

    st.markdown("##### 分析计划")
    st.markdown(f"**计划类型**：{report.plan.plan_type}")
    for i, step in enumerate(report.plan.steps, 1):
        st.markdown(f"{i}. **{step.id}** — {step.question}")
        if step.rationale:
            st.caption(f"   依据：{step.rationale}")

    st.markdown("##### 分步执行")
    last_df = None
    for sr in report.step_results:
        with st.expander(f"Step {sr.step.id}: {sr.step.question}", expanded=False):
            if sr.skipped:
                st.info("（步骤被跳过）")
                continue
            if sr.error_class:
                st.error(f"错误：{sr.error_class.value} — {sr.error_msg}")
            render_sql_block(sr.sql)
            last_df = render_dataframe_block(sr.rows)
            st.caption(f"耗时 {sr.latency_ms:.0f} ms")

    render_chart_block(last_df, key="p2")
    render_insight_block(report.final_answer, title="最终报告")
    st.caption(f"replan {report.replan_count} 次 | 总耗时 {report.total_latency_ms:.0f} ms")
