"""P3 drilldown dimension selector: LLM emits NL sub-questions per dimension."""

import json
import re
from typing import Any

from chat_bi_agent.agents.p3.prompts.drilldown_selector_system import (
    DRILLDOWN_SELECTOR_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p3.types import DrillRequest, FactAnchor

DEFAULT_DIMS: list[str] = [
    "branch_id",
    "customer_tier",
    "product_id",
    "product_name",
    "transaction_type",
    "transaction_channel",
]

MIN_COUNT = 2
MAX_COUNT = 4

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def catalog_dims(metric_router: Any, metric_id: str | None) -> list[str] | None:
    """取该指标 dim_catalog 里声明过的维度作为下钻候选集。

    拿不到就返回 None，调用方退回 DEFAULT_DIMS——归因链路不该因为 catalog 查不到
    就整体崩掉（走 nl2sql 路径时本来就没有 metric_id）。

    比全局白名单强的地方：LLM 只能在该指标真实声明过的维度里选，选不出来是
    「没有合适维度」，而不是编一个该指标根本没有的维度让 P1 去硬凑 SQL。
    """
    if metric_router is None or not metric_id:
        return None
    catalog = getattr(metric_router, "catalog", None)
    if catalog is None:
        return None
    try:
        metric = catalog.get(metric_id)
    except Exception:
        return None
    dims = list(getattr(metric, "dim_catalog", {}) or {})
    return dims or None


def _parse_selector_json(raw: str) -> list[DrillRequest]:
    """Parse LLM output into DrillRequest list. Raises ValueError on malformed JSON."""
    if not raw:
        raise ValueError("empty LLM output")
    text = raw.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
    payload = json.loads(text)
    subs = payload.get("sub_questions", [])
    if not isinstance(subs, list):
        raise ValueError("sub_questions must be a list")
    out: list[DrillRequest] = []
    for item in subs:
        if not isinstance(item, dict):
            continue
        dim = item.get("dimension")
        nlq = item.get("nl_question")
        if isinstance(dim, str) and isinstance(nlq, str):
            out.append(DrillRequest(dimension=dim, nl_question=nlq))
    return out


def _fallback_requests(question: str, available_dims: list[str]) -> list[DrillRequest]:
    """Fixed-template fallback: first two whitelisted dimensions."""
    return [
        DrillRequest(dimension=dim, nl_question=f"按 {dim} 拆解：{question}")
        for dim in available_dims[:MIN_COUNT]
    ]


def _build_user_prompt(question: str, fact_anchor: FactAnchor, available_dims: list[str]) -> str:
    return (
        f"【用户原问题】\n{question}\n\n"
        f"【事实锚定】\n"
        f"指标: {fact_anchor.metric_name}\n"
        f"时间窗口: {fact_anchor.time_window}\n"
        f"事实 SQL:\n{fact_anchor.sql}\n\n"
        f"【可用维度白名单】\n{', '.join(available_dims)}\n"
    )


def select_drilldown_dims(
    question: str,
    fact_anchor: FactAnchor,
    llm_client: Any,
    available_dims: list[str] | None = None,
) -> list[DrillRequest]:
    """Ask LLM to emit 2-4 drill-down NL sub-questions; fall back to DEFAULT_DIMS[:2] on failure."""
    dims = available_dims if available_dims is not None else DEFAULT_DIMS

    try:
        chat_result = llm_client.chat(
            system_prompt=DRILLDOWN_SELECTOR_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(question, fact_anchor, dims),
        )
        parsed = _parse_selector_json(chat_result.content)
    except Exception:
        return _fallback_requests(question, dims)

    # Filter to whitelist
    whitelist = set(dims)
    filtered = [r for r in parsed if r.dimension in whitelist]

    # Pad to MIN_COUNT using DEFAULT_DIMS not already present
    if len(filtered) < MIN_COUNT:
        existing = {r.dimension for r in filtered}
        for dim in dims:
            if dim in existing:
                continue
            filtered.append(DrillRequest(dim, f"按 {dim} 拆解：{question}"))
            existing.add(dim)
            if len(filtered) >= MIN_COUNT:
                break

    # Truncate to MAX_COUNT
    return filtered[:MAX_COUNT]
