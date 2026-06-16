"""FactExtractor: turn StepResult rows into structured Fact list via one LLM call."""

import json
import re

from langfuse import observe

from chat_bi_agent.agents.p2.prompts.fact_extractor_system import (
    FACT_EXTRACTOR_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p2.types import Fact, StepResult
from chat_bi_agent.llm import qwen_client

JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

MAX_ROWS_PER_STEP = 20


class FactParseError(Exception):
    """LLM output could not be parsed as the expected Facts JSON."""


class FactExtractor:
    """Extract structured Facts from executed step results."""

    @observe(name="p2_fact_extractor")
    def extract(self, step_results: list[StepResult]) -> list[Fact]:
        usable = [s for s in step_results if not s.skipped]
        if not usable:
            return []

        user_prompt = self._build_user_prompt(usable)
        chat_result = qwen_client.chat(
            system_prompt=FACT_EXTRACTOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return self._parse(chat_result.content)

    def _build_user_prompt(self, usable: list[StepResult]) -> str:
        parts = []
        for sr in usable:
            rows_preview = (sr.rows or [])[:MAX_ROWS_PER_STEP]
            rows_json = json.dumps(rows_preview, ensure_ascii=False, default=str)
            parts.append(
                f"步骤 id: {sr.step.id}\n"
                f"目的: {sr.step.rationale}\n"
                f"SQL: {sr.sql or '<none>'}\n"
                f"前 {len(rows_preview)} 行 rows: {rows_json}\n"
                f"该 step 的 expected_metrics 提示: {sr.step.expected_metrics}"
            )
        body = "\n\n---\n\n".join(parts)
        return f"请从以下步骤结果中抽取 Facts：\n\n{body}\n\n输出 JSON。"

    def _parse(self, raw: str) -> list[Fact]:
        m = JSON_FENCE_RE.search(raw)
        candidate = m.group(1) if m else raw.strip()
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as e:
            raise FactParseError(f"无法解析 JSON: {e}; raw 前 200 字符: {raw[:200]}") from e
        if "facts" not in data or not isinstance(data["facts"], list):
            raise FactParseError(f"缺少 facts 列表; 实际: {list(data.keys())}")
        result = []
        for i, f in enumerate(data["facts"]):
            for required in ("metric", "dimension", "value", "source_step"):
                if required not in f:
                    raise FactParseError(f"fact[{i}] 缺少字段 {required!r}")
            result.append(
                Fact(
                    metric=f["metric"],
                    dimension=f["dimension"],
                    value=f["value"],
                    source_step=f["source_step"],
                )
            )
        return result
