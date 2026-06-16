"""ReportWriter tests: prompt building, system prompt enforcement."""

from unittest.mock import patch

from chat_bi_agent.agents.p2.prompts.report_writer_system import (
    REPORT_WRITER_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p2.report_writer import ReportWriter
from chat_bi_agent.agents.p2.types import Fact, Insight, P2Plan, PlanStep


def _mock_chat(content: str):
    class _R:
        def __init__(self, c):
            self.content = c

    return _R(content)


def _plan() -> P2Plan:
    return P2Plan(
        question="春节对比",
        plan_type="temporal_comparison",
        steps=[
            PlanStep(id="step1", question="查节前", rationale="建立基线"),
            PlanStep(id="step2", question="查节中", rationale="对比"),
        ],
    )


def test_write_returns_llm_content():
    writer = ReportWriter()
    facts = [Fact(metric="m", dimension={}, value=1, source_step="step1")]
    insights = [Insight(statement="s", supporting_facts=[0], confidence="high")]
    with patch(
        "chat_bi_agent.agents.p2.report_writer.qwen_client.chat",
        return_value=_mock_chat("最终报告正文……"),
    ):
        out = writer.write(question="春节对比", plan=_plan(), facts=facts, insights=insights)
    assert out == "最终报告正文……"


def test_write_passes_keyword_guidance_via_system_prompt():
    """The system prompt sent to the LLM must explicitly contain keyword guidance."""
    writer = ReportWriter()
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return _mock_chat("output")

    with patch(
        "chat_bi_agent.agents.p2.report_writer.qwen_client.chat",
        side_effect=fake_chat,
    ):
        writer.write(question="q", plan=_plan(), facts=[], insights=[])
    assert captured["system_prompt"] == REPORT_WRITER_SYSTEM_PROMPT
    assert "因此" in captured["system_prompt"]
    assert "对比" in captured["system_prompt"]
    assert "AUM" in captured["system_prompt"]


def test_write_includes_insights_verbatim_in_user_prompt():
    """User prompt must include the literal Insight statements so the LLM
    can copy keywords verbatim (evaluator does string match)."""
    writer = ReportWriter()
    insights = [
        Insight(statement="春节期间ATM现金支取增长25%", supporting_facts=[0], confidence="high"),
    ]
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat("o")

    with patch(
        "chat_bi_agent.agents.p2.report_writer.qwen_client.chat",
        side_effect=fake_chat,
    ):
        writer.write(question="春节对比", plan=_plan(), facts=[], insights=insights)
    assert "春节期间ATM现金支取增长25%" in captured["user_prompt"]


def test_write_includes_plan_step_rationales_in_user_prompt():
    writer = ReportWriter()
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat("o")

    with patch(
        "chat_bi_agent.agents.p2.report_writer.qwen_client.chat",
        side_effect=fake_chat,
    ):
        writer.write(question="春节对比", plan=_plan(), facts=[], insights=[])
    assert "建立基线" in captured["user_prompt"]
    assert "对比" in captured["user_prompt"]
