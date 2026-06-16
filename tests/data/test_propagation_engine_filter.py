"""Tests for PropagationEngine dim filter logic (Bug A fix)."""

from datetime import date

import pytest

from chat_bi_agent.data.propagation_engine import PropagationEngine, PropagationRule


def _balance_row(
    account_id="A0", customer_id="C0", branch_id="BR_CITY_0006", product_id="P0", balance=100.0
):
    return {
        "account_id": account_id,
        "customer_id": customer_id,
        "branch_id": branch_id,
        "product_id": product_id,
        "balance": balance,
        "dt": date(2026, 5, 20),
    }


def _base_rule(**overrides):
    base = dict(
        target_table="fct_balance_daily",
        target_column="balance",
        metric_name="m",
        delta=-10.0,
        delay_days=0,
        ramp_days=10,
        affected_account_sample=1.0,
    )
    base.update(overrides)
    return PropagationRule(**base)


def test_rule_has_new_dim_filter_fields():
    rule = _base_rule(
        branch_ids=["BR_CITY_0006"],
        customer_tiers=["HIGH_NET_WORTH"],
        branch_levels=["SUBBRANCH"],
        product_ids=["P_X"],
        product_subcategories=["活期存款"],
    )
    assert rule.branch_ids == ["BR_CITY_0006"]
    assert rule.customer_tiers == ["HIGH_NET_WORTH"]
    assert rule.branch_levels == ["SUBBRANCH"]
    assert rule.product_ids == ["P_X"]
    assert rule.product_subcategories == ["活期存款"]


def test_rule_dim_fields_default_none():
    rule = _base_rule()
    assert rule.branch_ids is None
    assert rule.customer_tiers is None
    assert rule.branch_levels is None
    assert rule.product_ids is None
    assert rule.product_subcategories is None


def test_branch_filter_matches():
    engine = PropagationEngine(seed=42)
    rule = _base_rule(branch_ids=["BR_CITY_0006"])
    row = _balance_row(branch_id="BR_CITY_0006")
    assert engine.should_apply_rule(rule, row, date(2026, 5, 14), date(2026, 5, 20)) is True


def test_branch_filter_skips():
    engine = PropagationEngine(seed=42)
    rule = _base_rule(branch_ids=["BR_CITY_0006"])
    row = _balance_row(branch_id="BR_CITY_0001")
    assert engine.should_apply_rule(rule, row, date(2026, 5, 14), date(2026, 5, 20)) is False


def test_customer_tier_filter_requires_index():
    customer_index = {
        "C_HNW": {"customer_tier": "HIGH_NET_WORTH"},
        "C_MASS": {"customer_tier": "MASS"},
    }
    engine = PropagationEngine(seed=42, customer_index=customer_index)
    rule = _base_rule(customer_tiers=["HIGH_NET_WORTH"])
    assert (
        engine.should_apply_rule(
            rule, _balance_row(customer_id="C_HNW"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is True
    )
    assert (
        engine.should_apply_rule(
            rule, _balance_row(customer_id="C_MASS"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is False
    )


def test_customer_tier_filter_without_index_raises():
    engine = PropagationEngine(seed=42)  # no customer_index
    rule = _base_rule(customer_tiers=["HIGH_NET_WORTH"])
    with pytest.raises(RuntimeError, match="customer_index"):
        engine.should_apply_rule(rule, _balance_row(), date(2026, 5, 14), date(2026, 5, 20))


def test_branch_level_filter_via_index():
    branch_index = {
        "BR_CITY_0006": {"branch_level": "CITY"},
        "BR_SUB_X": {"branch_level": "SUBBRANCH"},
    }
    engine = PropagationEngine(seed=42, branch_index=branch_index)
    rule = _base_rule(branch_levels=["SUBBRANCH"])
    assert (
        engine.should_apply_rule(
            rule, _balance_row(branch_id="BR_SUB_X"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is True
    )
    assert (
        engine.should_apply_rule(
            rule, _balance_row(branch_id="BR_CITY_0006"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is False
    )


def test_product_id_filter():
    engine = PropagationEngine(seed=42)
    rule = _base_rule(product_ids=["P_TARGET"])
    assert (
        engine.should_apply_rule(
            rule, _balance_row(product_id="P_TARGET"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is True
    )
    assert (
        engine.should_apply_rule(
            rule, _balance_row(product_id="P_OTHER"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is False
    )


def test_product_subcategory_filter_via_index():
    product_index = {
        "P_DEMAND": {"product_subcategory": "活期存款"},
        "P_TIME": {"product_subcategory": "定期存款"},
    }
    engine = PropagationEngine(seed=42, product_index=product_index)
    rule = _base_rule(product_subcategories=["活期存款"])
    assert (
        engine.should_apply_rule(
            rule, _balance_row(product_id="P_DEMAND"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is True
    )
    assert (
        engine.should_apply_rule(
            rule, _balance_row(product_id="P_TIME"), date(2026, 5, 14), date(2026, 5, 20)
        )
        is False
    )


def test_combined_filters_AND():
    customer_index = {"C_HNW_001": {"customer_tier": "HIGH_NET_WORTH"}}
    product_index = {"P_DEMAND": {"product_subcategory": "活期存款"}}
    engine = PropagationEngine(seed=42, customer_index=customer_index, product_index=product_index)
    rule = _base_rule(
        branch_ids=["BR_CITY_0006"],
        customer_tiers=["HIGH_NET_WORTH"],
        product_subcategories=["活期存款"],
    )
    matching_row = _balance_row(
        branch_id="BR_CITY_0006", customer_id="C_HNW_001", product_id="P_DEMAND"
    )
    assert (
        engine.should_apply_rule(rule, matching_row, date(2026, 5, 14), date(2026, 5, 20)) is True
    )

    # Same row but wrong branch → should skip
    wrong_branch = {**matching_row, "branch_id": "BR_CITY_0001"}
    assert (
        engine.should_apply_rule(rule, wrong_branch, date(2026, 5, 14), date(2026, 5, 20)) is False
    )
