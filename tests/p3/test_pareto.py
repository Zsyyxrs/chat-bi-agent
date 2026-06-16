"""Tests for _compute_pareto (pure pandas algorithm, no LLM)."""

import pytest

from chat_bi_agent.agents.p3.drill_executor import _compute_pareto, _infer_value_col


def test_pareto_simple_three_items_threshold():
    rows = [
        {"branch_id": "B1", "v": 80.0},
        {"branch_id": "B2", "v": 15.0},
        {"branch_id": "B3", "v": 5.0},
    ]
    out = _compute_pareto(rows, value_col="v", threshold=0.6)
    # B1 alone is 80% >= 60% → truncates at 1
    assert len(out) == 1
    assert out[0]["key"] == "B1"
    assert out[0]["value"] == 80.0
    assert out[0]["share"] == pytest.approx(0.8)
    assert out[0]["cum_share"] == pytest.approx(0.8)


def test_pareto_top3_cap():
    # 5 equal items, each 20% — threshold never reached, cap at top-3
    rows = [{"k": f"K{i}", "v": 20.0} for i in range(5)]
    out = _compute_pareto(rows, value_col="v", threshold=0.6)
    assert len(out) == 3
    assert all(item["share"] == pytest.approx(0.2) for item in out)


def test_pareto_sorted_descending():
    rows = [
        {"k": "C", "v": 10.0},
        {"k": "A", "v": 50.0},
        {"k": "B", "v": 40.0},
    ]
    out = _compute_pareto(rows, value_col="v", threshold=0.9)
    keys = [item["key"] for item in out]
    assert keys == ["A", "B"]  # A=50 cum=50%, then B=40 cum=90% >= 90% → stop


def test_pareto_empty_rows():
    assert _compute_pareto([], value_col="v", threshold=0.6) == []


def test_pareto_zero_total():
    rows = [{"k": "A", "v": 0.0}, {"k": "B", "v": 0.0}]
    out = _compute_pareto(rows, value_col="v", threshold=0.6)
    # share denominator is 0 — must not raise; return [] (no contribution)
    assert out == []


def test_pareto_negative_values_use_absolute():
    # Drops (negative changes) — sort by magnitude
    rows = [
        {"k": "A", "v": -80.0},
        {"k": "B", "v": -20.0},
    ]
    out = _compute_pareto(rows, value_col="v", threshold=0.6)
    assert out[0]["key"] == "A"
    assert out[0]["value"] == -80.0
    assert out[0]["share"] == pytest.approx(0.8)


def test_infer_value_col_finds_first_numeric():
    rows = [{"branch_id": "B1", "name": "x", "balance": 100.0}]
    assert _infer_value_col(rows, dim_hint="branch_id") == "balance"


def test_infer_value_col_skips_dim_hint():
    rows = [{"product_id": "P1", "amount": 50.0, "count": 3}]
    # product_id is dim_hint → first numeric col is "amount"
    assert _infer_value_col(rows, dim_hint="product_id") == "amount"


def test_infer_value_col_no_numeric_raises():
    rows = [{"a": "x", "b": "y"}]
    with pytest.raises(ValueError):
        _infer_value_col(rows, dim_hint="a")
