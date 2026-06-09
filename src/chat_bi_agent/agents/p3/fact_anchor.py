"""P3 fact_anchor step: wraps P1 NL2SQL to anchor metric + period-over-period change."""

import re
from typing import Any, Literal

from chat_bi_agent.agents.p3.types import FactAnchor


def _compute_change(
    current: float,
    prior: float | None,
    flat_band_pct: float = 0.5,
) -> tuple[float, float | None, float | None, Literal["up", "down", "flat"]]:
    """Compute period-over-period change.

    Returns (current, prior, change_pct, direction).
    - prior is None → pct None, direction "flat".
    - prior is 0 → pct None, direction inferred from sign of current.
    - |pct| < flat_band_pct → direction "flat".
    """
    if prior is None:
        return current, None, None, "flat"
    if prior == 0:
        if current > 0:
            return current, prior, None, "up"
        if current < 0:
            return current, prior, None, "down"
        return current, prior, None, "flat"

    pct = (current - prior) / prior * 100.0
    if abs(pct) < flat_band_pct:
        direction: Literal["up", "down", "flat"] = "flat"
    elif pct > 0:
        direction = "up"
    else:
        direction = "down"
    return current, prior, pct, direction


_DATE_LITERAL_RE = re.compile(r"'(\d{4}-\d{2}-\d{2})'")


def _extract_time_window(sql: str) -> str:
    """Extract a 'YYYY-MM-DD to YYYY-MM-DD' string from SQL date literals (best-effort)."""
    dates = _DATE_LITERAL_RE.findall(sql or "")
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0]
    return f"{min(dates)} to {max(dates)}"


def _infer_metric_name(rows: list[dict]) -> str:
    """Use the first numeric column name as the metric label (placeholder).

    Real metric naming would require Metric Platform integration; for MVP we
    surface the SQL column name to the synthesizer (e.g. 'current_balance').
    """
    if not rows:
        return "unknown_metric"
    sample = rows[0]
    for col, val in sample.items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return col
    return "unknown_metric"


def _extract_current_prior(rows: list[dict]) -> tuple[float | None, float | None]:
    """Look for paired (current/prior) numeric columns; fall back to first numeric only."""
    if not rows:
        return None, None
    sample = rows[0]
    cur, prior = None, None
    for col, val in sample.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        lc = col.lower()
        if any(t in lc for t in ("prior", "prev", "last", "lastyear", "yoy", "mom_prev")):
            prior = float(val)
        elif cur is None:
            cur = float(val)
    return cur, prior


def run_fact_anchor(
    question_id: str,
    question: str,
    p1_agent: Any,
) -> FactAnchor | None:
    """Call P1NL2SQLAgent and convert its result into a FactAnchor.

    Returns None if P1 fails (caller decides whether to abort the RCA).
    """
    p1_result = p1_agent.run(question_id, question)
    if p1_result.rows is None or p1_result.sql is None:
        return None

    rows = p1_result.rows
    cur, prior = _extract_current_prior(rows)
    if cur is None:
        return None

    cur, prior, change_pct, direction = _compute_change(current=cur, prior=prior)
    return FactAnchor(
        metric_name=_infer_metric_name(rows),
        time_window=_extract_time_window(p1_result.sql),
        current_value=cur,
        prior_value=prior,
        change_pct=change_pct,
        direction=direction,
        sql=p1_result.sql,
        rows=rows,
    )
