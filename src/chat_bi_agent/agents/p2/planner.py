"""P2 Planner: question → P2Plan via single LLM call with JSON output."""

import json
import re

from langfuse import observe

from chat_bi_agent.agents.p2.prompts.planner_few_shots import FEW_SHOTS
from chat_bi_agent.agents.p2.prompts.planner_system import PLANNER_SYSTEM_PROMPT
from chat_bi_agent.agents.p2.types import P2Plan, PlanStep
from chat_bi_agent.agents.schema_linker import SchemaLinker
from chat_bi_agent.llm import qwen_client
from chat_bi_agent.schema.loader import SchemaLoader

MIN_STEPS = 2
MAX_STEPS = 8

JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

REQUIRED_STEP_FIELDS = (
    "id", "question", "rationale", "depends_on", "context_keys", "expected_metrics",
)


class PlanParseError(Exception):
    """LLM output could not be parsed as the expected JSON Plan."""


class PlanValidationError(Exception):
    """Parsed Plan failed structural validation (step count, missing fields)."""


def _format_few_shots() -> str:
    parts = []
    for i, ex in enumerate(FEW_SHOTS, start=1):
        plan_json = json.dumps(ex["plan_json"], ensure_ascii=False, indent=2)
        parts.append(
            f"示例 {i}：\n问题：{ex['question']}\n输出：\n```json\n{plan_json}\n```"
        )
    return "\n\n".join(parts)


class Planner:
    """Generate a P2Plan from a complex analytical question."""

    def __init__(
        self,
        schema_linker: SchemaLinker,
        loader: SchemaLoader,
        top_k: int = 8,
    ):
        self.schema_linker = schema_linker
        self.loader = loader
        self.top_k = top_k

    @observe(name="p2_planner")
    def plan(self, question: str) -> P2Plan:
        matches = self.schema_linker.link(question)
        top_names = [m.name for m in matches[: self.top_k]]
        schema_ddl = "\n\n".join(self.loader.get_ddl_text(n) for n in top_names)

        user_prompt = self._build_user_prompt(question, schema_ddl)

        chat_result = qwen_client.chat(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        parsed = self._parse(chat_result.content)
        self._validate(parsed)

        steps = [
            PlanStep(
                id=s["id"],
                question=s["question"],
                rationale=s["rationale"],
                depends_on=s["depends_on"],
                context_keys=s["context_keys"],
                expected_metrics=s["expected_metrics"],
            )
            for s in parsed["steps"]
        ]
        return P2Plan(
            question=question,
            plan_type=parsed["plan_type"],
            steps=steps,
        )

    def _build_user_prompt(self, question: str, schema_ddl: str) -> str:
        few_shots = _format_few_shots()
        return (
            f"可用 schema（top {self.top_k} 表）：\n\n{schema_ddl}\n\n"
            f"Few-shot 示例：\n{few_shots}\n\n"
            f"用户问题：{question}\n\n请输出 JSON Plan。"
        )

    def _parse(self, raw: str) -> dict:
        m = JSON_FENCE_RE.search(raw)
        candidate = m.group(1) if m else raw.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise PlanParseError(
                f"无法解析为 JSON: {e}; raw 前 200 字符: {raw[:200]}"
            ) from e

    def _validate(self, parsed: dict) -> None:
        if "plan_type" not in parsed or "steps" not in parsed:
            raise PlanValidationError(
                f"缺少顶层字段; 实际: {list(parsed.keys())}"
            )
        steps = parsed["steps"]
        if not isinstance(steps, list):
            raise PlanValidationError("steps 必须是 list")
        if len(steps) < MIN_STEPS or len(steps) > MAX_STEPS:
            raise PlanValidationError(
                f"steps 数 {len(steps)} 越界 [{MIN_STEPS}, {MAX_STEPS}]"
            )
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                raise PlanValidationError(f"step[{i}] 不是 dict")
            for f in REQUIRED_STEP_FIELDS:
                if f not in s:
                    raise PlanValidationError(
                        f"step[{i}] 缺少字段 {f!r}; 实际: {list(s.keys())}"
                    )
