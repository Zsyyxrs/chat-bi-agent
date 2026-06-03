"""Replanner tests."""

from unittest.mock import MagicMock, patch

import pytest

from chat_bi_agent.agents.p2.planner import (
    PlanParseError,
    PlanValidationError,
    Replanner,
)
from chat_bi_agent.agents.p2.types import P2Plan, PlanStep, StepResult
from chat_bi_agent.agents.sql_executor import SQLErrorClass


def _mock_chat(content: str):
    class _R:
        def __init__(self, c):
            self.content = c
    return _R(content)


VALID_REPLAN = """```json
{
  "plan_type": "temporal_comparison",
  "steps": [
    {"id": "step3", "question": "新的子查询", "rationale": "改用 fct_balance_daily",
     "depends_on": [], "context_keys": [], "expected_metrics": ["m"]},
    {"id": "step4", "question": "综合", "rationale": "合并结果",
     "depends_on": ["step3"], "context_keys": ["step3.rows"], "expected_metrics": []}
  ]
}
```"""


def _make_replanner():
    linker = MagicMock()
    m = MagicMock()
    m.name = "fct_transaction"
    linker.link.return_value = [m]
    loader = MagicMock()
    loader.get_ddl_text.return_value = "CREATE TABLE ..."
    return Replanner(schema_linker=linker, loader=loader, top_k=8)


def _orig_plan() -> P2Plan:
    return P2Plan(
        question="q",
        plan_type="temporal_comparison",
        steps=[
            PlanStep(id="step1", question="q1", rationale="r1"),
            PlanStep(id="step2", question="q2", rationale="r2"),
            PlanStep(id="step3", question="q3", rationale="r3"),
        ],
    )


def test_replan_returns_list_of_planstep():
    replanner = _make_replanner()
    failed = _orig_plan().steps[2]
    executed = [
        StepResult(step=_orig_plan().steps[0], sql="s1", rows=[{}],
                   error_class=None, error_msg=None, skipped=False),
        StepResult(step=_orig_plan().steps[1], sql="s2", rows=[{}],
                   error_class=None, error_msg=None, skipped=False),
    ]
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat(VALID_REPLAN),
    ):
        new_steps = replanner.replan(
            original_plan=_orig_plan(),
            failed_at_index=2,
            failed_step=failed,
            error_class=SQLErrorClass.UNKNOWN_TABLE,
            error_msg="relation does not exist",
            executed_steps=executed,
        )
    assert len(new_steps) == 2
    assert all(isinstance(s, PlanStep) for s in new_steps)
    assert new_steps[0].id == "step3"


def test_replan_includes_failure_context_in_prompt():
    replanner = _make_replanner()
    failed = _orig_plan().steps[2]
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(VALID_REPLAN)

    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat", side_effect=fake_chat,
    ):
        replanner.replan(
            original_plan=_orig_plan(),
            failed_at_index=2,
            failed_step=failed,
            error_class=SQLErrorClass.UNKNOWN_TABLE,
            error_msg="relation foo does not exist",
            executed_steps=[],
        )
    assert "UNKNOWN_TABLE" in captured["user_prompt"]
    assert "relation foo does not exist" in captured["user_prompt"]
    assert failed.question in captured["user_prompt"]


def test_replan_raises_on_invalid_json():
    replanner = _make_replanner()
    failed = _orig_plan().steps[2]
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat("garbage"),
    ):
        with pytest.raises(PlanParseError):
            replanner.replan(
                original_plan=_orig_plan(),
                failed_at_index=2,
                failed_step=failed,
                error_class=SQLErrorClass.UNKNOWN_TABLE,
                error_msg="x",
                executed_steps=[],
            )


def test_replan_raises_on_total_steps_out_of_bounds():
    """If executed_steps + new_steps total exceeds MAX_STEPS, must fail validation.

    Uses 7 new steps (within general bounds) + 2 executed = 9 total > 8.
    This specifically exercises the Replanner-only total-bounds check, not the
    generic _validate_plan_dict count check.
    """
    new_steps_json = (
        "```json\n{\n"
        + '  "plan_type": "x", "steps": ['
        + ",".join(
            f'{{"id":"step{i}","question":"q","rationale":"r",'
            f'"depends_on":[],"context_keys":[],"expected_metrics":[]}}'
            for i in range(3, 10)  # 7 new steps: step3..step9
        )
        + "]}\n```"
    )
    replanner = _make_replanner()
    failed = _orig_plan().steps[2]
    executed = [
        StepResult(step=_orig_plan().steps[0], sql="s", rows=[],
                   error_class=None, error_msg=None, skipped=False),
        StepResult(step=_orig_plan().steps[1], sql="s", rows=[],
                   error_class=None, error_msg=None, skipped=False),
    ]
    with patch(
        "chat_bi_agent.agents.p2.planner.qwen_client.chat",
        return_value=_mock_chat(new_steps_json),
    ):
        with pytest.raises(PlanValidationError):
            replanner.replan(
                original_plan=_orig_plan(),
                failed_at_index=2,
                failed_step=failed,
                error_class=SQLErrorClass.UNKNOWN_TABLE,
                error_msg="x",
                executed_steps=executed,
            )
