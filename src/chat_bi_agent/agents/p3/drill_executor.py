"""P3 drill-down executor: per-dimension P1 call + Pareto TopN contribution."""

from numbers import Real
from typing import Any

from chat_bi_agent.agents.p3.types import DrillRequest, DrillResult


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


def run_drill_down(
    question_id: str,
    requests: list[DrillRequest],
    p1_agent: Any,
) -> list[DrillResult]:
    """Execute each DrillRequest via P1 and compute Pareto TopN.

    Each drill is independent: a failure in one does not stop the others.
    """
    results: list[DrillResult] = []
    for i, req in enumerate(requests):
        sub_qid = f"{question_id}__drill_{i}"
        p1_result = p1_agent.run(sub_qid, req.nl_question)

        # Failure cases → skip but keep a stub
        if p1_result.rows is None or p1_result.sql is None or not p1_result.rows:
            results.append(
                DrillResult(
                    dimension=req.dimension,
                    nl_question=req.nl_question,
                    sql=p1_result.sql or "",
                    rows=p1_result.rows or [],
                    pareto_top_k=[],
                    error_class=p1_result.error_class,
                    skipped=True,
                )
            )
            continue

        try:
            value_col = _infer_value_col(p1_result.rows, dim_hint=req.dimension)
            top_k = _compute_pareto(p1_result.rows, value_col=value_col)
        except ValueError:
            results.append(
                DrillResult(
                    dimension=req.dimension,
                    nl_question=req.nl_question,
                    sql=p1_result.sql,
                    rows=p1_result.rows,
                    pareto_top_k=[],
                    error_class=None,
                    skipped=True,
                )
            )
            continue

        results.append(
            DrillResult(
                dimension=req.dimension,
                nl_question=req.nl_question,
                sql=p1_result.sql,
                rows=p1_result.rows,
                pareto_top_k=top_k,
                error_class=None,
                skipped=False,
            )
        )
    return results
