"""P3 synthesizer: LLM-based narrative composition from facts + drill + events."""

from typing import Any

from chat_bi_agent.agents.p3.prompts.synthesizer_system import SYNTHESIZER_SYSTEM_PROMPT
from chat_bi_agent.agents.p3.types import DrillResult, FactAnchor, MatchedEvent


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
    lines.append(
        f"当前值：{fact_anchor.current_value} | 环比：{pct} ({fact_anchor.direction})\n"
    )

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
    lines.append("")

    lines.append("【输出要求】")
    lines.append("- 引用具体数字（不要编造），数字必须来自上面提供的事实")
    lines.append("- 引用维度 ID（如 BR_CITY_0006、HIGH_NET_WORTH）和事件名")
    lines.append("- 解释因果链条（事件 → 数据变化 → 业务影响）")
    lines.append("- 控制在 5-8 句话连贯叙述")

    return "\n".join(lines)


def _fallback_narrative(
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
) -> str:
    """Template fallback when LLM call fails."""
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


def synthesize_narrative(
    question: str,
    fact_anchor: FactAnchor,
    drill_results: list[DrillResult],
    matched_events: list[MatchedEvent],
    llm_client: Any,
) -> str:
    """Compose business-language narrative via LLM. Falls back to a template on error.

    `llm_client` must expose `.chat(system_prompt: str, user_prompt: str) -> ChatResult-like`
    (i.e. an object with a `.content: str` attribute). Pass the `qwen_client` module
    at the call site.
    """
    user_prompt = _build_user_prompt(question, fact_anchor, drill_results, matched_events)
    try:
        result = llm_client.chat(
            system_prompt=SYNTHESIZER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return result.content
    except Exception:
        return _fallback_narrative(fact_anchor, drill_results, matched_events)
