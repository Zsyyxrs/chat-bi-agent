"""Tests for fact_anchor module: _compute_change pure logic."""

import pytest

from chat_bi_agent.agents.p3.fact_anchor import _compute_change, run_fact_anchor
from tests.p3.conftest import FakeP1Agent, FakeP1Result


def test_compute_change_down():
    cur, prior, pct, direction = _compute_change(current=100.0, prior=110.0)
    assert cur == 100.0
    assert prior == 110.0
    assert pct == pytest.approx(-9.0909, rel=1e-3)
    assert direction == "down"


def test_compute_change_up():
    cur, prior, pct, direction = _compute_change(current=120.0, prior=100.0)
    assert pct == pytest.approx(20.0)
    assert direction == "up"


def test_compute_change_flat_under_threshold():
    # default flat band is +/- 0.5%
    cur, prior, pct, direction = _compute_change(current=100.2, prior=100.0)
    assert direction == "flat"


def test_compute_change_no_prior():
    cur, prior, pct, direction = _compute_change(current=100.0, prior=None)
    assert prior is None
    assert pct is None
    assert direction == "flat"  # no comparison possible


def test_compute_change_zero_prior():
    cur, prior, pct, direction = _compute_change(current=100.0, prior=0.0)
    # division-by-zero guard: pct=None, but direction=up (current > 0)
    assert pct is None
    assert direction == "up"


def test_run_fact_anchor_happy():
    p1 = FakeP1Agent(
        responses={
            "q001": FakeP1Result(
                question_id="q001",
                sql="SELECT SUM(balance) FROM t WHERE date BETWEEN '2026-05-01' AND '2026-05-20'",
                rows=[{"current_balance": 92.0, "prior_balance": 100.0}],
            )
        }
    )
    anchor = run_fact_anchor(
        question_id="q001",
        question="why dropped?",
        p1_agent=p1,
    )
    assert anchor is not None
    assert anchor.current_value == 92.0
    assert anchor.prior_value == 100.0
    assert anchor.direction == "down"
    assert anchor.change_pct is not None and anchor.change_pct < 0
    assert "2026-05-01" in anchor.time_window
    assert anchor.rows == [{"current_balance": 92.0, "prior_balance": 100.0}]


def test_run_fact_anchor_p1_failure_returns_none():
    p1 = FakeP1Agent(
        responses={
            "q002": FakeP1Result(
                question_id="q002",
                sql=None,
                rows=None,
                execution_error="syntax error",
                error_class="INVALID_JSON",
            )
        }
    )
    anchor = run_fact_anchor(
        question_id="q002",
        question="why?",
        p1_agent=p1,
    )
    assert anchor is None


def test_run_fact_anchor_no_prior_column():
    p1 = FakeP1Agent(
        responses={
            "q003": FakeP1Result(
                question_id="q003",
                sql="SELECT SUM(balance) FROM t WHERE date BETWEEN '2026-05-01' AND '2026-05-20'",
                rows=[{"balance": 92.0}],
            )
        }
    )
    anchor = run_fact_anchor(question_id="q003", question="why?", p1_agent=p1)
    assert anchor is not None
    assert anchor.current_value == 92.0
    assert anchor.prior_value is None
    assert anchor.direction == "flat"
    assert anchor.change_pct is None
