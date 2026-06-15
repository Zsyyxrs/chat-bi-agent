"""Tests for scenario_anchor helpers."""

from datetime import date

from chat_bi_agent.data.event_loader import Event, RequiredPopulation
from chat_bi_agent.data.scenario_anchor import (
    anchor_event_populations,
    pick_product_by_category,
    pick_product_by_subcategory,
)


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
# anchor_event_populations main entry tests
# ─────────────────────────────────────────────────────────────────────────────


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
