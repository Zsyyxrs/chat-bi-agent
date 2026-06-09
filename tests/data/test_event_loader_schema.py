"""Schema validation tests for EventLoader required_population field."""

from datetime import date

import pytest

from chat_bi_agent.data.event_loader import (
    Event,
    EventLoaderError,
    RequiredPopulation,
)


def _base_event_dict():
    return {
        "id": "test_evt",
        "name": "test",
        "type": "PRODUCT_EXPIRY",
        "date": "2026-05-14",
        "description": "test",
        "affected_dimensions": {},
        "propagation": [],
    }


def test_event_without_required_population_loads():
    event = Event.from_dict(_base_event_dict())
    assert event.required_population is None


def test_required_population_minimal_fields():
    d = _base_event_dict()
    d["required_population"] = {
        "min_customers": 20,
        "must_hold": [{"product_subcategory": "活期存款"}],
    }
    event = Event.from_dict(d)
    assert isinstance(event.required_population, RequiredPopulation)
    assert event.required_population.min_customers == 20
    assert event.required_population.must_hold == [{"product_subcategory": "活期存款"}]


def test_required_population_full_fields():
    d = _base_event_dict()
    d["required_population"] = {
        "min_customers": 50,
        "branches": ["BR_CITY_0006"],
        "tiers": ["HIGH_NET_WORTH"],
        "branch_levels": ["SUBBRANCH"],
        "must_hold": [
            {"product_subcategory": "活期存款"},
            {"product_ids": ["PROD_WEA_0000"]},
        ],
        "must_have_transactions": {
            "type": "WITHDRAW",
            "channels": ["ATM", "COUNTER"],
            "min_txn_per_customer": 3,
        },
    }
    event = Event.from_dict(d)
    rp = event.required_population
    assert rp.branches == ["BR_CITY_0006"]
    assert rp.tiers == ["HIGH_NET_WORTH"]
    assert rp.branch_levels == ["SUBBRANCH"]
    assert len(rp.must_hold) == 2
    assert rp.must_have_transactions["type"] == "WITHDRAW"


def test_required_population_min_customers_zero_raises():
    d = _base_event_dict()
    d["required_population"] = {"min_customers": 0, "must_hold": [{"product_subcategory": "x"}]}
    with pytest.raises(EventLoaderError, match="min_customers"):
        Event.from_dict(d)


def test_required_population_empty_must_hold_raises():
    d = _base_event_dict()
    d["required_population"] = {"min_customers": 20, "must_hold": []}
    with pytest.raises(EventLoaderError, match="must_hold"):
        Event.from_dict(d)


def test_required_population_missing_must_hold_raises():
    d = _base_event_dict()
    d["required_population"] = {"min_customers": 20}
    with pytest.raises(EventLoaderError, match="must_hold"):
        Event.from_dict(d)
