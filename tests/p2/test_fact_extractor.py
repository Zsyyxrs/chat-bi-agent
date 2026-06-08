"""FactExtractor tests: parsing, skipped-step filtering, error handling."""

from unittest.mock import patch

import pytest

from chat_bi_agent.agents.p2.fact_extractor import (
    FactExtractor,
    FactParseError,
)
from chat_bi_agent.agents.p2.types import Fact, PlanStep, StepResult


def _mock_chat(content: str):
    class _R:
        def __init__(self, c):
            self.content = c
    return _R(content)


def _mk_step(sid: str) -> PlanStep:
    return PlanStep(id=sid, question="q", rationale="r")


SAMPLE_RESPONSE = """```json
{
  "facts": [
    {"metric": "withdraw_total_amount",
     "dimension": {"period": "before", "channel": "ATM"},
     "value": 1000.0, "source_step": "step1"},
    {"metric": "withdraw_count",
     "dimension": {"period": "before"},
     "value": 50, "source_step": "step1"}
  ]
}
```"""


def test_extract_returns_facts():
    extractor = FactExtractor()
    sr1 = StepResult(step=_mk_step("step1"), sql="SELECT 1",
                     rows=[{"amount": 1000, "channel": "ATM"}],
                     error_class=None, error_msg=None, skipped=False)
    with patch(
        "chat_bi_agent.agents.p2.fact_extractor.qwen_client.chat",
        return_value=_mock_chat(SAMPLE_RESPONSE),
    ):
        facts = extractor.extract([sr1])
    assert len(facts) == 2
    assert isinstance(facts[0], Fact)
    assert facts[0].metric == "withdraw_total_amount"
    assert facts[0].source_step == "step1"


def test_extract_skips_skipped_steps_in_prompt():
    extractor = FactExtractor()
    sr1 = StepResult(step=_mk_step("step1"), sql="SELECT 1",
                     rows=[{"x": 1}], error_class=None, error_msg=None, skipped=False)
    sr2 = StepResult(step=_mk_step("step2"), sql=None, rows=None,
                     error_class=None, error_msg="failed", skipped=True)
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE_RESPONSE)

    with patch(
        "chat_bi_agent.agents.p2.fact_extractor.qwen_client.chat",
        side_effect=fake_chat,
    ):
        extractor.extract([sr1, sr2])
    assert "step1" in captured["user_prompt"]
    assert "step2" not in captured["user_prompt"]


def test_extract_empty_when_all_skipped():
    extractor = FactExtractor()
    sr = StepResult(step=_mk_step("step1"), sql=None, rows=None,
                    error_class=None, error_msg="x", skipped=True)
    with patch(
        "chat_bi_agent.agents.p2.fact_extractor.qwen_client.chat",
    ) as mock_chat:
        facts = extractor.extract([sr])
    assert facts == []
    mock_chat.assert_not_called()


def test_extract_raises_on_invalid_json():
    extractor = FactExtractor()
    sr = StepResult(step=_mk_step("step1"), sql="SELECT 1", rows=[{"x": 1}],
                    error_class=None, error_msg=None, skipped=False)
    with patch(
        "chat_bi_agent.agents.p2.fact_extractor.qwen_client.chat",
        return_value=_mock_chat("garbage"),
    ):
        with pytest.raises(FactParseError):
            extractor.extract([sr])


def test_extract_raises_on_missing_facts_key():
    extractor = FactExtractor()
    sr = StepResult(step=_mk_step("step1"), sql="SELECT 1", rows=[{"x": 1}],
                    error_class=None, error_msg=None, skipped=False)
    with patch(
        "chat_bi_agent.agents.p2.fact_extractor.qwen_client.chat",
        return_value=_mock_chat('```json\n{"wrong_key": []}\n```'),
    ):
        with pytest.raises(FactParseError):
            extractor.extract([sr])
