"""Tests for transaction_generator forced account injection."""

from datetime import date

from chat_bi_agent.data.scenario_anchor import ForcedTxnSpec
from chat_bi_agent.data.transaction_generator import TransactionGenerator


def test_force_account_ids_in_balance_daily():
    gen = TransactionGenerator(seed=42)
    rows = list(
        gen.generate_balance_daily(
            account_ids=["A_RAND_1", "A_RAND_2", "A_RAND_3"],
            customer_ids=["C1", "C2"],
            product_ids=["P1"],
            branch_ids=["BR_X"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            force_account_ids=["A_ANCHOR_X"],
        )
    )
    anchor_rows = [r for r in rows if r["account_id"] == "A_ANCHOR_X"]
    # 3 days × 1 forced account = 3 daily rows
    assert len(anchor_rows) == 3
    assert {r["dt"] for r in anchor_rows} == {date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)}


def test_force_specs_in_transactions():
    gen = TransactionGenerator(seed=42)
    spec = ForcedTxnSpec(
        event_id="evt",
        account_ids=["A_ANCHOR_W"],
        txn_type="WITHDRAW",
        channels=["ATM"],
        min_txn_per_customer=3,
        event_date=date(2026, 5, 15),
    )
    rows = list(
        gen.generate_transactions(
            account_ids=["A_RAND_1"],
            customer_ids=["C1"],
            product_ids=["P1"],
            branch_ids=["BR_X"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 31),
            transactions_per_account_per_month=0.0,
            force_specs=[spec],
        )
    )
    anchor_txns = [r for r in rows if r["account_id"] == "A_ANCHOR_W"]
    assert len(anchor_txns) >= 3
    assert all(r["transaction_type"] == "WITHDRAW" for r in anchor_txns)
    assert all(r["transaction_channel"] == "ATM" for r in anchor_txns)
    assert all(date(2026, 5, 10) <= r["dt"] <= date(2026, 5, 25) for r in anchor_txns)
