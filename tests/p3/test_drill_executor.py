"""Tests for run_drill_down (wraps P1NL2SQLAgent + Pareto)."""

from chat_bi_agent.agents.p3.drill_executor import run_drill_down
from chat_bi_agent.agents.p3.types import DrillRequest
from tests.p3.conftest import FakeP1Agent, FakeP1Result


def test_run_drill_down_happy():
    requests = [
        DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解"),
        DrillRequest(dimension="customer_tier", nl_question="按 customer_tier 拆解"),
    ]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, SUM(balance) FROM t GROUP BY branch_id",
                rows=[
                    {"branch_id": "BR_CITY_0006", "balance": 80.0},
                    {"branch_id": "BR_CITY_0002", "balance": 20.0},
                ],
            ),
            "qid__drill_1": FakeP1Result(
                question_id="qid__drill_1",
                sql="SELECT customer_tier, SUM(balance) FROM t GROUP BY customer_tier",
                rows=[
                    {"customer_tier": "HIGH_NET_WORTH", "balance": 81.0},
                    {"customer_tier": "AFFLUENT", "balance": 19.0},
                ],
            ),
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert len(results) == 2
    assert results[0].dimension == "branch_id"
    assert results[0].skipped is False
    assert results[0].pareto_top_k[0]["key"] == "BR_CITY_0006"
    assert results[1].pareto_top_k[0]["key"] == "HIGH_NET_WORTH"


def test_run_drill_down_p1_failure_marks_skipped():
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql=None,
                rows=None,
                execution_error="timeout",
                error_class="TIMEOUT",
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert len(results) == 1
    assert results[0].skipped is True
    assert results[0].error_class == "TIMEOUT"
    assert results[0].pareto_top_k == []


def test_run_drill_down_partial_failure_continues():
    requests = [
        DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解"),
        DrillRequest(dimension="customer_tier", nl_question="按 customer_tier 拆解"),
    ]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql=None,
                rows=None,
                execution_error="timeout",
                error_class="TIMEOUT",
            ),
            "qid__drill_1": FakeP1Result(
                question_id="qid__drill_1",
                sql="SELECT customer_tier, SUM(balance) FROM t GROUP BY customer_tier",
                rows=[{"customer_tier": "HIGH_NET_WORTH", "balance": 100.0}],
            ),
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is True
    assert results[1].skipped is False
    assert results[1].pareto_top_k[0]["key"] == "HIGH_NET_WORTH"


def test_run_drill_down_empty_rows_marks_skipped():
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, SUM(balance) FROM t GROUP BY branch_id",
                rows=[],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is True
