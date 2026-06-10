"""Tests for PropagationEngine effect_type=sustained logic (Bug C fix)."""

from datetime import date, timedelta

import pytest

from chat_bi_agent.data.propagation_engine import PropagationEngine, PropagationRule


def _rule(effect_type="transient", ramp_days=3, delay_days=1, delta=-10.0):
    return PropagationRule(
        target_table="fct_balance_daily",
        target_column="balance",
        metric_name="m",
        delta=delta,
        delay_days=delay_days,
        ramp_days=ramp_days,
        affected_account_sample=1.0,
        effect_type=effect_type,
    )


def _row():
    return {"branch_id": "B", "customer_id": "C", "product_id": "P", "balance": 100.0}


def test_transient_skips_after_ramp_end():
    engine = PropagationEngine(seed=42)
    rule = _rule(effect_type="transient", delay_days=1, ramp_days=3)
    event_date = date(2026, 5, 14)
    # delay 1 + ramp 3 → ramp ends day +4 (5/18). Day +5 (5/19) → skip.
    assert engine.should_apply_rule(rule, _row(), event_date, event_date + timedelta(days=5)) is False


def test_sustained_applies_after_ramp_end():
    engine = PropagationEngine(seed=42)
    rule = _rule(effect_type="sustained", delay_days=1, ramp_days=3)
    event_date = date(2026, 5, 14)
    # Day +10 → still apply with multiplier 1.0
    assert engine.should_apply_rule(rule, _row(), event_date, event_date + timedelta(days=10)) is True


def test_sustained_multiplier_is_1_after_ramp():
    engine = PropagationEngine(seed=42)
    rule = _rule(effect_type="sustained", ramp_days=3)
    assert engine.compute_delta_multiplier(rule, days_since_start=10) == 1.0


def test_sustained_multiplier_ramps_linearly():
    engine = PropagationEngine(seed=42)
    rule = _rule(effect_type="sustained", ramp_days=4)
    assert engine.compute_delta_multiplier(rule, days_since_start=0) == 0.0
    assert engine.compute_delta_multiplier(rule, days_since_start=1) == 0.25
    assert engine.compute_delta_multiplier(rule, days_since_start=2) == 0.5
    assert engine.compute_delta_multiplier(rule, days_since_start=4) == 1.0
    assert engine.compute_delta_multiplier(rule, days_since_start=10) == 1.0


def test_transient_still_works_for_old_callers():
    """Default effect_type must be transient — backward compat."""
    rule = PropagationRule(
        target_table="fct_balance_daily",
        target_column="balance",
        metric_name="m",
        delta=-10.0,
        delay_days=0,
        ramp_days=3,
    )
    assert rule.effect_type == "transient"


def test_apply_rule_to_row_sustained_e2e():
    """End-to-end: sustained rule on day after ramp end still reduces balance."""
    engine = PropagationEngine(seed=42)
    rule = _rule(effect_type="sustained", delay_days=1, ramp_days=3, delta=-10.0)
    row = _row()
    engine.apply_rule_to_row(
        rule, row, event_date=date(2026, 5, 14), current_date=date(2026, 5, 24)
    )
    # ramp完成（day 10 ≥ ramp_days=3）→ multiplier=1.0 → balance × (1 - 0.1) = 90
    assert row["balance"] == pytest.approx(90.0)
    assert "_propagations" in row
