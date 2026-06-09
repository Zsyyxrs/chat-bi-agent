"""P3 event matcher: extracts question time window from fact_anchor SQL and
matches against events YAML by date overlap."""

import re
from datetime import date, timedelta
from pathlib import Path

import yaml

from chat_bi_agent.agents.p3.types import FactAnchor, MatchedEvent

_DATE_RE = re.compile(r"'(\d{4}-\d{2}-\d{2})'")


def _extract_date_range_from_sql(sql: str) -> tuple[str, str] | None:
    """Extract (start, end) date strings from a SQL WHERE clause.

    Strategy: find all 'YYYY-MM-DD' literals; return (min, max).
    Returns None when no date literals found.
    """
    dates = _DATE_RE.findall(sql or "")
    if not dates:
        return None
    return min(dates), max(dates)


def _date_overlap(
    event_date_str: str,
    window: tuple[str, str],
    slack_days: int = 7,
) -> bool:
    """True iff event_date is within [window_start - slack, window_end + slack]."""
    ev = date.fromisoformat(event_date_str)
    start = date.fromisoformat(window[0]) - timedelta(days=slack_days)
    end = date.fromisoformat(window[1]) + timedelta(days=slack_days)
    return start <= ev <= end


def _load_events(events_dir: Path) -> list[dict]:
    """Load all events from *.yaml files in events_dir.

    Each YAML is expected to have a top-level 'events' list of dicts with
    at least id/name/date/description fields.
    """
    out: list[dict] = []
    for path in sorted(events_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        events = payload.get("events", [])
        if isinstance(events, list):
            out.extend(events)
    return out


def match_events(
    fact_anchor: FactAnchor,
    events_dir: Path,
    slack_days: int = 7,
) -> list[MatchedEvent]:
    """Return events whose date falls within fact_anchor's SQL date window (with slack).

    Falls back to returning all events when no date window can be extracted.
    """
    events = _load_events(events_dir)
    window = _extract_date_range_from_sql(fact_anchor.sql)

    if window is None:
        return [
            MatchedEvent(
                event_id=e["id"],
                event_name=e.get("name", e["id"]),
                effective_date=e.get("date", ""),
                relevance="fallback: no date window extracted from SQL",
            )
            for e in events
        ]

    matched: list[MatchedEvent] = []
    for e in events:
        ev_date = e.get("date")
        if not ev_date:
            continue
        if _date_overlap(ev_date, window, slack_days=slack_days):
            matched.append(
                MatchedEvent(
                    event_id=e["id"],
                    event_name=e.get("name", e["id"]),
                    effective_date=ev_date,
                    relevance=(
                        f"event date {ev_date} within window"
                        f" {window[0]}..{window[1]} (slack {slack_days}d)"
                    ),
                )
            )
    return matched
