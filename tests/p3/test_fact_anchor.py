"""Tests for fact_anchor module: _compute_change pure logic."""
import pytest

from chat_bi_agent.agents.p3.fact_anchor import _compute_change


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
