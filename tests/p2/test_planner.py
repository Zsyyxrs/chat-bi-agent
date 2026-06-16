"""Planner tests: JSON parsing, validation, schema linking integration.

All tests mock qwen_client.chat and SchemaLinker — no real LLM or embedding calls.
"""

from unittest.mock import MagicMock, patch

import pytest

from chat_bi_agent.agents.p2.planner import (
    Planner,
    PlanParseError,
    PlanValidationError,
)
from chat_bi_agent.agents.p2.types import P2Plan, PlanStep


def _mock_chat(content: str):
    class _R:
        def __init__(self, c):
            self.content = c

    return _R(content)


VALID_PLAN_JSON = """```json
{
  "plan_type": "temporal_comparison",
  "steps": [
    {
      "id": "step1",
      "question": "查询春节前现金支取",
      "rationale": "建立基线",
      "depends_on": [],
      "context_keys": [],
      "expected_metrics": ["withdraw_total"]
    },
    {
      "id": "step2",
      "question": "查询春节期间现金支取",
      "rationale": "对比组",
      "depends_on": [],
      "context_keys": [],
      "expected_metrics": ["withdraw_total"]
    }
  ]
}
```"""


def _make_planner_with_mock_linker():
    mock_linker = MagicMock()
    mock_match_a = MagicMock(name="fct_transaction")
    mock_match_a.name = "fct_transaction"
    mock_match_b = MagicMock(name="dim_date")
    mock_match_b.name = "dim_date"
    mock_linker.link.return_value = [mock_match_a, mock_match_b]
    mock_loader = MagicMock()
    mock_loader.get_ddl_text.return_value = "CREATE TABLE x (...)"
    return Planner(schema_linker=mock_linker, loader=mock_loader, top_k=8)


def test_plan_returns_valid_p2plan():
    planner = _make_planner_with_mock_linker()
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat(VALID_PLAN_JSON),
    ):
        plan = planner.plan(question="春节对比")
    assert isinstance(plan, P2Plan)
    assert plan.question == "春节对比"
    assert plan.plan_type == "temporal_comparison"
    assert len(plan.steps) == 2
    assert isinstance(plan.steps[0], PlanStep)
    assert plan.steps[0].id == "step1"
    assert plan.steps[0].expected_metrics == ["withdraw_total"]


def test_plan_injects_schema_into_user_prompt():
    planner = _make_planner_with_mock_linker()
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(VALID_PLAN_JSON)

    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        side_effect=fake_chat,
    ):
        planner.plan(question="春节对比")
    assert "CREATE TABLE x" in captured["user_prompt"]
    assert "春节对比" in captured["user_prompt"]


def test_plan_includes_few_shot_examples_in_prompt():
    planner = _make_planner_with_mock_linker()
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(VALID_PLAN_JSON)

    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        side_effect=fake_chat,
    ):
        planner.plan(question="春节对比")
    assert "春节前" in captured["user_prompt"]


def test_plan_raises_parse_error_on_invalid_json():
    planner = _make_planner_with_mock_linker()
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat("not json at all"),
    ):
        with pytest.raises(PlanParseError):
            planner.plan(question="q")


def test_plan_raises_validation_error_on_too_few_steps():
    bad = """```json
{"plan_type": "temporal_comparison", "steps": [
  {"id": "step1", "question": "q", "rationale": "r",
   "depends_on": [], "context_keys": [], "expected_metrics": []}
]}
```"""
    planner = _make_planner_with_mock_linker()
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat(bad),
    ):
        with pytest.raises(PlanValidationError):
            planner.plan(question="q")


def test_plan_raises_validation_error_on_too_many_steps():
    steps = [
        {
            "id": f"step{i}",
            "question": "q",
            "rationale": "r",
            "depends_on": [],
            "context_keys": [],
            "expected_metrics": [],
        }
        for i in range(1, 10)
    ]
    import json as _json

    bad = (
        "```json\n"
        + _json.dumps(
            {
                "plan_type": "temporal_comparison",
                "steps": steps,
            }
        )
        + "\n```"
    )
    planner = _make_planner_with_mock_linker()
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat(bad),
    ):
        with pytest.raises(PlanValidationError):
            planner.plan(question="q")


def test_plan_raises_validation_error_on_missing_step_field():
    bad = """```json
{"plan_type": "temporal_comparison", "steps": [
  {"id": "step1", "question": "q",
   "depends_on": [], "context_keys": [], "expected_metrics": []},
  {"id": "step2", "question": "q", "rationale": "r",
   "depends_on": [], "context_keys": [], "expected_metrics": []}
]}
```"""
    planner = _make_planner_with_mock_linker()
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat(bad),
    ):
        with pytest.raises(PlanValidationError):
            planner.plan(question="q")
