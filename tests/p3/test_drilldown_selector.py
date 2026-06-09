"""Tests for drilldown_selector (LLM JSON + fallback)."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from chat_bi_agent.agents.p3.drilldown_selector import (
    DEFAULT_DIMS,
    _parse_selector_json,
    select_drilldown_dims,
)
from chat_bi_agent.agents.p3.types import DrillRequest, FactAnchor


def _anchor() -> FactAnchor:
    return FactAnchor(
        metric_name="retail_deposit_balance",
        time_window="2026-05-01 to 2026-05-20",
        current_value=92.0,
        prior_value=100.0,
        change_pct=-8.0,
        direction="down",
        sql="SELECT ...",
        rows=[],
    )


def _content(s: str) -> SimpleNamespace:
    return SimpleNamespace(content=s)


def test_parse_selector_json_raw():
    raw = (
        '{"sub_questions": ['
        '{"dimension": "branch_id", "nl_question": "按 branch_id 拆解"}, '
        '{"dimension": "customer_tier", "nl_question": "按 customer_tier 拆解"}'
        "]}"
    )
    out = _parse_selector_json(raw)
    assert len(out) == 2
    assert out[0].dimension == "branch_id"


def test_parse_selector_json_in_fence():
    raw = """```json
{"sub_questions": [
  {"dimension": "branch_id", "nl_question": "按 branch_id 拆解"},
  {"dimension": "product_id", "nl_question": "按 product_id 拆解"}
]}
```"""
    out = _parse_selector_json(raw)
    assert [r.dimension for r in out] == ["branch_id", "product_id"]


def test_select_drilldown_dims_happy():
    fake_client = MagicMock()
    fake_client.chat.return_value = _content(
        '{"sub_questions": ['
        '{"dimension": "branch_id", "nl_question": "按 branch_id 拆解原问题"},'
        '{"dimension": "customer_tier", "nl_question": "按 customer_tier 拆解"}'
        ']}'
    )
    out = select_drilldown_dims(
        question="why?",
        fact_anchor=_anchor(),
        llm_client=fake_client,
    )
    assert len(out) == 2
    assert out[0].dimension == "branch_id"
    assert out[1].dimension == "customer_tier"


def test_select_drilldown_dims_invalid_json_fallback():
    fake_client = MagicMock()
    fake_client.chat.return_value = _content("not json at all")
    out = select_drilldown_dims(
        question="why retail_deposit_balance dropped?",
        fact_anchor=_anchor(),
        llm_client=fake_client,
    )
    assert len(out) == 2
    assert out[0].dimension == DEFAULT_DIMS[0]
    assert out[1].dimension == DEFAULT_DIMS[1]


def test_select_drilldown_dims_unknown_dim_filtered():
    fake_client = MagicMock()
    fake_client.chat.return_value = _content(
        '{"sub_questions": ['
        '{"dimension": "branch_id", "nl_question": "按 branch_id 拆解"},'
        '{"dimension": "made_up_dim", "nl_question": "按 made_up_dim 拆解"}'
        ']}'
    )
    out = select_drilldown_dims(
        question="why?",
        fact_anchor=_anchor(),
        llm_client=fake_client,
    )
    # made_up_dim filtered out; only branch_id remains. With count < 2,
    # fallback pads with DEFAULT_DIMS until 2.
    assert len(out) >= 2
    assert out[0].dimension == "branch_id"


def test_select_drilldown_dims_too_many_truncates():
    fake_client = MagicMock()
    subs = [
        {"dimension": d, "nl_question": f"按 {d} 拆解"}
        for d in DEFAULT_DIMS[:6]  # 6 items
    ]
    fake_client.chat.return_value = _content(json.dumps({"sub_questions": subs}))
    out = select_drilldown_dims(
        question="why?",
        fact_anchor=_anchor(),
        llm_client=fake_client,
    )
    assert len(out) == 4  # capped at 4


def test_select_drilldown_dims_llm_exception_uses_fallback():
    fake_client = MagicMock()
    fake_client.chat.side_effect = RuntimeError("LLM down")
    out = select_drilldown_dims(
        question="why?",
        fact_anchor=_anchor(),
        llm_client=fake_client,
    )
    assert len(out) == 2
    assert all(isinstance(r, DrillRequest) for r in out)
