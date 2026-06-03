"""InsightSynthesizer: Facts + question → list[Insight] via one LLM call."""

import json
import re
from dataclasses import asdict

from langfuse import observe

from chat_bi_agent.agents.p2.prompts.insight_synthesizer_system import (
    INSIGHT_SYNTHESIZER_SYSTEM_PROMPT,
)
from chat_bi_agent.agents.p2.types import Fact, Insight
from chat_bi_agent.llm import qwen_client

JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class InsightParseError(Exception):
    pass


class InsightSynthesizer:
    """Synthesize business insights from extracted Facts."""

    @observe(name="p2_insight_synthesizer")
    def synthesize(self, question: str, facts: list[Fact]) -> list[Insight]:
        if not facts:
            return []
        user_prompt = self._build_user_prompt(question, facts)
        chat_result = qwen_client.chat(
            system_prompt=INSIGHT_SYNTHESIZER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return self._parse(chat_result.content)

    def _build_user_prompt(self, question: str, facts: list[Fact]) -> str:
        facts_json = json.dumps(
            [asdict(f) for f in facts], ensure_ascii=False, indent=2, default=str,
        )
        return (
            f"原始问题：{question}\n\n"
            f"已抽取的 Facts（带索引）：\n```json\n{facts_json}\n```\n\n"
            f"请综合得出 3-6 条 Insight，输出 JSON。"
        )

    def _parse(self, raw: str) -> list[Insight]:
        m = JSON_FENCE_RE.search(raw)
        candidate = m.group(1) if m else raw.strip()
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as e:
            raise InsightParseError(
                f"无法解析 JSON: {e}; raw 前 200 字符: {raw[:200]}"
            ) from e
        if "insights" not in data or not isinstance(data["insights"], list):
            raise InsightParseError(
                f"缺少 insights 列表; 实际: {list(data.keys())}"
            )
        result = []
        for i, ins in enumerate(data["insights"]):
            for required in ("statement", "supporting_facts", "confidence"):
                if required not in ins:
                    raise InsightParseError(
                        f"insight[{i}] 缺少字段 {required!r}"
                    )
            result.append(Insight(
                statement=ins["statement"],
                supporting_facts=ins["supporting_facts"],
                confidence=ins["confidence"],
            ))
        return result
