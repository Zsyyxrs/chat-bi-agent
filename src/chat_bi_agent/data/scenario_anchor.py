"""Scenario anchoring: ensure each event's required_population exists in dim tables.

Pure functions (no DB) + main entry point `anchor_event_populations` (uses cursor).
"""

import hashlib


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
