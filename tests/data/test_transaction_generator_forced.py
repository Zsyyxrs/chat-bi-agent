"""Tests for transaction_generator forced account injection."""

from datetime import date

from chat_bi_agent.data.scenario_anchor import ForcedTxnSpec
from chat_bi_agent.data.transaction_generator import TransactionGenerator


def _acct(
    account_id: str,
    customer_id: str = "C1",
    branch_id: str = "BR_X",
    product_id: str | None = "P1",
    account_type: str = "SAVING",
    open_date: date | None = None,
    close_date: date | None = None,
) -> dict:
    return {
        "account_id": account_id,
        "customer_id": customer_id,
        "branch_id": branch_id,
        "product_id": product_id,
        "account_type": account_type,
        "open_date": open_date,
        "close_date": close_date,
    }


def test_force_account_ids_in_balance_daily():
    gen = TransactionGenerator(seed=42)
    rows = list(
        gen.generate_balance_daily(
            accounts=[_acct("A_RAND_1"), _acct("A_RAND_2"), _acct("A_RAND_3")],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 3),
            force_account_ids=["A_ANCHOR_X"],
            anchor_metadata={
                "A_ANCHOR_X": {
                    "customer_id": "CUST_A",
                    "product_id": "P_ANCH",
                    "branch_id": "BR_X",
                }
            },
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
            accounts=[_acct("A_RAND_1")],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 31),
            transactions_per_account_per_month=0.0,
            force_specs=[spec],
            anchor_metadata={
                "A_ANCHOR_W": {
                    "customer_id": "CUST_W",
                    "product_id": "P_W",
                    "branch_id": "BR_X",
                }
            },
        )
    )
    anchor_txns = [r for r in rows if r["account_id"] == "A_ANCHOR_W"]
    assert len(anchor_txns) >= 3
    assert all(r["transaction_type"] == "WITHDRAW" for r in anchor_txns)
    assert all(r["transaction_channel"] == "ATM" for r in anchor_txns)
    assert all(date(2026, 5, 10) <= r["dt"] <= date(2026, 5, 25) for r in anchor_txns)
    # 关系绑定：forced 行 customer/branch 应来自 anchor_metadata，而非随机
    assert all(r["customer_id"] == "CUST_W" for r in anchor_txns)
    assert all(r["branch_id"] == "BR_X" for r in anchor_txns)


def test_balance_daily_stops_after_account_close_date():
    """generate_balance_daily must skip days strictly after each account's close_date."""
    gen = TransactionGenerator(seed=42)
    rows = list(
        gen.generate_balance_daily(
            accounts=[_acct("A_REG_1")],
            start_date=date(2026, 5, 12),
            end_date=date(2026, 5, 17),
            force_account_ids=["A_ANCHOR_X"],
            anchor_metadata={
                "A_ANCHOR_X": {
                    "customer_id": "CUST_A",
                    "product_id": "P_ANCH",
                    "branch_id": "BR_X",
                }
            },
            account_close_dates={
                "A_ANCHOR_X": date(2026, 5, 14),
                "A_REG_1": date(2026, 5, 13),
            },
        )
    )
    anchor_rows = [r for r in rows if r["account_id"] == "A_ANCHOR_X"]
    reg_rows = [r for r in rows if r["account_id"] == "A_REG_1"]

    assert {r["dt"] for r in anchor_rows} == {
        date(2026, 5, 12),
        date(2026, 5, 13),
        date(2026, 5, 14),
    }
    assert {r["dt"] for r in reg_rows} == {date(2026, 5, 12), date(2026, 5, 13)}


def test_balance_daily_skips_expired_product_rows_for_random_accounts():
    """Non-anchor rows whose bound product expired before `current` must drop product_id."""
    gen = TransactionGenerator(seed=42)
    # 把 200 个账户分成两批：一半挂 WEA_0030，一半挂 WEA_0031；两者都会过期。
    accounts = []
    for i in range(100):
        accounts.append(_acct(f"A_WEA30_{i}", product_id="PROD_WEA_0030"))
    for i in range(100):
        accounts.append(_acct(f"A_WEA31_{i}", product_id="PROD_WEA_0031"))
    rows = list(
        gen.generate_balance_daily(
            accounts=accounts,
            start_date=date(2026, 5, 12),
            end_date=date(2026, 5, 20),
            product_expiry_dates={
                "PROD_WEA_0030": date(2026, 5, 14),
                "PROD_WEA_0031": date(2026, 5, 14),
            },
        )
    )
    bad = [
        r
        for r in rows
        if r["product_id"] in {"PROD_WEA_0030", "PROD_WEA_0031"} and r["dt"] > date(2026, 5, 14)
    ]
    assert bad == [], f"expired products leaked into post-expiry rows: {bad[:3]}"
    # 过期前仍可见
    pre = [r for r in rows if r["product_id"] in {"PROD_WEA_0030", "PROD_WEA_0031"}]
    assert all(r["dt"] <= date(2026, 5, 14) for r in pre)


def test_balance_daily_no_close_dates_preserves_existing_behavior():
    """When account_close_dates is None, all days are emitted."""
    gen = TransactionGenerator(seed=42)
    rows = list(
        gen.generate_balance_daily(
            accounts=[_acct("A_REG_1")],
            start_date=date(2026, 5, 12),
            end_date=date(2026, 5, 14),
            force_account_ids=["A_ANCHOR_X"],
            anchor_metadata={
                "A_ANCHOR_X": {
                    "customer_id": "CUST_A",
                    "product_id": "P_ANCH",
                    "branch_id": "BR_X",
                }
            },
        )
    )
    assert len({r["dt"] for r in rows if r["account_id"] == "A_ANCHOR_X"}) == 3


def test_generate_holdings_excludes_expired_products():
    """When excluded_product_ids is set, generated rows must not reference those products."""
    gen = TransactionGenerator(seed=42)
    # 200 accounts: 100 on the expired WEA products, 100 on a valid FUN product.
    accounts = []
    for i in range(50):
        accounts.append(
            _acct(
                f"A_WEA30_{i}",
                customer_id=f"C_W30_{i}",
                product_id="PROD_WEA_0030",
                account_type="INVESTMENT",
            )
        )
        accounts.append(
            _acct(
                f"A_WEA31_{i}",
                customer_id=f"C_W31_{i}",
                product_id="PROD_WEA_0031",
                account_type="INVESTMENT",
            )
        )
    for i in range(100):
        accounts.append(
            _acct(
                f"A_FUN_{i}",
                customer_id=f"C_F_{i}",
                product_id="PROD_FUN_0001",
                account_type="INVESTMENT",
            )
        )
    rows = list(
        gen.generate_holdings(
            accounts=accounts,
            count=200,
            excluded_product_ids={"PROD_WEA_0030", "PROD_WEA_0031"},
        )
    )
    # 候选池排除 WEA 后仅剩 100 个 FUN 账户；count=200 会被截到池大小。
    assert len(rows) == 100
    assert all(r["product_id"] == "PROD_FUN_0001" for r in rows)
    # 关系绑定：每一行的 customer/account 都互相对应，且来自候选池。
    for r in rows:
        assert r["account_id"].startswith("A_FUN_")


def test_generate_holdings_no_exclusion_preserves_existing_behavior():
    gen = TransactionGenerator(seed=42)
    # 60 INVESTMENT accounts; expect 50 holdings (sampled without replacement).
    accounts = [
        _acct(f"A_INV_{i}", customer_id=f"C_{i}", product_id=f"P{i % 3}", account_type="INVESTMENT")
        for i in range(60)
    ]
    rows = list(
        gen.generate_holdings(
            accounts=accounts,
            count=50,
        )
    )
    assert len(rows) == 50
