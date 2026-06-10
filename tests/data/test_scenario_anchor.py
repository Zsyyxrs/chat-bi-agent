"""Tests for scenario_anchor helpers."""

from chat_bi_agent.data.scenario_anchor import (
    filter_customers,
    has_holding,
    pick_product_by_subcategory,
    pick_product_by_category,
)


def _cust(cid, branch_id="BR_X", tier="MASS"):
    return {"customer_id": cid, "branch_id": branch_id, "customer_tier": tier}


def _acct(cid, product_id, account_id=None):
    return {
        "account_id": account_id or f"AC_{cid}_{product_id}",
        "customer_id": cid,
        "product_id": product_id,
    }


def test_filter_customers_by_branch():
    customers = [_cust("C1", "BR_A"), _cust("C2", "BR_B"), _cust("C3", "BR_A")]
    result = filter_customers(customers, branches=["BR_A"])
    assert {c["customer_id"] for c in result} == {"C1", "C3"}


def test_filter_customers_by_tier():
    customers = [_cust("C1", tier="MASS"), _cust("C2", tier="HIGH_NET_WORTH")]
    result = filter_customers(customers, tiers=["HIGH_NET_WORTH"])
    assert [c["customer_id"] for c in result] == ["C2"]


def test_filter_customers_by_branch_level():
    customers = [_cust("C1", "BR_A"), _cust("C2", "BR_B")]
    branch_index = {"BR_A": {"branch_level": "CITY"}, "BR_B": {"branch_level": "SUBBRANCH"}}
    result = filter_customers(customers, branch_levels=["SUBBRANCH"], branch_index=branch_index)
    assert [c["customer_id"] for c in result] == ["C2"]


def test_filter_customers_AND_branch_and_tier():
    customers = [
        _cust("C1", "BR_A", "MASS"),
        _cust("C2", "BR_A", "HIGH_NET_WORTH"),
        _cust("C3", "BR_B", "HIGH_NET_WORTH"),
    ]
    result = filter_customers(customers, branches=["BR_A"], tiers=["HIGH_NET_WORTH"])
    assert [c["customer_id"] for c in result] == ["C2"]


def test_has_holding_by_product_subcategory():
    accounts = [_acct("C1", "P_DEMAND"), _acct("C1", "P_TIME")]
    product_index = {
        "P_DEMAND": {"product_subcategory": "活期存款"},
        "P_TIME": {"product_subcategory": "定期存款"},
    }
    assert has_holding(_cust("C1"), accounts, {"product_subcategory": "活期存款"}, product_index) is True
    assert has_holding(_cust("C1"), accounts, {"product_subcategory": "理财"}, product_index) is False


def test_has_holding_by_product_ids():
    accounts = [_acct("C1", "PROD_WEA_0000")]
    assert has_holding(_cust("C1"), accounts, {"product_ids": ["PROD_WEA_0000"]}, product_index={}) is True
    assert has_holding(_cust("C1"), accounts, {"product_ids": ["PROD_WEA_9999"]}, product_index={}) is False


def test_has_holding_by_product_category():
    accounts = [_acct("C1", "P_LOAN")]
    product_index = {"P_LOAN": {"product_category": "LOAN"}}
    assert has_holding(_cust("C1"), accounts, {"product_category": "LOAN"}, product_index) is True
    assert has_holding(_cust("C1"), accounts, {"product_category": "DEPOSIT"}, product_index) is False


def test_pick_product_by_subcategory_deterministic():
    product_index = {
        "P_A": {"product_id": "P_A", "product_subcategory": "活期存款"},
        "P_B": {"product_id": "P_B", "product_subcategory": "活期存款"},
        "P_C": {"product_id": "P_C", "product_subcategory": "定期存款"},
    }
    p1 = pick_product_by_subcategory(product_index, "活期存款", hash_key="CUST_001")
    p2 = pick_product_by_subcategory(product_index, "活期存款", hash_key="CUST_001")
    assert p1 == p2
    assert p1 in {"P_A", "P_B"}


def test_pick_product_by_subcategory_no_match_raises():
    import pytest

    with pytest.raises(LookupError, match="subcategory"):
        pick_product_by_subcategory({}, "活期存款", hash_key="C")


def test_pick_product_by_category():
    product_index = {
        "P_LOAN_1": {"product_id": "P_LOAN_1", "product_category": "LOAN"},
        "P_DEP_1": {"product_id": "P_DEP_1", "product_category": "DEPOSIT"},
    }
    pid = pick_product_by_category(product_index, "LOAN", hash_key="C")
    assert pid == "P_LOAN_1"
