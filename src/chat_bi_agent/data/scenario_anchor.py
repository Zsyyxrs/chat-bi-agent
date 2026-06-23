"""Scenario anchoring: ensure each event's required_population exists in dim tables.

Pure functions (no DB) + main entry point `anchor_event_populations` (uses cursor).
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any


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
        pid for pid, meta in product_index.items() if meta.get("product_subcategory") == subcategory
    ]
    if not candidates:
        raise LookupError(f"no product with subcategory={subcategory!r}")
    return _det_choice(candidates, hash_key)


def pick_product_by_category(product_index: dict[str, dict], category: str, hash_key: str) -> str:
    candidates = [
        pid for pid, meta in product_index.items() if meta.get("product_category") == category
    ]
    if not candidates:
        raise LookupError(f"no product with category={category!r}")
    return _det_choice(candidates, hash_key)


@dataclass
class ForcedTxnSpec:
    """交易生成时强制注入的规格：来自 must_have_transactions。

    injection_*_offset_days 定义注入窗口相对 event_date 的偏移；默认 -5/+10
    兼容旧行为。脉冲事件（如春节）应自动收紧到匹配 propagation 规则的
    [delay_days, delay_days + ramp_days]，避免注入溢出到对照期淹没信号。

    amount_range 可选 (low, high)：强制注入金额从 uniform(low, high) 采样；
    缺省走 _sample_baseline_amount(txn_type) 走原生重尾分布。窄区间用于
    需要稳定 PoP 信号的场景（spring_festival 的 +25% 在重尾分布下方差过大）。
    """

    event_id: str
    account_ids: list[str]
    txn_type: str
    channels: list[str] | None
    min_txn_per_customer: int
    event_date: date
    injection_start_offset_days: int = -5
    injection_end_offset_days: int = 10
    amount_range: tuple[float, float] | None = None


@dataclass
class AnchorReportEntry:
    event_id: str
    deficit: int
    anchored: int


@dataclass
class AnchorReport:
    entries: list[AnchorReportEntry] = field(default_factory=list)
    forced_specs: list[ForcedTxnSpec] = field(default_factory=list)


def _build_anchor_customer(n_idx: int, event_id: str, branch_id: str, tier: str) -> dict:
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


_PRODUCT_PREFIX_TO_ACCOUNT_TYPE = {
    "LOA": "LOAN",
    "WEA": "INVESTMENT",
    "FUN": "INVESTMENT",
    "INS": "INVESTMENT",
    "CAR": "CARD",
}

# DEPOSIT 子类别需要细分：活期存款 → CURRENT，定期存款 / 大额存单 → SAVING；
# 与 dimension_generator._ACCOUNT_TYPE_TO_PRODUCT_CATEGORIES 保持一致。
_DEPOSIT_SUBCAT_TO_ACCOUNT_TYPE = {
    "活期存款": "CURRENT",
    "定期存款": "SAVING",
    "大额存单": "SAVING",
}


def _account_type_for_product(product_id: str, product_index: dict | None = None) -> str:
    # product_id format: "PROD_<3-char-category>_<NNNN>"
    # (see dimension_generator.generate_products).
    parts = product_id.split("_")
    prefix = parts[1] if len(parts) >= 2 else ""
    if prefix == "DEP" and product_index is not None:
        subcat = product_index.get(product_id, {}).get("product_subcategory", "")
        return _DEPOSIT_SUBCAT_TO_ACCOUNT_TYPE.get(subcat, "SAVING")
    if prefix == "DEP":
        # 缺 product_index 时退回 SAVING（旧行为），但应避免到这里。
        return "SAVING"
    return _PRODUCT_PREFIX_TO_ACCOUNT_TYPE.get(prefix, "CURRENT")


def _build_anchor_account(
    customer: dict,
    product_id: str,
    account_idx: int,
    product_index: dict | None = None,
) -> dict:
    # account_id must fit VARCHAR(32). customer_id is already short (CA_XXXX_NNN).
    cid_short = customer["customer_id"]
    return {
        "account_id": f"ACC_AN_{cid_short}_{account_idx:02d}",
        "customer_id": customer["customer_id"],
        "account_type": _account_type_for_product(product_id, product_index),
        "account_subtype": "ANCHOR",
        "currency": "CNY",
        "product_id": product_id,
        "branch_id": customer["branch_id"],
        "open_date": date(2024, 1, 1),
        "close_date": None,
        "status": "ACTIVE",
        "is_event_anchor": True,
    }


def _build_anchor_holding(customer: dict, product_id: str, account_id: str) -> dict:
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

        # Always anchor min_customers fresh customers, even when existing
        # customers already match the cohort. Reason: verify_events relies
        # on anchor accounts having deterministic balance to escape the
        # CV=1 noise floor of generate_balance_daily's expovariate base.
        deficit = rp.min_customers

        if rp.branches:
            branch_pool = rp.branches
        elif rp.branch_levels:
            branch_pool = [
                bid
                for bid in branch_ids
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
                        acct = _build_anchor_account(cust, pid, acct_idx, product_index)
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
                    new_accounts.append(_build_anchor_account(cust, pid, acct_idx, product_index))
                elif "product_category" in hold_spec:
                    pid = pick_product_by_category(
                        product_index,
                        hold_spec["product_category"],
                        hash_key=cust["customer_id"],
                    )
                    new_accounts.append(_build_anchor_account(cust, pid, acct_idx, product_index))

        _insert_dim_accounts(cursor, new_accounts)
        if new_holdings:
            _insert_fct_holdings(cursor, new_holdings)

        if rp.must_have_transactions:
            mht = rp.must_have_transactions
            # Derive injection window from the propagation rule that matches
            # this must_have_transactions (target=fct_transaction.amount with
            # the same transaction_type). Falls back to -5/+10 when no rule
            # matches—preserves legacy behavior for events without a tx rule.
            inj_start, inj_end = -5, 10
            mht_type = mht.get("type")
            mht_channels = set(mht.get("channels") or [])
            explicit_override = (
                "injection_offset_start_days" in mht or "injection_offset_end_days" in mht
            )
            rules_to_scan = [] if explicit_override else (getattr(event, "propagation", []) or [])
            for rule in rules_to_scan:
                # rule may be either a PropagationRule instance or a raw dict
                # (depends on call site); read via .get / getattr uniformly.
                def _f(name):
                    if isinstance(rule, dict):
                        return rule.get(name)
                    return getattr(rule, name, None)
                if _f("target_table") != "fct_transaction" or _f("target_column") != "amount":
                    continue
                r_type = _f("transaction_type")
                if r_type and r_type != mht_type:
                    continue
                r_channels = _f("transaction_channel")
                if r_channels and mht_channels and not (set(r_channels) & mht_channels):
                    continue
                inj_start = _f("delay_days") or 0
                inj_end = (_f("delay_days") or 0) + (_f("ramp_days") or 0)
                break
            if explicit_override:
                inj_start = mht.get("injection_offset_start_days", inj_start)
                inj_end = mht.get("injection_offset_end_days", inj_end)
            amount_range = None
            ar_raw = mht.get("amount_range")
            if ar_raw and len(ar_raw) == 2:
                amount_range = (float(ar_raw[0]), float(ar_raw[1]))
            report.forced_specs.append(
                ForcedTxnSpec(
                    event_id=event.id,
                    account_ids=[a["account_id"] for a in new_accounts],
                    txn_type=mht["type"],
                    channels=mht.get("channels"),
                    min_txn_per_customer=mht.get("min_txn_per_customer", 1),
                    event_date=event.date,
                    injection_start_offset_days=inj_start,
                    injection_end_offset_days=inj_end,
                    amount_range=amount_range,
                )
            )

        report.entries.append(
            AnchorReportEntry(event.id, deficit=deficit, anchored=len(new_customers))
        )

    return report
