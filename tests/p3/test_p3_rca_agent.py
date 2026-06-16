"""Tests for P3RootCauseAnalysisAgent orchestrator."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from chat_bi_agent.agents.p3.p3_rca_agent import P3RootCauseAnalysisAgent
from tests.p3.conftest import FakeP1Agent, FakeP1Result


def _good_fact_anchor_p1(qid: str) -> FakeP1Result:
    sql = (
        "SELECT SUM(balance), prior_balance FROM t WHERE date BETWEEN '2026-05-01' AND '2026-05-20'"
    )
    return FakeP1Result(
        question_id=qid,
        sql=sql,
        rows=[{"balance": 92.0, "prior_balance": 100.0}],
    )


def _good_drill_p1(qid: str, dim: str, top_key: str) -> FakeP1Result:
    return FakeP1Result(
        question_id=qid,
        sql=f"SELECT {dim}, SUM(balance) FROM t GROUP BY {dim}",
        rows=[
            {dim: top_key, "balance": 80.0},
            {dim: f"{top_key}_OTHER", "balance": 20.0},
        ],
    )


def _content(s: str) -> SimpleNamespace:
    return SimpleNamespace(content=s)


def test_happy_path_returns_full_report(fake_events_dir: Path):
    p1 = FakeP1Agent(
        responses={
            "q001": _good_fact_anchor_p1("q001"),
            "q001__drill_0": _good_drill_p1("q001__drill_0", "branch_id", "BR_CITY_0006"),
            "q001__drill_1": _good_drill_p1("q001__drill_1", "customer_tier", "HIGH_NET_WORTH"),
        }
    )
    llm = MagicMock()
    llm.chat.side_effect = [
        _content(
            '{"sub_questions": ['
            '{"dimension": "branch_id", "nl_question": "按 branch_id 拆解"},'
            '{"dimension": "customer_tier", "nl_question": "按 customer_tier 拆解"}'
            "]}"
        ),
        _content(
            "【叙述】\nnarrative including BR_CITY_0006 HIGH_NET_WORTH 安鑫 anxin_90_expire\n"
            "【结论】\n根因为安鑫 90 天理财到期。"
        ),
    ]
    agent = P3RootCauseAnalysisAgent(p1_agent=p1, llm_client=llm, events_dir=fake_events_dir)
    report = agent.run("q001", "上海分行高净值客户为什么...?")

    assert report.error is None
    assert report.fact_anchor is not None
    assert report.fact_anchor.current_value == 92.0
    assert len(report.drill_results) == 2
    assert any(ev.event_id == "anxin_90_expire" for ev in report.matched_events)
    assert "BR_CITY_0006" in report.narrative
    assert "安鑫" in report.conclusion


def test_fact_anchor_failure_returns_error_report(fake_events_dir: Path):
    p1 = FakeP1Agent(
        responses={
            "q001": FakeP1Result(
                question_id="q001",
                sql=None,
                rows=None,
                execution_error="schema error",
                error_class="VALIDATOR_FAIL",
            )
        }
    )
    llm = MagicMock()
    agent = P3RootCauseAnalysisAgent(p1_agent=p1, llm_client=llm, events_dir=fake_events_dir)
    report = agent.run("q001", "why?")

    assert report.fact_anchor is None
    assert report.error is not None
    assert "fact_anchor" in report.error.lower() or "anchor" in report.error.lower()
    assert llm.chat.call_count == 0  # never reached drilldown_selector

    # R2: graceful fallback — narrative is now stub'd with matched event names so
    # downstream RCAEvaluator's fuzzy event_hit can still fire. matched_events
    # comes from event_matcher's "no date window" fallback path = all events.
    assert report.matched_events, "fallback should still surface candidate events"
    assert any(ev.event_name in report.narrative for ev in report.matched_events)
    assert "查询失败" in report.narrative


def test_drilldown_selector_failure_uses_fallback(fake_events_dir: Path):
    p1 = FakeP1Agent(
        responses={
            "q001": _good_fact_anchor_p1("q001"),
            # Fallback uses DEFAULT_DIMS[:2] = ["branch_id", "sub_branch_id"]
            "q001__drill_0": _good_drill_p1("q001__drill_0", "branch_id", "BR_CITY_0006"),
            "q001__drill_1": _good_drill_p1("q001__drill_1", "sub_branch_id", "SUB_001"),
        }
    )
    llm = MagicMock()
    llm.chat.side_effect = [
        _content("not json"),  # selector fails → fallback
        _content("narrative including BR_CITY_0006"),  # synthesizer succeeds
    ]
    agent = P3RootCauseAnalysisAgent(p1_agent=p1, llm_client=llm, events_dir=fake_events_dir)
    report = agent.run("q001", "why?")
    assert report.fact_anchor is not None
    assert len(report.drill_results) == 2
    dims = {dr.dimension for dr in report.drill_results}
    assert "branch_id" in dims


def test_all_drills_skipped_still_synthesizes(fake_events_dir: Path):
    p1 = FakeP1Agent(
        responses={
            "q001": _good_fact_anchor_p1("q001"),
            "q001__drill_0": FakeP1Result(
                question_id="q001__drill_0",
                sql=None,
                rows=None,
                execution_error="timeout",
                error_class="TIMEOUT",
            ),
            "q001__drill_1": FakeP1Result(
                question_id="q001__drill_1",
                sql=None,
                rows=None,
                execution_error="timeout",
                error_class="TIMEOUT",
            ),
        }
    )
    llm = MagicMock()
    llm.chat.side_effect = [
        _content(
            '{"sub_questions": ['
            '{"dimension": "branch_id", "nl_question": "x"},'
            '{"dimension": "customer_tier", "nl_question": "y"}'
            "]}"
        ),
        _content("narrative based on metric only"),
    ]
    agent = P3RootCauseAnalysisAgent(p1_agent=p1, llm_client=llm, events_dir=fake_events_dir)
    report = agent.run("q001", "why?")
    assert all(dr.skipped for dr in report.drill_results)
    assert report.narrative
