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


# ─────────────────────────────────────────────────────────────────────────────
# Task 7: anchor_event_populations main entry tests
# ─────────────────────────────────────────────────────────────────────────────

from datetime import date

from chat_bi_agent.data.event_loader import Event, RequiredPopulation
from chat_bi_agent.data.scenario_anchor import (
    AnchorReport,
    ForcedTxnSpec,
    anchor_event_populations,
)


class _FakeCursor:
    """In-memory fake cursor that records all INSERTs for assertion."""

    def __init__(self):
        self.inserts: dict[str, list[dict]] = {
            "dim_customer": [],
            "dim_account": [],
            "fct_holding": [],
        }

    def execute(self, sql, values=None):
        if "INSERT INTO" in sql:
            table = sql.split("INSERT INTO")[1].strip().split()[0]
            cols = sql.split("(")[1].split(")")[0].split(", ")
            self.inserts.setdefault(table, []).append(dict(zip(cols, values)))


def _make_event(event_id, rp_dict):
    return Event(
        id=event_id,
        name=event_id,
        type="PRODUCT_EXPIRY",
        date=date(2026, 5, 14),
        description="t",
        required_population=RequiredPopulation.from_dict(rp_dict),
    )


def test_anchor_inserts_when_deficit():
    event = _make_event(
        "evt1",
        {
            "min_customers": 5,
            "branches": ["BR_X"],
            "tiers": ["HIGH_NET_WORTH"],
            "must_hold": [{"product_subcategory": "活期存款"}],
        },
    )
    cur = _FakeCursor()
    product_index = {"P_DEMAND": {"product_id": "P_DEMAND", "product_subcategory": "活期存款"}}
    report = anchor_event_populations(
        cursor=cur,
        events=[event],
        existing_customers=[],
        existing_accounts=[],
        branch_ids=["BR_X"],
        branch_index={"BR_X": {"branch_level": "CITY"}},
        product_index=product_index,
    )
    assert len(cur.inserts["dim_customer"]) == 5
    assert all(c["customer_tier"] == "HIGH_NET_WORTH" for c in cur.inserts["dim_customer"])
    assert all(c["branch_id"] == "BR_X" for c in cur.inserts["dim_customer"])
    assert all(c["is_event_anchor"] is True for c in cur.inserts["dim_customer"])
    assert all(c["customer_name"].startswith("_anchor_evt1_") for c in cur.inserts["dim_customer"])
    assert len(cur.inserts["dim_account"]) == 5
    assert report.entries[0].event_id == "evt1"
    assert report.entries[0].anchored == 5
    assert report.entries[0].deficit == 5


def test_anchor_skips_when_sufficient():
    event = _make_event(
        "evt2",
        {
            "min_customers": 2,
            "branches": ["BR_X"],
            "tiers": ["MASS"],
            "must_hold": [{"product_subcategory": "活期存款"}],
        },
    )
    existing_customers = [
        {"customer_id": "C1", "branch_id": "BR_X", "customer_tier": "MASS"},
        {"customer_id": "C2", "branch_id": "BR_X", "customer_tier": "MASS"},
        {"customer_id": "C3", "branch_id": "BR_X", "customer_tier": "MASS"},
    ]
    existing_accounts = [
        {"account_id": "A1", "customer_id": "C1", "product_id": "P_DEMAND"},
        {"account_id": "A2", "customer_id": "C2", "product_id": "P_DEMAND"},
        {"account_id": "A3", "customer_id": "C3", "product_id": "P_DEMAND"},
    ]
    product_index = {"P_DEMAND": {"product_id": "P_DEMAND", "product_subcategory": "活期存款"}}
    cur = _FakeCursor()
    report = anchor_event_populations(
        cursor=cur,
        events=[event],
        existing_customers=existing_customers,
        existing_accounts=existing_accounts,
        branch_ids=["BR_X"],
        branch_index={"BR_X": {"branch_level": "CITY"}},
        product_index=product_index,
    )
    assert cur.inserts["dim_customer"] == []
    assert report.entries[0].anchored == 0
    assert report.entries[0].deficit == 0


def test_anchor_must_hold_AND_multiple_specs():
    event = _make_event(
        "evt3",
        {
            "min_customers": 3,
            "branches": ["BR_X"],
            "tiers": ["AFFLUENT"],
            "must_hold": [
                {"product_subcategory": "活期存款"},
                {"product_ids": ["PROD_WEA_0000"]},
            ],
        },
    )
    cur = _FakeCursor()
    product_index = {
        "P_DEMAND": {"product_id": "P_DEMAND", "product_subcategory": "活期存款"},
        "PROD_WEA_0000": {"product_id": "PROD_WEA_0000", "product_subcategory": "短期理财"},
    }
    anchor_event_populations(
        cursor=cur,
        events=[event],
        existing_customers=[],
        existing_accounts=[],
        branch_ids=["BR_X"],
        branch_index={"BR_X": {"branch_level": "CITY"}},
        product_index=product_index,
    )
    # 3 customers × (1 demand account + 1 wealth account) = 6 accounts
    assert len(cur.inserts["dim_account"]) == 6
    # 3 customers × 1 wealth holding = 3 holdings
    assert len(cur.inserts["fct_holding"]) == 3


def test_anchor_must_have_transactions_returns_forced_specs():
    event = _make_event(
        "evt4",
        {
            "min_customers": 2,
            "tiers": ["BASIC"],
            "must_hold": [{"product_subcategory": "活期存款"}],
            "must_have_transactions": {
                "type": "WITHDRAW",
                "channels": ["ATM"],
                "min_txn_per_customer": 3,
            },
        },
    )
    cur = _FakeCursor()
    product_index = {"P_DEMAND": {"product_id": "P_DEMAND", "product_subcategory": "活期存款"}}
    report = anchor_event_populations(
        cursor=cur,
        events=[event],
        existing_customers=[],
        existing_accounts=[],
        branch_ids=["BR_X"],
        branch_index={"BR_X": {"branch_level": "CITY"}},
        product_index=product_index,
    )
    forced = report.forced_specs
    assert len(forced) == 1
    spec = forced[0]
    assert spec.event_id == "evt4"
    assert spec.txn_type == "WITHDRAW"
    assert spec.channels == ["ATM"]
    assert spec.min_txn_per_customer == 3
    assert len(spec.account_ids) == 2
