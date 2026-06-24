"""P3 synthesizer: LLM-based narrative + conclusion composition."""

import json
import re
from typing import Any

from chat_bi_agent.agents.p3.prompts.synthesizer_extractor import (
    SYNTHESIZER_EXTRACTOR_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p3.prompts.synthesizer_narrator import (
    SYNTHESIZER_NARRATOR_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p3.prompts.synthesizer_system import SYNTHESIZER_SYSTEM_PROMPT
from chat_bi_agent.agents.p3.types import DrillResult, FactAnchor, MatchedEvent


def is_rca_question(fact_anchor: FactAnchor) -> bool:
    """RCA 题：有显著指标数值变化；设计题/无变化题走 legacy 旁路。"""
    if fact_anchor.change_pct is None:
        return False
    if abs(fact_anchor.change_pct) < 0.5:
        return False
    return True


CLOSE_PEER_THRESHOLD = 0.10  # 10pp


def _mark_close_peers(items: list[dict]) -> list[dict]:
    """给 share 距离 top1 不超过 10pp 的 item 打 is_peer=True。

    items 已按 share 降序，top1 自己也算 peer（gap=0）。
    """
    if not items:
        return items
    top_share = items[0].get("share", 0.0)
    return [
        {**it, "is_peer": (top_share - it.get("share", 0.0)) <= CLOSE_PEER_THRESHOLD}
        for it in items
    ]


def _build_user_prompt(
    question: str,
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
) -> str:
    """Assemble the user message for the synthesizer."""
    lines: list[str] = []
    lines.append(f"【用户问题】\n{question}\n")
    lines.append("【事实锚定】")
    lines.append(f"指标：{fact_anchor.metric_name}")
    lines.append(f"时间窗口：{fact_anchor.time_window}")
    pct = "n/a" if fact_anchor.change_pct is None else f"{fact_anchor.change_pct:.2f}%"
    lines.append(f"当前值：{fact_anchor.current_value} | 环比：{pct} ({fact_anchor.direction})\n")

    lines.append("【维度下钻 TopN 贡献度】")
    if not drill_results:
        lines.append("(无可用下钻结果)")
    else:
        for dr in drill_results:
            if dr.skipped:
                lines.append(f"按 {dr.dimension}：(查询失败，已跳过)")
                continue
            marked = _mark_close_peers(dr.pareto_top_k)
            parts = []
            any_peer = False
            for item in marked:
                key = item.get("key")
                share = item.get("share", 0.0)
                star = " ★" if item.get("is_peer") else ""
                if star:
                    any_peer = True
                parts.append(f"{key} (贡献 {share * 100:.0f}%){star}")
            lines.append(f"按 {dr.dimension}：" + ", ".join(parts))
            if any_peer:
                lines.append(
                    "  说明：★ 标记的是并列贡献者（与 Top1 差距 ≤ 10pp），"
                    "分析时必须全部纳入 scope。"
                )
    lines.append("")

    lines.append("【可能相关事件（来自行内事件库）】")
    if not matched_events:
        lines.append("(本时间窗口没有匹配到事件库中的已知事件)")
    else:
        for ev in matched_events:
            lines.append(f"- {ev.event_id} ({ev.effective_date}): {ev.event_name}")

    return "\n".join(lines)


_NARRATIVE_TAG = re.compile(r"【\s*叙述\s*】")
_CONCLUSION_TAG = re.compile(r"【\s*结论\s*】")


def _parse_dual_output(content: str) -> tuple[str, str]:
    """Split LLM output into (narrative, conclusion) by tags.

    On parse failure returns (content, "") so the caller can apply its own
    conclusion fallback.
    """
    nar_match = _NARRATIVE_TAG.search(content)
    concl_match = _CONCLUSION_TAG.search(content)
    if not concl_match:
        return content.strip(), ""

    narrative_start = nar_match.end() if nar_match else 0
    narrative = content[narrative_start : concl_match.start()].strip()
    conclusion = content[concl_match.end() :].strip()
    return narrative, conclusion


_EXTRACTION_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_extraction_json(raw: str) -> dict:
    """剥 markdown fence 后 json.loads。失败抛 ValueError。"""
    if not raw or not raw.strip():
        raise ValueError("empty extractor output")
    text = raw.strip()
    m = _EXTRACTION_FENCE_RE.search(text)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"extractor JSON parse failed: {e}") from e


_REQUIRED_EXTRACTION_KEYS = ("event", "quant", "mechanism_chain", "scope")


def _validate_extraction(ext: dict) -> None:
    """字段齐全性校验。失败抛 ValueError(field_name)。"""
    for k in _REQUIRED_EXTRACTION_KEYS:
        if k not in ext:
            raise ValueError(f"missing field: {k}")
    quant = ext.get("quant")
    if not isinstance(quant, dict) or "current_value" not in quant or "pop_pct" not in quant:
        raise ValueError("quant must include both current_value and pop_pct")
    chain = ext.get("mechanism_chain")
    if not isinstance(chain, list) or len(chain) != 3:
        raise ValueError(f"mechanism_chain must have exactly 3 items, got {chain}")
    scope = ext.get("scope")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("scope must be a non-empty dict")


def _fallback_narrative(
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
) -> str:
    top_keys = []
    for dr in drill_results:
        if dr.skipped or not dr.pareto_top_k:
            continue
        top_keys.append(f"{dr.dimension}={dr.pareto_top_k[0].get('key')}")

    parts = [
        f"指标 {fact_anchor.metric_name} 在 {fact_anchor.time_window} 期间发生变化"
        f"（方向：{fact_anchor.direction}）。"
    ]
    if top_keys:
        parts.append(f"主要贡献维度：{', '.join(top_keys)}。")
    if matched_events:
        names = ", ".join(ev.event_name for ev in matched_events[:2])
        parts.append(f"同期相关事件：{names}。")
    return "".join(parts)


def _fallback_conclusion(
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
) -> str:
    top_dim = next(
        (
            f"{dr.dimension}={dr.pareto_top_k[0].get('key')}"
            for dr in drill_results
            if not dr.skipped and dr.pareto_top_k
        ),
        None,
    )
    event_name = matched_events[0].event_name if matched_events else None
    if event_name and top_dim:
        return f"{fact_anchor.metric_name} 的变化主要由「{event_name}」驱动，集中于 {top_dim}。"
    if event_name:
        return f"{fact_anchor.metric_name} 的变化主要与「{event_name}」相关。"
    if top_dim:
        return f"{fact_anchor.metric_name} 的变化主要集中于 {top_dim}。"
    return (
        f"{fact_anchor.metric_name} 在 {fact_anchor.time_window} 期间出现 "
        f"{fact_anchor.direction} 方向变化，未识别到明确根因。"
    )


def _template_narrative_from_extraction(ext: dict) -> str:
    """Pass 2 失败时的模板 narrative：保证 4 要素全部出现。"""
    event_name = ext["event"]["name"]
    quant = ext["quant"]
    metric = quant["metric_name"]
    current = quant["current_value"]
    pop = quant["pop_pct"]
    window = quant["window"]
    chain = " → ".join(ext["mechanism_chain"])
    scope_parts = [f"{dim}={','.join(str(v) for v in vals)}" for dim, vals in ext["scope"].items()]
    scope_text = "; ".join(scope_parts) if scope_parts else "全行口径"
    return (
        f"受「{event_name}」影响，{metric} 在 {window} 期间当前值 {current}，"
        f"环比 {pop:+.1f}%。传导路径：{chain}。"
        f"影响范围集中于 {scope_text}。"
    )


def _template_conclusion_from_extraction(ext: dict) -> str:
    """Pass 2 失败时的模板 conclusion：点名 event 与首要 scope。"""
    event_name = ext["event"]["name"]
    scope = ext.get("scope") or {}
    main_scope = next(iter(scope.items()), None)
    if main_scope:
        dim, vals = main_scope
        return (
            f"本期变化主要由「{event_name}」驱动，集中于 {dim}={','.join(str(v) for v in vals)}。"
        )
    return f"本期变化主要与「{event_name}」相关。"


def _synthesize_legacy(
    question: str,
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
    llm_client: Any,
) -> tuple[str, str]:
    """原单次调用 dual-output 路径。设计题与 RCA 两段式失败回退共用。"""
    user_prompt = _build_user_prompt(question, fact_anchor, drill_results, matched_events)
    try:
        result = llm_client.chat(
            system_prompt=SYNTHESIZER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        narrative, conclusion = _parse_dual_output(result.content)
        if not conclusion:
            conclusion = _fallback_conclusion(fact_anchor, drill_results, matched_events)
        return narrative, conclusion
    except Exception:
        return (
            _fallback_narrative(fact_anchor, drill_results, matched_events),
            _fallback_conclusion(fact_anchor, drill_results, matched_events),
        )


def _synthesize_rca_two_pass(
    question: str,
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
    llm_client: Any,
) -> tuple[str, str]:
    """RCA 题两段式：Pass 1 抽 JSON → Pass 2 翻自然语言。

    Pass 1 失败 → 回退 legacy。Pass 2 失败 → 用模板兜底。
    """
    user_prompt = _build_user_prompt(question, fact_anchor, drill_results, matched_events)

    # Pass 1: 抽取 JSON
    try:
        pass1_result = llm_client.chat(
            system_prompt=SYNTHESIZER_EXTRACTOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        extraction = _parse_extraction_json(pass1_result.content)
        _validate_extraction(extraction)
    except Exception as e:
        print(f"[synthesizer] Pass 1 failed ({type(e).__name__}: {e}); fallback to legacy")
        return _synthesize_legacy(question, fact_anchor, drill_results, matched_events, llm_client)

    # Pass 2: 翻自然语言
    pass2_user_prompt = (
        f"【用户问题】\n{question}\n\n"
        f"【已抽取的 RCA 要素 JSON】\n```json\n"
        f"{json.dumps(extraction, ensure_ascii=False, indent=2)}\n```"
    )
    try:
        pass2_result = llm_client.chat(
            system_prompt=SYNTHESIZER_NARRATOR_SYSTEM_PROMPT,
            user_prompt=pass2_user_prompt,
        )
        narrative, conclusion = _parse_dual_output(pass2_result.content)
        if not narrative or not conclusion:
            print("[synthesizer] Pass 2 tag parse failed; fallback to template")
            return (
                _template_narrative_from_extraction(extraction),
                _template_conclusion_from_extraction(extraction),
            )
        return narrative, conclusion
    except Exception as e:
        print(f"[synthesizer] Pass 2 failed ({type(e).__name__}: {e}); fallback to template")
        return (
            _template_narrative_from_extraction(extraction),
            _template_conclusion_from_extraction(extraction),
        )


def synthesize(
    question: str,
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
    llm_client: Any,
) -> tuple[str, str]:
    """Compose (narrative, conclusion) via two-pass (RCA) or single-pass (design).

    `llm_client` must expose `.chat(system_prompt: str, user_prompt: str) -> ChatResult-like`
    (i.e. an object with a `.content: str` attribute).
    """
    if is_rca_question(fact_anchor):
        return _synthesize_rca_two_pass(
            question, fact_anchor, drill_results, matched_events, llm_client
        )
    return _synthesize_legacy(question, fact_anchor, drill_results, matched_events, llm_client)
