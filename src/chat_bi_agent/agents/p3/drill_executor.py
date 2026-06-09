"""P3 drill-down executor: per-dimension P1 call + Pareto TopN contribution."""

from numbers import Real


def _infer_value_col(rows: list[dict], dim_hint: str) -> str:
    """Return the first numeric column in rows that is not dim_hint.

    Raises ValueError if no numeric column found.
    """
    if not rows:
        raise ValueError("cannot infer value column from empty rows")
    sample = rows[0]
    for col, val in sample.items():
        if col == dim_hint:
            continue
        if isinstance(val, Real) and not isinstance(val, bool):
            return col
    raise ValueError(f"no numeric column found in rows (sample keys={list(sample)})")


def _compute_pareto(
    rows: list[dict],
    value_col: str,
    threshold: float = 0.6,
    top_k_cap: int = 3,
) -> list[dict]:
    """Sort rows by |value| desc, return top items until cum_share >= threshold or len >= top_k_cap.

    Returns: [{"key", "value", "share", "cum_share"}, ...]

    Rules:
      - Sort by |value| descending.
      - Total = sum of |value|. If total == 0, return [].
      - Stop when cum_share >= threshold OR len == top_k_cap (whichever first).
      - "key" is the first non-numeric field of each row (the dimension value).
    """
    if not rows:
        return []

    total = sum(abs(r.get(value_col, 0.0)) for r in rows)
    if total == 0:
        return []

    sorted_rows = sorted(rows, key=lambda r: abs(r.get(value_col, 0.0)), reverse=True)

    out: list[dict] = []
    cum = 0.0
    for r in sorted_rows:
        val = r.get(value_col, 0.0)
        share = abs(val) / total
        cum += share
        # extract the "key": first non-numeric, non-value_col field
        key = None
        for k, v in r.items():
            if k == value_col:
                continue
            if not isinstance(v, Real) or isinstance(v, bool):
                key = v
                break
        out.append({"key": key, "value": val, "share": share, "cum_share": cum})
        if cum >= threshold or len(out) >= top_k_cap:
            break
    return out
