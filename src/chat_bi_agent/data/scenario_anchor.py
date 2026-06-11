"""Scenario anchoring: ensure each event's required_population exists in dim tables.

Pure functions (no DB) + main entry point `anchor_event_populations` (uses cursor).
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any


def filter_customers(
    customers: list[dict],
    branches: list[str] | None = None,
    tiers: list[str] | None = None,
    branch_levels: list[str] | None = None,
    branch_index: dict[str, dict] | None = None,
) -> list[dict]:
    """Filter customer dicts by branch / tier / branch_level (AND)."""
    result = customers
    if branches:
        result = [c for c in result if c.get("branch_id") in branches]
    if tiers:
        result = [c for c in result if c.get("customer_tier") in tiers]
    if branch_levels:
        if branch_index is None:
            raise RuntimeError("branch_levels filter requires branch_index")
        result = [
            c for c in result
            if branch_index.get(c.get("branch_id"), {}).get("branch_level") in branch_levels
        ]
    return result


def has_holding(
    customer: dict,
    accounts: list[dict],
    hold_spec: dict,
    product_index: dict[str, dict],
) -> bool:
    """Check whether customer holds at least one account matching hold_spec.

    hold_spec keys (mutually exclusive in spec):
      - product_subcategory: str
      - product_ids: list[str]
      - product_category: str
    """
    cid = customer.get("customer_id")
    cust_accounts = [a for a in accounts if a.get("customer_id") == cid]
    if not cust_accounts:
        return False

    if "product_ids" in hold_spec:
        wanted = set(hold_spec["product_ids"])
        return any(a.get("product_id") in wanted for a in cust_accounts)

    if "product_subcategory" in hold_spec:
        wanted = hold_spec["product_subcategory"]
        return any(
            product_index.get(a.get("product_id"), {}).get("product_subcategory") == wanted
            for a in cust_accounts
        )

    if "product_category" in hold_spec:
        wanted = hold_spec["product_category"]
        return any(
            product_index.get(a.get("product_id"), {}).get("product_category") == wanted
            for a in cust_accounts
        )

    return False


def _det_choice(items: list[str], hash_key: str) -> str:
    """Deterministic pick from a sorted list using SHA1(hash_key)."""
    if not items:
        raise LookupError("empty candidate list")
    sorted_items = sorted(items)
    h = int(hashlib.sha1(hash_key.encode("utf-8")).hexdigest(), 16)
    return sorted_items[h % len(sorted_items)]


def pick_product_by_subcategory(
    product_index: dict[str, dict], subcategory: str, hash_key: str
) -> str:
    candidates = [
        pid for pid, meta in product_index.items()
        if meta.get("product_subcategory") == subcategory
    ]
    if not candidates:
        raise LookupError(f"no product with subcategory={subcategory!r}")
    return _det_choice(candidates, hash_key)


def pick_product_by_category(
    product_index: dict[str, dict], category: str, hash_key: str
) -> str:
    candidates = [
        pid for pid, meta in product_index.items()
        if meta.get("product_category") == category
    ]
    if not candidates:
        raise LookupError(f"no product with category={category!r}")
    return _det_choice(candidates, hash_key)


@dataclass
class ForcedTxnSpec:
    """交易生成时强制注入的规格：来自 must_have_transactions。"""

    event_id: str
    account_ids: list[str]
    txn_type: str
    channels: list[str] | None
    min_txn_per_customer: int
    event_date: date


@dataclass
class AnchorReportEntry:
    event_id: str
    deficit: int
    anchored: int


@dataclass
class AnchorReport:
    entries: list[AnchorReportEntry] = field(default_factory=list)
    forced_specs: list[ForcedTxnSpec] = field(default_factory=list)


def _build_anchor_customer(
    n_idx: int, event_id: str, branch_id: str, tier: str
) -> dict:
    # dim_customer.customer_id is VARCHAR(16); use SHA1 prefix of event_id to fit.
    short_evt = hashlib.sha1(event_id.encode("utf-8")).hexdigest()[:4]
    cid = f"CA_{short_evt}_{n_idx:03d}"  # 11 chars, fits VARCHAR(16)
    return {
        "customer_id": cid,
        "customer_name": f"_anchor_{event_id}_{n_idx:03d}",
        "id_no_masked": "0000****0000",
        "gender": "U",
        "birth_date": date(1985, 1, 1),
        "age": 41,
        "customer_tier": tier,
        "risk_appetite": "C3",
        "open_date": date(2024, 1, 1),
        "branch_id": branch_id,
        "customer_manager_id": "MGR_ANCHOR",
        "aum": 0,
        "is_active": True,
        "is_event_anchor": True,
    }


def _build_anchor_account(
    customer: dict, product_id: str, account_idx: int
) -> dict:
    # account_id must fit VARCHAR(32). customer_id is already short (CA_XXXX_NNN).
    cid_short = customer["customer_id"]
    return {
        "account_id": f"ACC_AN_{cid_short}_{account_idx:02d}",
        "customer_id": customer["customer_id"],
        "account_type": "CURRENT",
        "account_subtype": "ANCHOR",
        "currency": "CNY",
        "product_id": product_id,
        "branch_id": customer["branch_id"],
        "open_date": date(2024, 1, 1),
        "close_date": None,
        "status": "ACTIVE",
        "is_event_anchor": True,
    }


def _build_anchor_holding(
    customer: dict, product_id: str, account_id: str
) -> dict:
    return {
        "snapshot_dt": date(2026, 1, 1),
        "account_id": account_id,
        "customer_id": customer["customer_id"],
        "product_id": product_id,
        "branch_id": customer["branch_id"],
        "holding_amount": 100000.0,
        "holding_shares": 0,
        "market_value": 100000.0,
        "cost_basis": 100000.0,
        "pnl": 0,
        "currency": "CNY",
        "is_event_anchor": True,
    }


def _insert_dim_customers(cursor, rows: list[dict]) -> None:
    for row in rows:
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cursor.execute(
            f"INSERT INTO dim_customer ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(row[c] for c in cols),
        )


def _insert_dim_accounts(cursor, rows: list[dict]) -> None:
    for row in rows:
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cursor.execute(
            f"INSERT INTO dim_account ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(row[c] for c in cols),
        )


def _insert_fct_holdings(cursor, rows: list[dict]) -> None:
    for row in rows:
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cursor.execute(
            f"INSERT INTO fct_holding ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(row[c] for c in cols),
        )


def anchor_event_populations(
    cursor: Any,
    events: list[Any],
    existing_customers: list[dict],
    existing_accounts: list[dict],
    branch_ids: list[str],
    branch_index: dict[str, dict],
    product_index: dict[str, dict],
) -> AnchorReport:
    """For each event with required_population, ensure dim tables satisfy the contract.

    Inserts anchor rows (is_event_anchor=True) only where deficient. Returns
    AnchorReport with per-event counts and forced_specs for transaction stage.
    """
    report = AnchorReport()

    for event in events:
        rp = event.required_population
        if rp is None:
            continue

        qualified = filter_customers(
            existing_customers,
            branches=rp.branches,
            tiers=rp.tiers,
            branch_levels=rp.branch_levels,
            branch_index=branch_index,
        )
        for hold_spec in rp.must_hold:
            qualified = [
                c for c in qualified
                if has_holding(c, existing_accounts, hold_spec, product_index)
            ]
        # Always anchor min_customers fresh customers, even when existing
        # customers already match the cohort. Reason: verify_events relies
        # on anchor accounts having deterministic balance to escape the
        # CV=1 noise floor of generate_balance_daily's expovariate base.
        deficit = rp.min_customers

        if rp.branches:
            branch_pool = rp.branches
        elif rp.branch_levels:
            branch_pool = [
                bid for bid in branch_ids
                if branch_index.get(bid, {}).get("branch_level") in rp.branch_levels
            ]
            if not branch_pool:
                raise LookupError(
                    f"no branches match branch_levels={rp.branch_levels} for event {event.id}"
                )
        else:
            branch_pool = branch_ids

        tier_pool = rp.tiers or ["MASS"]

        new_customers = []
        for i in range(1, deficit + 1):
            branch = _det_choice(branch_pool, hash_key=f"{event.id}_b_{i}")
            tier = _det_choice(tier_pool, hash_key=f"{event.id}_t_{i}")
            new_customers.append(_build_anchor_customer(i, event.id, branch, tier))

        _insert_dim_customers(cursor, new_customers)

        new_accounts = []
        new_holdings = []
        for cust in new_customers:
            acct_idx = 0
            for hold_spec in rp.must_hold:
                acct_idx += 1
                if "product_ids" in hold_spec:
                    for pid in hold_spec["product_ids"]:
                        acct = _build_anchor_account(cust, pid, acct_idx)
                        new_accounts.append(acct)
                        new_holdings.append(
                            _build_anchor_holding(cust, pid, account_id=acct["account_id"])
                        )
                        acct_idx += 1
                elif "product_subcategory" in hold_spec:
                    pid = pick_product_by_subcategory(
                        product_index,
                        hold_spec["product_subcategory"],
                        hash_key=cust["customer_id"],
                    )
                    new_accounts.append(_build_anchor_account(cust, pid, acct_idx))
                elif "product_category" in hold_spec:
                    pid = pick_product_by_category(
                        product_index,
                        hold_spec["product_category"],
                        hash_key=cust["customer_id"],
                    )
                    new_accounts.append(_build_anchor_account(cust, pid, acct_idx))

        _insert_dim_accounts(cursor, new_accounts)
        if new_holdings:
            _insert_fct_holdings(cursor, new_holdings)

        if rp.must_have_transactions:
            mht = rp.must_have_transactions
            report.forced_specs.append(
                ForcedTxnSpec(
                    event_id=event.id,
                    account_ids=[a["account_id"] for a in new_accounts],
                    txn_type=mht["type"],
                    channels=mht.get("channels"),
                    min_txn_per_customer=mht.get("min_txn_per_customer", 1),
                    event_date=event.date,
                )
            )

        report.entries.append(
            AnchorReportEntry(event.id, deficit=deficit, anchored=len(new_customers))
        )

    return report
