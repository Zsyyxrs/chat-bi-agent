"""Tests for event_matcher (deterministic, no LLM)."""

from pathlib import Path

from chat_bi_agent.agents.p3.event_matcher import (
    _date_overlap,
    _extract_date_range_from_sql,
    _load_events,
    match_events,
)
from chat_bi_agent.agents.p3.types import FactAnchor


def _make_anchor(sql: str) -> FactAnchor:
    return FactAnchor(
        metric_name="x",
        time_window="",
        current_value=0.0,
        prior_value=None,
        change_pct=None,
        direction="flat",
        sql=sql,
        rows=[],
    )


def test_extract_date_range_between():
    sql = "SELECT * FROM t WHERE date BETWEEN '2026-05-01' AND '2026-05-20'"
    assert _extract_date_range_from_sql(sql) == ("2026-05-01", "2026-05-20")


def test_extract_date_range_gte_lte():
    sql = "SELECT * FROM t WHERE date >= '2026-05-01' AND date <= '2026-05-20'"
    assert _extract_date_range_from_sql(sql) == ("2026-05-01", "2026-05-20")


def test_extract_date_range_single_date_falls_back_to_same_day():
    sql = "SELECT * FROM t WHERE date = '2026-05-14'"
    assert _extract_date_range_from_sql(sql) == ("2026-05-14", "2026-05-14")


def test_extract_date_range_none_when_no_dates():
    sql = "SELECT 1"
    assert _extract_date_range_from_sql(sql) is None


def test_date_overlap_within_window():
    assert _date_overlap("2026-05-14", ("2026-05-01", "2026-05-20"), slack_days=0) is True


def test_date_overlap_outside_with_slack():
    # 5/14 event, window 5/15..5/20, slack=7 days → still matches
    assert _date_overlap("2026-05-14", ("2026-05-15", "2026-05-20"), slack_days=7) is True


def test_date_overlap_outside_without_slack():
    assert _date_overlap("2026-05-14", ("2026-05-15", "2026-05-20"), slack_days=0) is False


def test_load_events(fake_events_dir: Path):
    events = _load_events(fake_events_dir)
    assert len(events) == 3
    ids = {e["id"] for e in events}
    assert ids == {"anxin_90_expire", "spring_festival_withdrawal", "lpr_cut_q2"}


def test_match_events_overlap_with_slack(fake_events_dir: Path):
    anchor = _make_anchor(
        "SELECT SUM(balance) FROM fct_balance_daily"
        " WHERE date BETWEEN '2026-05-01' AND '2026-05-20'"
    )
    matched = match_events(anchor, fake_events_dir, slack_days=7)
    matched_ids = [m.event_id for m in matched]
    assert "anxin_90_expire" in matched_ids  # 2026-05-14 inside window
    assert "lpr_cut_q2" not in matched_ids  # 2026-06-20 outside even with slack
    assert "spring_festival_withdrawal" not in matched_ids


def test_match_events_no_dates_returns_all(fake_events_dir: Path):
    anchor = _make_anchor("SELECT 1")
    matched = match_events(anchor, fake_events_dir, slack_days=7)
    assert len(matched) == 3  # fallback: return all
