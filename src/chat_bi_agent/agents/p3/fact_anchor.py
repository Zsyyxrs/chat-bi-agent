"""P3 fact_anchor step: wraps P1 NL2SQL to anchor metric + period-over-period change."""

from typing import Literal


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
