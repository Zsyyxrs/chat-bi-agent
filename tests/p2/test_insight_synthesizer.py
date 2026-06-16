"""InsightSynthesizer tests."""

from unittest.mock import patch

import pytest

from chat_bi_agent.agents.p2.insight_synthesizer import (
    InsightParseError,
    InsightSynthesizer,
)
from chat_bi_agent.agents.p2.types import Fact, Insight


def _mock_chat(content: str):
    class _R:
        def __init__(self, c):
            self.content = c

    return _R(content)


SAMPLE = """```json
{
  "insights": [
    {"statement": "春节期间现金支取增长25%",
     "supporting_facts": [0, 1], "confidence": "high"},
    {"statement": "客户数同比上升",
     "supporting_facts": [0], "confidence": "medium"}
  ]
}
```"""


def test_synthesize_returns_insights():
    syn = InsightSynthesizer()
    facts = [
        Fact(
            metric="withdraw_amount",
            dimension={"period": "before"},
            value=1000,
            source_step="step1",
        ),
        Fact(
            metric="withdraw_amount", dimension={"period": "after"}, value=1250, source_step="step2"
        ),
    ]
    with patch(
        "chat_bi_agent.agents.p2.insight_synthesizer.qwen_client.chat",
        return_value=_mock_chat(SAMPLE),
    ):
        insights = syn.synthesize(question="春节对比", facts=facts)
    assert len(insights) == 2
    assert isinstance(insights[0], Insight)
    assert insights[0].statement == "春节期间现金支取增长25%"
    assert insights[0].supporting_facts == [0, 1]
    assert insights[0].confidence == "high"


def test_synthesize_includes_facts_and_question_in_prompt():
    syn = InsightSynthesizer()
    facts = [
        Fact(metric="m1", dimension={"d": "v"}, value=42, source_step="step1"),
    ]
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE)

    with patch(
        "chat_bi_agent.agents.p2.insight_synthesizer.qwen_client.chat",
        side_effect=fake_chat,
    ):
        syn.synthesize(question="春节对比", facts=facts)
    assert "春节对比" in captured["user_prompt"]
    assert "m1" in captured["user_prompt"]
    assert "42" in captured["user_prompt"]


def test_synthesize_empty_when_no_facts():
    syn = InsightSynthesizer()
    with patch(
        "chat_bi_agent.agents.p2.insight_synthesizer.qwen_client.chat",
    ) as mock_chat:
        insights = syn.synthesize(question="q", facts=[])
    assert insights == []
    mock_chat.assert_not_called()


def test_synthesize_raises_on_invalid_json():
    syn = InsightSynthesizer()
    facts = [Fact(metric="m", dimension={}, value=1, source_step="step1")]
    with patch(
        "chat_bi_agent.agents.p2.insight_synthesizer.qwen_client.chat",
        return_value=_mock_chat("not json"),
    ):
        with pytest.raises(InsightParseError):
            syn.synthesize(question="q", facts=facts)
