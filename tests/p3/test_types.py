"""Tests for p3.types dataclasses and RCAReport.to_eval_input mapping."""

from chat_bi_agent.agents.p3.types import (
    DrillRequest,
    DrillResult,
    FactAnchor,
    MatchedEvent,
    RCAReport,
)


def _sample_fact_anchor() -> FactAnchor:
    return FactAnchor(
        metric_name="retail_deposit_balance",
        time_window="2026-05-01 to 2026-05-20",
        current_value=100.0,
        prior_value=110.0,
        change_pct=-9.09,
        direction="down",
        sql="SELECT SUM(balance) ... WHERE date BETWEEN '2026-05-01' AND '2026-05-20'",
        rows=[{"balance": 100.0}],
    )


def test_drill_request_roundtrip():
    req = DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解 X")
    assert req.dimension == "branch_id"
    assert req.nl_question == "按 branch_id 拆解 X"


def test_drill_result_defaults():
    res = DrillResult(
        dimension="branch_id",
        nl_question="按 branch_id 拆解 X",
        sql="SELECT branch_id, ...",
        rows=[{"branch_id": "BR_CITY_0006", "value": 80.0}],
        pareto_top_k=[{"key": "BR_CITY_0006", "value": 80.0, "share": 0.8, "cum_share": 0.8}],
    )
    assert res.error_class is None
    assert res.skipped is False


def test_matched_event_basic():
    ev = MatchedEvent(
        event_id="anxin_90_expire",
        event_name="安鑫90天理财到期",
        effective_date="2026-05-14",
        relevance="event date 2026-05-14 within question window 2026-05-01..2026-05-20",
    )
    assert ev.event_id == "anxin_90_expire"


def test_rca_report_to_eval_input_basic():
    fa = _sample_fact_anchor()
    drill = DrillResult(
        dimension="branch_id",
        nl_question="按 branch_id 拆解",
        sql="SELECT ...",
        rows=[{"branch_id": "BR_CITY_0006", "v": 80.0}],
        pareto_top_k=[{"key": "BR_CITY_0006", "value": 80.0, "share": 0.8, "cum_share": 0.8}],
    )
    event = MatchedEvent(
        event_id="anxin_90_expire",
        event_name="安鑫90天理财到期",
        effective_date="2026-05-14",
        relevance="overlap",
    )
    report = RCAReport(
        question_id="attribution_q001",
        question="上海分行高净值客户为什么...?",
        fact_anchor=fa,
        drill_results=[drill],
        matched_events=[event],
        narrative="上海分行 BR_CITY_0006 的高净值客户...安鑫 90 天理财到期...",
        conclusion="根因是安鑫 90 天理财到期。",
        trace_id="trace-abc",
        latency_ms=5000,
    )
    eval_input = report.to_eval_input()
    assert eval_input["question_id"] == "attribution_q001"
    assert "BR_CITY_0006" in eval_input["agent_response"]
    # agent_conclusion 现在传 narrative（保持 runner ↔ rejudge 评分输入一致），
    # 而非简短的 conclusion 字段——judge prompt 的 quant/mech/scope 机械化规则需要细节。
    assert eval_input["agent_conclusion"] == report.narrative
    assert eval_input["agent_identified_event"] == "anxin_90_expire"
    assert "branch_id" in eval_input["agent_extracted_dimensions"]
    assert "BR_CITY_0006" in eval_input["agent_extracted_dimensions"]["branch_id"]


def test_rca_report_to_eval_input_no_events():
    fa = _sample_fact_anchor()
    report = RCAReport(
        question_id="q1",
        question="why?",
        fact_anchor=fa,
        drill_results=[],
        matched_events=[],
        narrative="no clear cause identified",
        trace_id=None,
        latency_ms=100,
    )
    eval_input = report.to_eval_input()
    assert eval_input["agent_identified_event"] is None
    assert eval_input["agent_extracted_dimensions"] == {}
    # agent_conclusion 现在传 narrative，no-events 用例的 narrative 是 "no clear cause identified"
    assert eval_input["agent_conclusion"] == report.narrative
