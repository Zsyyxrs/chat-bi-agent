"""Tests for synthesizer (LLM with mocked client)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from chat_bi_agent.agents.p3.synthesizer import (
    _build_user_prompt,
    _parse_dual_output,
    synthesize,
)
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


def test_parse_dual_output_splits_on_tags():
    content = (
        "【叙述】\n上海分行 BR_CITY_0006 出现下滑，主要受安鑫到期影响。\n"
        "【结论】\n根因为安鑫 90 天理财到期。"
    )
    narrative, conclusion = _parse_dual_output(content)
    assert "BR_CITY_0006" in narrative
    assert "结论" not in narrative
    assert conclusion == "根因为安鑫 90 天理财到期。"


def test_parse_dual_output_missing_conclusion_returns_empty():
    narrative, conclusion = _parse_dual_output("just some unstructured text")
    assert narrative == "just some unstructured text"
    assert conclusion == ""


def test_synthesize_returns_narrative_and_conclusion():
    fake_client = MagicMock()
    fake_client.chat.return_value = SimpleNamespace(
        content=("【叙述】\nnarrative text BR_CITY_0006 安鑫\n【结论】\n根因是安鑫 90 天到期。")
    )

    narrative, conclusion = synthesize(
        question="why?",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake_client,
    )
    assert "BR_CITY_0006" in narrative
    assert "结论" not in narrative
    assert "安鑫" in conclusion
    assert fake_client.chat.call_count == 1
    call_kwargs = fake_client.chat.call_args.kwargs
    sysprompt = call_kwargs.get("system_prompt", "")
    assert "禁止" in sysprompt or "不要编造" in sysprompt or "严禁" in sysprompt


def test_synthesize_untagged_output_falls_back_for_conclusion():
    fake_client = MagicMock()
    fake_client.chat.return_value = SimpleNamespace(content="一段没有标签的叙述")

    narrative, conclusion = synthesize(
        question="why?",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake_client,
    )
    assert narrative == "一段没有标签的叙述"
    # Conclusion is derived from facts when LLM omits the tag.
    assert "安鑫" in conclusion or "retail_deposit_balance" in conclusion


def test_synthesize_llm_failure_returns_fallback_pair():
    fake_client = MagicMock()
    fake_client.chat.side_effect = RuntimeError("LLM down")

    narrative, conclusion = synthesize(
        question="why did X drop?",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake_client,
    )
    assert "retail_deposit_balance" in narrative
    assert conclusion  # non-empty fallback conclusion


# ============================================================
# Task 1: is_rca_question classifier
# ============================================================

from chat_bi_agent.agents.p3.synthesizer import is_rca_question


def _anchor_with(change_pct):
    return FactAnchor(
        metric_name="m",
        time_window="w",
        current_value=1.0,
        prior_value=1.0,
        change_pct=change_pct,
        direction="flat" if change_pct in (None, 0.0) else "down",
        sql="",
        rows=[],
    )


def test_is_rca_question_with_significant_change():
    assert is_rca_question(_anchor_with(-20.9)) is True
    assert is_rca_question(_anchor_with(5.0)) is True


def test_is_rca_question_no_change():
    assert is_rca_question(_anchor_with(None)) is False
    assert is_rca_question(_anchor_with(0.0)) is False


def test_is_rca_question_below_threshold():
    assert is_rca_question(_anchor_with(0.3)) is False
    assert is_rca_question(_anchor_with(-0.49)) is False
    assert is_rca_question(_anchor_with(0.5)) is True  # 边界：≥ 0.5% 算 RCA


# ============================================================
# Task 2: close-peer ★ marking
# ============================================================

from chat_bi_agent.agents.p3.synthesizer import (
    CLOSE_PEER_THRESHOLD,
    _mark_close_peers,
)


def test_close_peer_threshold_is_10pp():
    assert CLOSE_PEER_THRESHOLD == 0.10


def test_mark_close_peers_within_threshold():
    items = [
        {"key": "MASS", "share": 0.40},
        {"key": "AFFLUENT", "share": 0.32},
        {"key": "BASIC", "share": 0.31},
    ]
    out = _mark_close_peers(items)
    assert out[0]["is_peer"] is True   # top1 自己
    assert out[1]["is_peer"] is True   # gap 8pp ≤ 10pp
    assert out[2]["is_peer"] is True   # gap 9pp ≤ 10pp


def test_mark_close_peers_outside_threshold():
    items = [
        {"key": "MASS", "share": 0.40},
        {"key": "AFFLUENT", "share": 0.32},
        {"key": "BASIC", "share": 0.15},
    ]
    out = _mark_close_peers(items)
    assert out[0]["is_peer"] is True
    assert out[1]["is_peer"] is True   # gap 8pp ≤ 10pp
    assert out[2]["is_peer"] is False  # gap 25pp > 10pp


def test_mark_close_peers_empty():
    assert _mark_close_peers([]) == []


def test_user_prompt_contains_star_for_peers():
    """有 close peer 时渲染输出含 ★ 标记和说明。"""
    drill = DrillResult(
        dimension="customer_tier",
        nl_question="按 customer_tier 拆解",
        sql="",
        rows=[],
        pareto_top_k=[
            {"key": "MASS", "value": 40.0, "share": 0.40, "cum_share": 0.40},
            {"key": "AFFLUENT", "value": 32.0, "share": 0.32, "cum_share": 0.72},
            {"key": "BASIC", "value": 15.0, "share": 0.15, "cum_share": 0.87},
        ],
    )
    prompt = _build_user_prompt(
        question="why?",
        fact_anchor=_fact_anchor(),
        drill_results=[drill],
        matched_events=[_event()],
    )
    assert "MASS (贡献 40%) ★" in prompt
    assert "AFFLUENT (贡献 32%) ★" in prompt
    assert "BASIC (贡献 15%)" in prompt
    assert "BASIC (贡献 15%) ★" not in prompt
    assert "★ 标记的是并列贡献者" in prompt


# ============================================================
# Task 3: Pass 1 extractor — prompt + parse + validate
# ============================================================

import copy

import pytest

from chat_bi_agent.agents.p3.prompts.synthesizer_extractor import (
    SYNTHESIZER_EXTRACTOR_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p3.synthesizer import (
    _parse_extraction_json,
    _validate_extraction,
)


_GOOD_JSON_STR = """{
  "event": {"id": "anxin_90_expire", "name": "安鑫 90 天到期"},
  "quant": {"metric_name": "AUM", "pop_pct": -20.9,
            "window": "2026-05-14 to 2026-05-20", "direction": "down"},
  "mechanism_chain": [
    "安鑫 90 天产品集中到期触发资金回流",
    "高净值客户选择不续作以观望市场",
    "AUM 出现集中下降"
  ],
  "scope": {"branch_id": ["BR_CITY_0006"], "customer_tier": ["HIGH_NET_WORTH"]}
}"""


def test_extractor_prompt_has_required_rules():
    p = SYNTHESIZER_EXTRACTOR_SYSTEM_PROMPT
    assert "JSON" in p
    assert "mechanism_chain" in p
    assert "3 段" in p or "3段" in p or "三段" in p
    assert "★" in p
    assert "原样" in p or "禁止改" in p


def test_parse_extraction_json_happy_path():
    parsed = _parse_extraction_json(_GOOD_JSON_STR)
    assert parsed["event"]["id"] == "anxin_90_expire"
    assert parsed["quant"]["pop_pct"] == -20.9
    assert len(parsed["mechanism_chain"]) == 3


def test_parse_extraction_json_with_markdown_fence():
    fenced = "```json\n" + _GOOD_JSON_STR + "\n```"
    parsed = _parse_extraction_json(fenced)
    assert parsed["event"]["name"] == "安鑫 90 天到期"


def test_parse_extraction_json_with_plain_fence():
    fenced = "```\n" + _GOOD_JSON_STR + "\n```"
    parsed = _parse_extraction_json(fenced)
    assert parsed["quant"]["direction"] == "down"


def test_parse_extraction_json_invalid_raises_value_error():
    with pytest.raises(ValueError):
        _parse_extraction_json("not a json {{{")


def test_validate_extraction_happy_path():
    _validate_extraction(json.loads(_GOOD_JSON_STR))  # 不抛即通过


def test_validate_extraction_missing_event():
    bad = copy.deepcopy(json.loads(_GOOD_JSON_STR))
    del bad["event"]
    with pytest.raises(ValueError, match="event"):
        _validate_extraction(bad)


def test_validate_extraction_wrong_chain_length():
    bad = copy.deepcopy(json.loads(_GOOD_JSON_STR))
    bad["mechanism_chain"] = ["a", "b"]
    with pytest.raises(ValueError, match="mechanism_chain"):
        _validate_extraction(bad)


def test_validate_extraction_empty_scope():
    bad = copy.deepcopy(json.loads(_GOOD_JSON_STR))
    bad["scope"] = {}
    with pytest.raises(ValueError, match="scope"):
        _validate_extraction(bad)
