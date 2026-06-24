"""P3 synthesizer: LLM-based narrative + conclusion composition."""

import re
from typing import Any

from chat_bi_agent.agents.p3.prompts.synthesizer_system import SYNTHESIZER_SYSTEM_PROMPT
from chat_bi_agent.agents.p3.types import DrillResult, FactAnchor, MatchedEvent


def is_rca_question(fact_anchor: FactAnchor) -> bool:
    """RCA 题：有显著指标数值变化；设计题/无变化题走 legacy 旁路。"""
    if fact_anchor.change_pct is None:
        return False
    if abs(fact_anchor.change_pct) < 0.5:
        return False
    return True


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
            parts = []
            for item in dr.pareto_top_k:
                key = item.get("key")
                share = item.get("share", 0.0)
                parts.append(f"{key} (贡献 {share * 100:.0f}%)")
            lines.append(f"按 {dr.dimension}：" + ", ".join(parts))
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


def synthesize(
    question: str,
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
    llm_client: Any,
) -> tuple[str, str]:
    """Compose (narrative, conclusion) via a single LLM call. Falls back on error.

    `llm_client` must expose `.chat(system_prompt: str, user_prompt: str) -> ChatResult-like`
    (i.e. an object with a `.content: str` attribute).
    """
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
