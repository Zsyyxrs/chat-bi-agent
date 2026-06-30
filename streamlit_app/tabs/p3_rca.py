"""Tab P3: 根因分析（Fact Anchor + Drilldown + 叙事）。"""

import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.p3 import P3RootCauseAnalysisAgent
from chat_bi_agent.llm import qwen_client
from streamlit_app.components.chart_block import render_chart_block
from streamlit_app.components.dataframe_block import render_dataframe_block
from streamlit_app.components.insight_block import render_insight_block
from streamlit_app.components.sql_block import render_sql_block

_SESSION_KEY = "p3_last_result"
_AGENT_KEY = "p3_agent"

_EVENTS_DIR = Path(__file__).resolve().parents[2] / "src" / "chat_bi_agent" / "data" / "events"


def _get_agent() -> P3RootCauseAnalysisAgent:
    if _AGENT_KEY not in st.session_state:
        p1 = P1NL2SQLAgent(top_k=4)
        st.session_state[_AGENT_KEY] = P3RootCauseAnalysisAgent(
            p1_agent=p1,
            llm_client=qwen_client,
            events_dir=_EVENTS_DIR,
        )
    return st.session_state[_AGENT_KEY]


def _render_fact_anchor(fact_anchor) -> None:
    st.markdown("##### Fact Anchor（事实锚点）")
    cols = st.columns(4)
    cols[0].metric("指标", fact_anchor.metric_name)
    cols[1].metric("时间窗口", fact_anchor.time_window)
    cur = fact_anchor.current_value
    prior = fact_anchor.prior_value
    cols[2].metric("本期值", f"{cur:,.2f}" if isinstance(cur, float) else str(cur))
    if prior is not None and fact_anchor.change_pct is not None:
        cols[3].metric(
            "变化幅度",
            f"{fact_anchor.change_pct * 100:.2f}%",
            delta=f"{fact_anchor.direction}",
        )
    else:
        cols[3].metric("变化幅度", "—")
    with st.expander("Fact Anchor SQL / 数据", expanded=False):
        render_sql_block(fact_anchor.sql)
        render_dataframe_block(fact_anchor.rows, title="锚点明细")


def render_p3_tab(call_counter: dict) -> None:
    st.subheader("P3：根因分析（RCA）")
    st.caption("自动识别业务指标异动 → 下钻维度 → 匹配业务事件 → 生成归因叙事。")

    question = st.text_area(
        "问题",
        height=100,
        placeholder="例：2026 年 3 月定期存款余额为什么下降？",
        key="p3_question_input",
    )

    if st.button("执行", key="p3_run_btn", type="primary"):
        if not question.strip():
            st.warning("请输入问题")
            return
        with st.spinner("P3 RCA 执行中（含 LLM 两段式归因，可能耗时 20-40s）..."):
            try:
                report = _get_agent().run(
                    question_id=f"ui_p3_{uuid.uuid4().hex[:8]}",
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

    if report.error:
        st.error(f"RCA 执行错误：{report.error}")

    if report.fact_anchor is not None:
        _render_fact_anchor(report.fact_anchor)

    st.markdown("##### 维度下钻")
    if not report.drill_results:
        st.info("（无下钻结果）")
    else:
        drill_df = None
        for dr in report.drill_results:
            with st.expander(f"下钻维度：{dr.dimension}", expanded=False):
                if dr.skipped:
                    st.info("（已跳过）")
                    continue
                if dr.error_class:
                    st.error(f"错误：{dr.error_class.value}")
                render_sql_block(dr.sql)
                drill_df = render_dataframe_block(dr.rows)
                if dr.pareto_top_k:
                    st.caption(f"Top {len(dr.pareto_top_k)} 贡献维度已识别")
        render_chart_block(drill_df, key="p3_drill")

    if report.matched_events:
        st.markdown("##### 匹配业务事件")
        events_df = pd.DataFrame(
            [
                {
                    "事件 ID": e.event_id,
                    "名称": e.event_name,
                    "生效日期": e.effective_date,
                    "相关性": e.relevance,
                }
                for e in report.matched_events
            ]
        )
        st.dataframe(events_df, use_container_width=True)

    render_insight_block(report.narrative, title="RCA 叙事")
    if report.conclusion:
        render_insight_block(report.conclusion, title="结论摘要")

    if report.trace_id:
        st.caption(f"Langfuse trace_id: `{report.trace_id}`")
    st.caption(f"总耗时 {report.latency_ms} ms")
