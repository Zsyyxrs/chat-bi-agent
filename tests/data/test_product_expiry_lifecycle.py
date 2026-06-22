"""Tests for apply_product_expiry_lifecycle in seed.py.

Verifies that only events with type=PRODUCT_EXPIRY drive dim_account closure,
and that the returned dict maps every relevant account to its close_date so the
balance generator can skip days after closure.
"""

from datetime import date

from chat_bi_agent.data.event_loader import Event
from chat_bi_agent.data.seed import apply_product_expiry_lifecycle


class _FakeCursor:
    """In-memory fake cursor that simulates dim_account UPDATE + SELECT."""

    def __init__(self, accounts: list[dict]):
        self._accounts = accounts
        self._select_result: list[tuple] = []

    def execute(self, sql: str, params=None):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("UPDATE dim_account"):
            event_date, prod_ids, event_date_2 = params
            for acct in self._accounts:
                if acct["product_id"] in prod_ids and (
                    acct["close_date"] is None or acct["close_date"] > event_date_2
                ):
                    acct["close_date"] = event_date
                    acct["status"] = "CLOSED"
        elif sql_norm.startswith("SELECT account_id, close_date FROM dim_account"):
            (prod_ids,) = params
            self._select_result = [
                (acct["account_id"], acct["close_date"])
                for acct in self._accounts
                if acct["product_id"] in prod_ids and acct["close_date"] is not None
            ]
        else:
            raise AssertionError(f"unexpected SQL: {sql_norm}")

    def fetchall(self):
        return self._select_result


def _make_event(
    event_id: str, event_type: str, event_date: date, product_ids: list[str] | None = None
) -> Event:
    return Event(
        id=event_id,
        name=event_id,
        type=event_type,
        date=event_date,
        description="",
        affected_dimensions={"product_id": product_ids} if product_ids else {},
    )


def test_product_expiry_event_closes_matching_accounts():
    accounts = [
        {"account_id": "A1", "product_id": "PROD_WEA_0030", "close_date": None, "status": "ACTIVE"},
        {"account_id": "A2", "product_id": "PROD_WEA_0030", "close_date": None, "status": "ACTIVE"},
        {"account_id": "A3", "product_id": "PROD_DEP_0001", "close_date": None, "status": "ACTIVE"},
    ]
    cursor = _FakeCursor(accounts)
    events = [
        _make_event("anxin", "PRODUCT_EXPIRY", date(2026, 5, 14), ["PROD_WEA_0030"]),
    ]

    result = apply_product_expiry_lifecycle(cursor, events)

    assert result.account_close_dates == {"A1": date(2026, 5, 14), "A2": date(2026, 5, 14)}
    assert result.product_expiry_dates == {"PROD_WEA_0030": date(2026, 5, 14)}
    assert accounts[0]["close_date"] == date(2026, 5, 14)
    assert accounts[0]["status"] == "CLOSED"
    assert accounts[1]["close_date"] == date(2026, 5, 14)
    assert accounts[2]["close_date"] is None  # untouched
    assert accounts[2]["status"] == "ACTIVE"


def test_non_product_expiry_events_are_skipped():
    accounts = [
        {"account_id": "A1", "product_id": "PROD_DEP_0005", "close_date": None, "status": "ACTIVE"},
    ]
    cursor = _FakeCursor(accounts)
    events = [
        _make_event("qixi", "MARKETING_EVENT", date(2026, 8, 10), ["PROD_DEP_0005"]),
        _make_event("lpr", "MACRO_EVENT", date(2026, 7, 20)),
        _make_event("spring", "SEASONAL_EVENT", date(2026, 2, 15)),
    ]

    result = apply_product_expiry_lifecycle(cursor, events)

    assert result.account_close_dates == {}
    assert result.product_expiry_dates == {}
    assert accounts[0]["close_date"] is None
    assert accounts[0]["status"] == "ACTIVE"


def test_product_expiry_without_product_id_is_skipped():
    """If affected_dimensions.product_id is missing/empty, we skip silently."""
    accounts = [
        {"account_id": "A1", "product_id": "PROD_WEA_0030", "close_date": None, "status": "ACTIVE"},
    ]
    cursor = _FakeCursor(accounts)
    events = [_make_event("bad", "PRODUCT_EXPIRY", date(2026, 5, 14), product_ids=None)]

    result = apply_product_expiry_lifecycle(cursor, events)

    assert result.account_close_dates == {}
    assert result.product_expiry_dates == {}
    assert accounts[0]["close_date"] is None


def test_multiple_expiry_events_aggregate_closures():
    accounts = [
        {"account_id": "A1", "product_id": "PROD_WEA_0030", "close_date": None, "status": "ACTIVE"},
        {"account_id": "A2", "product_id": "PROD_WEA_0099", "close_date": None, "status": "ACTIVE"},
    ]
    cursor = _FakeCursor(accounts)
    events = [
        _make_event("e1", "PRODUCT_EXPIRY", date(2026, 5, 14), ["PROD_WEA_0030"]),
        _make_event("e2", "PRODUCT_EXPIRY", date(2026, 8, 1), ["PROD_WEA_0099"]),
    ]

    result = apply_product_expiry_lifecycle(cursor, events)

    assert result.account_close_dates == {"A1": date(2026, 5, 14), "A2": date(2026, 8, 1)}
    assert result.product_expiry_dates == {
        "PROD_WEA_0030": date(2026, 5, 14),
        "PROD_WEA_0099": date(2026, 8, 1),
    }


def test_already_closed_earlier_account_preserved():
    """Account closed before event date keeps its earlier close_date; still returned."""
    accounts = [
        {
            "account_id": "A1",
            "product_id": "PROD_WEA_0030",
            "close_date": date(2026, 3, 1),
            "status": "CLOSED",
        },
    ]
    cursor = _FakeCursor(accounts)
    events = [_make_event("anxin", "PRODUCT_EXPIRY", date(2026, 5, 14), ["PROD_WEA_0030"])]

    result = apply_product_expiry_lifecycle(cursor, events)

    # UPDATE leaves close_date untouched (earlier than event date), SELECT still picks it up
    # so the balance generator knows to stop at 2026-03-01.
    assert result.account_close_dates == {"A1": date(2026, 3, 1)}
    assert result.product_expiry_dates == {"PROD_WEA_0030": date(2026, 5, 14)}
    assert accounts[0]["close_date"] == date(2026, 3, 1)
