"""Tests for fact_anchor module: _compute_change pure logic."""

from decimal import Decimal

import pytest

from chat_bi_agent.agents.p3.fact_anchor import (
    _compute_change,
    _extract_current_prior,
    run_fact_anchor,
)
from tests.p3.conftest import FakeP1Agent, FakeP1Result


def test_extract_current_prior_pair_match_same_suffix():
    # q007 历史 bug：P1 CROSS JOIN 多指标后，4 列里 cur 和 prior 是不同指标，
    # 旧逻辑 cross-metric 错配出 -100%。新逻辑必须按 current_<suffix> ↔
    # prior_<suffix> 配对，取首个完整对。
    row = {
        "current_deposit_balance": Decimal("99300"),
        "prior_deposit_balance": Decimal("99100"),
        "current_aum": Decimal("12000000000"),
        "prior_aum": Decimal("11860000000"),
    }
    cur, prior = _extract_current_prior([row])
    assert cur == 99300.0
    assert prior == 99100.0


def test_extract_current_prior_legacy_loose_match():
    # 单对 PoP SQL（没 current_ 前缀）走旧松匹配
    row = {"avg_balance": 100.0, "prior_avg_balance": 110.0}
    cur, prior = _extract_current_prior([row])
    assert cur == 100.0
    assert prior == 110.0


def test_extract_current_prior_no_overwrite_legacy_bug():
    # 历史 bug：loose match 路径里多个 prior-like 列会被后面的覆盖
    row = {"v_cur": 100.0, "v_prior": 50.0, "v_lastyear": 999.0}
    cur, prior = _extract_current_prior([row])
    assert cur == 100.0
    assert prior == 50.0  # 不能被 v_lastyear=999 覆盖


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


def test_run_fact_anchor_augments_pop_question_with_dual_window_constraint():
    # fact_anchor 对 PoP 题（含"为什么/上升/下降/变化"等关键词）必须拼双窗口指令，
    # 否则 P1 可能写单窗口 SQL，导致 _extract_current_prior 拿到 prior=None。
    p1 = FakeP1Agent(
        responses={
            "q_aug": FakeP1Result(
                question_id="q_aug",
                sql="SELECT current_balance, prior_balance FROM t",
                rows=[{"current_balance": 92.0, "prior_balance": 100.0}],
            )
        }
    )
    run_fact_anchor(question_id="q_aug", question="8月余额为什么涨了12%", p1_agent=p1)
    assert len(p1.calls) == 1
    sent_qid, sent_question = p1.calls[0]
    assert sent_qid == "q_aug"
    assert "8月余额为什么涨了12%" in sent_question
    assert "current_<metric>" in sent_question  # 双窗口指令在
    assert "prior_<metric>" in sent_question


def test_run_fact_anchor_accepts_decimal_rows():
    # psycopg2 RealDictCursor returns NUMERIC/DECIMAL columns as Decimal —
    # fact_anchor must treat them as numeric, not skip them.
    p1 = FakeP1Agent(
        responses={
            "q_dec": FakeP1Result(
                question_id="q_dec",
                sql=(
                    "SELECT current_balance, prior_balance FROM t "
                    "WHERE dt BETWEEN '2026-05-01' AND '2026-05-20'"
                ),
                rows=[{"current_balance": Decimal("92.0"), "prior_balance": Decimal("100.0")}],
            )
        }
    )
    anchor = run_fact_anchor(question_id="q_dec", question="why?", p1_agent=p1)
    assert anchor is not None
    assert anchor.current_value == 92.0
    assert anchor.prior_value == 100.0
    assert anchor.direction == "down"


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
