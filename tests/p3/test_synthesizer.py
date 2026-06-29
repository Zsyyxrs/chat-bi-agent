"""Tests for synthesizer (LLM with mocked client)."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_bi_agent.agents.p3.prompts.synthesizer_extractor import (
    SYNTHESIZER_EXTRACTOR_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p3.prompts.synthesizer_narrator import (
    SYNTHESIZER_NARRATOR_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p3.synthesizer import (
    CLOSE_PEER_THRESHOLD,
    _build_user_prompt,
    _mark_close_peers,
    _parse_dual_output,
    _parse_extraction_json,
    _synthesize_rca_two_pass,
    _template_conclusion_from_extraction,
    _template_narrative_from_extraction,
    _validate_extraction,
    is_rca_question,
    synthesize,
)
from chat_bi_agent.agents.p3.types import (
    DrillResult,
    FactAnchor,
    MatchedEvent,
)


def _fact_anchor() -> FactAnchor:
    """共享 fixture：保持 change_pct=None 让原 7 个 synthesize 测试走 legacy 路径。"""
    return FactAnchor(
        metric_name="retail_deposit_balance",
        time_window="2026-05-01 to 2026-05-20",
        current_value=92.0,
        prior_value=100.0,
        change_pct=None,
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


def test_build_user_prompt_lists_pinned_entities_from_question():
    # q001 案例：题面把 BR_CITY_0006 写在原文里，drill 不会再拿它做下钻维度，
    # narrator 容易漏 echo。user_prompt 必须列出题面已固定的实体提醒 narrator 复述。
    prompt = _build_user_prompt(
        question="上海浦东分行（BR_CITY_0006）的高净值客户活期存款余额在五月中旬下降",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[],
    )
    assert "【题面已固定实体】" in prompt
    assert "BR_CITY_0006" in prompt.split("【题面已固定实体】")[1].split("\n")[0]
    assert "echo" in prompt or "复述" in prompt or "字面值" in prompt


def test_build_user_prompt_omits_pinned_section_when_no_codes():
    # 题面无编码型实体（自然语言提问）→ 不加该段，避免噪声
    prompt = _build_user_prompt(
        question="为什么 2 月中旬现金支取量上升？",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[],
    )
    assert "【题面已固定实体】" not in prompt


def test_build_user_prompt_pinned_entities_capture_multiple_types():
    # 多种编码混合（branch + product + tier）都应被捕获
    prompt = _build_user_prompt(
        question="MASS 客群在 BR_CITY_0000 分行对 PROD_DEP_0008 的认购上升",
        fact_anchor=_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[],
    )
    pinned_line = prompt.split("【题面已固定实体】")[1].split("\n")[0]
    assert "BR_CITY_0000" in pinned_line
    assert "PROD_DEP_0008" in pinned_line
    assert "MASS" in pinned_line


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


def test_close_peer_threshold_is_10pp():
    assert CLOSE_PEER_THRESHOLD == 0.10


def test_mark_close_peers_within_threshold():
    items = [
        {"key": "MASS", "share": 0.40},
        {"key": "AFFLUENT", "share": 0.32},
        {"key": "BASIC", "share": 0.31},
    ]
    out = _mark_close_peers(items)
    assert out[0]["is_peer"] is True  # top1 自己
    assert out[1]["is_peer"] is True  # gap 8pp ≤ 10pp
    assert out[2]["is_peer"] is True  # gap 9pp ≤ 10pp


def test_mark_close_peers_outside_threshold():
    items = [
        {"key": "MASS", "share": 0.40},
        {"key": "AFFLUENT", "share": 0.32},
        {"key": "BASIC", "share": 0.15},
    ]
    out = _mark_close_peers(items)
    assert out[0]["is_peer"] is True
    assert out[1]["is_peer"] is True  # gap 8pp ≤ 10pp
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

_GOOD_JSON_STR = """{
  "event": {"id": "anxin_90_expire", "name": "安鑫 90 天到期"},
  "quant": {"metric_name": "AUM", "metric_name_zh": "管理资产规模",
            "current_value": 80000000.0, "current_value_display": "8000 万元",
            "pop_pct": -20.9,
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


def test_validate_extraction_missing_metric_name_zh():
    bad = copy.deepcopy(json.loads(_GOOD_JSON_STR))
    del bad["quant"]["metric_name_zh"]
    with pytest.raises(ValueError, match="metric_name_zh"):
        _validate_extraction(bad)


def test_validate_extraction_missing_current_value_display():
    bad = copy.deepcopy(json.loads(_GOOD_JSON_STR))
    del bad["quant"]["current_value_display"]
    with pytest.raises(ValueError, match="current_value_display"):
        _validate_extraction(bad)


def test_validate_extraction_missing_pop_pct():
    bad = copy.deepcopy(json.loads(_GOOD_JSON_STR))
    del bad["quant"]["pop_pct"]
    with pytest.raises(ValueError, match="pop_pct"):
        _validate_extraction(bad)


# ============================================================
# Task 4: Pass 2 narrator — prompt + template fallback
# ============================================================


def test_narrator_prompt_mentions_hard_constraints():
    p = SYNTHESIZER_NARRATOR_SYSTEM_PROMPT
    assert "【叙述】" in p
    assert "【结论】" in p
    assert "event.name" in p or "event_name" in p
    assert "mechanism_chain" in p
    assert "scope" in p


def test_template_narrative_contains_all_four_elements():
    ext = json.loads(_GOOD_JSON_STR)
    nar = _template_narrative_from_extraction(ext)
    assert "安鑫 90 天到期" in nar
    assert "管理资产规模" in nar  # metric_name_zh
    assert "8000 万元" in nar  # current_value_display
    assert "-20.9" in nar  # pop_pct
    assert "2026-05-14" in nar
    for seg in ext["mechanism_chain"]:
        assert seg in nar
    assert "BR_CITY_0006" in nar
    assert "HIGH_NET_WORTH" in nar


def test_template_conclusion_contains_event_and_scope():
    ext = json.loads(_GOOD_JSON_STR)
    concl = _template_conclusion_from_extraction(ext)
    assert "安鑫 90 天到期" in concl
    assert "BR_CITY_0006" in concl or "branch_id" in concl


def test_template_narrative_handles_empty_event_id():
    ext = json.loads(_GOOD_JSON_STR)
    ext["event"] = {"id": None, "name": "未识别到事件库匹配"}
    nar = _template_narrative_from_extraction(ext)
    assert "未识别到事件库匹配" in nar
    assert "AUM" in nar


# ============================================================
# Task 5: orchestration — _synthesize_rca_two_pass + synthesize() dispatch
# ============================================================

_PASS1_GOOD = SimpleNamespace(content="```json\n" + _GOOD_JSON_STR + "\n```")
_PASS2_GOOD = SimpleNamespace(
    content=(
        "【叙述】\n受「安鑫 90 天到期」影响，AUM 在 2026-05-14 to 2026-05-20 "
        "期间下降 -20.9%。资金回流到客户账户，HIGH_NET_WORTH 选择不续作，"
        "AUM 集中下降。影响范围集中于 BR_CITY_0006 与 HIGH_NET_WORTH。\n"
        "【结论】\n本期变化主要由「安鑫 90 天到期」驱动，集中于 BR_CITY_0006。"
    )
)


def _rca_fact_anchor() -> FactAnchor:
    return FactAnchor(
        metric_name="AUM",
        time_window="2026-05-14 to 2026-05-20",
        current_value=80.0,
        prior_value=100.0,
        change_pct=-20.9,
        direction="down",
        sql="",
        rows=[],
    )


def test_synthesize_rca_two_pass_happy_path():
    fake = MagicMock()
    fake.chat.side_effect = [_PASS1_GOOD, _PASS2_GOOD]
    nar, concl = _synthesize_rca_two_pass(
        question="why?",
        fact_anchor=_rca_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert fake.chat.call_count == 2
    assert "安鑫 90 天到期" in nar
    assert "BR_CITY_0006" in nar
    assert "安鑫 90 天到期" in concl


def test_synthesize_rca_pass1_invalid_json_falls_back_to_legacy():
    fake = MagicMock()
    fake.chat.side_effect = [
        SimpleNamespace(content="not a json"),
        SimpleNamespace(content="【叙述】\nlegacy narrative\n【结论】\nlegacy concl"),
    ]
    nar, concl = _synthesize_rca_two_pass(
        question="why?",
        fact_anchor=_rca_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert fake.chat.call_count == 2  # Pass 1 + legacy 各 1 次
    assert "legacy narrative" in nar


def test_synthesize_rca_pass1_missing_field_falls_back_to_legacy():
    bad_json = json.dumps({"event": {"id": None, "name": "x"}})  # 缺 quant/chain/scope
    fake = MagicMock()
    fake.chat.side_effect = [
        SimpleNamespace(content=bad_json),
        SimpleNamespace(content="【叙述】\nlegacy\n【结论】\nL"),
    ]
    nar, _ = _synthesize_rca_two_pass(
        question="q",
        fact_anchor=_rca_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert "legacy" in nar


def test_synthesize_rca_pass1_llm_exception_falls_back_to_legacy():
    fake = MagicMock()
    fake.chat.side_effect = [
        RuntimeError("Pass 1 LLM down"),
        SimpleNamespace(content="【叙述】\nlegacy\n【结论】\nL"),
    ]
    nar, _ = _synthesize_rca_two_pass(
        question="q",
        fact_anchor=_rca_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert "legacy" in nar


def test_synthesize_rca_pass2_exception_uses_template():
    fake = MagicMock()
    fake.chat.side_effect = [_PASS1_GOOD, RuntimeError("Pass 2 LLM down")]
    nar, concl = _synthesize_rca_two_pass(
        question="q",
        fact_anchor=_rca_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert "安鑫 90 天到期" in nar
    assert "AUM" in nar
    assert "-20.9" in nar
    for seg in json.loads(_GOOD_JSON_STR)["mechanism_chain"]:
        assert seg in nar
    assert "安鑫 90 天到期" in concl


def test_synthesize_rca_pass2_untagged_uses_template():
    fake = MagicMock()
    fake.chat.side_effect = [
        _PASS1_GOOD,
        SimpleNamespace(content="一段没有 tag 的文本"),
    ]
    nar, _ = _synthesize_rca_two_pass(
        question="q",
        fact_anchor=_rca_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert "安鑫 90 天到期" in nar
    assert "AUM" in nar


def test_synthesize_design_question_uses_legacy_path():
    """change_pct=None 的设计题：legacy 单次调用。"""
    design_anchor = FactAnchor(
        metric_name="term_days",
        time_window="2026-05-01 to 2026-05-31",
        current_value=360.0,
        prior_value=360.0,
        change_pct=None,
        direction="flat",
        sql="",
        rows=[],
    )
    fake = MagicMock()
    fake.chat.return_value = SimpleNamespace(
        content="【叙述】\n设计题 narrative\n【结论】\n设计题结论"
    )
    nar, concl = synthesize(
        question="如何设计预警模型",
        fact_anchor=design_anchor,
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert fake.chat.call_count == 1
    assert nar == "设计题 narrative"
    assert "设计题结论" in concl


def test_synthesize_rca_question_dispatches_to_two_pass():
    """change_pct=-20.9 的 RCA 题：两段式 2 次调用。"""
    fake = MagicMock()
    fake.chat.side_effect = [_PASS1_GOOD, _PASS2_GOOD]
    nar, concl = synthesize(
        question="why dropped?",
        fact_anchor=_rca_fact_anchor(),
        drill_results=[_drill()],
        matched_events=[_event()],
        llm_client=fake,
    )
    assert fake.chat.call_count == 2
    assert "安鑫 90 天到期" in nar
