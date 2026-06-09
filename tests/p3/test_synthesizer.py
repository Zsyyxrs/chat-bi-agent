"""Tests for synthesizer (LLM with mocked client)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from chat_bi_agent.agents.p3.synthesizer import _build_user_prompt, synthesize_narrative
from chat_bi_agent.agents.p3.types import (
    DrillResult,
    FactAnchor,
    MatchedEvent,
)


def _fact_anchor() -> FactAnchor:
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


def _drill() -> DrillResult:
    return DrillResult(
        dimension="branch_id",
        nl_question="按 branch_id 拆解",
        sql="SELECT branch_id, SUM(balance) ...",
        rows=[],
        pareto_top_k=[
            {"key": "BR_CITY_0006", "value": -80.0, "share": 0.8, "cum_share": 0.8},
        ],
    )


def _event() -> MatchedEvent:
    return MatchedEvent(
        event_id="anxin_90_expire",
        event_name="安鑫90天理财到期",
        effective_date="2026-05-14",
        relevance="overlap",
    )


def test_build_user_prompt_contains_anchor_and_drill_and_events():
    prompt = _build_user_prompt(
        question="上海分行高净值客户为什么...",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
    )
    assert "retail_deposit_balance" in prompt
    assert "BR_CITY_0006" in prompt
    assert "0.8" in prompt or "80" in prompt
    assert "anxin_90_expire" in prompt or "安鑫" in prompt


def test_build_user_prompt_no_events_omits_event_block():
    prompt = _build_user_prompt(
        question="why?",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[],
    )
    assert "anxin_90_expire" not in prompt


def test_synthesize_narrative_calls_llm_and_returns_text():
    fake_client = MagicMock()
    fake_client.chat.return_value = SimpleNamespace(content="narrative text BR_CITY_0006 安鑫")

    out = synthesize_narrative(
        question="why?",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake_client,
    )
    assert out == "narrative text BR_CITY_0006 安鑫"
    assert fake_client.chat.call_count == 1
    # System prompt must be passed via system_prompt kwarg
    call_kwargs = fake_client.chat.call_args.kwargs
    sysprompt = call_kwargs.get("system_prompt", "")
    assert "禁止" in sysprompt or "不要编造" in sysprompt or "严禁" in sysprompt


def test_synthesize_narrative_llm_failure_returns_fallback():
    fake_client = MagicMock()
    fake_client.chat.side_effect = RuntimeError("LLM down")

    out = synthesize_narrative(
        question="why did X drop?",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake_client,
    )
    assert "retail_deposit_balance" in out
    assert "BR_CITY_0006" in out
