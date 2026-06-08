"""P2 Planner and Replanner: question → P2Plan via single LLM call with JSON output.

Planner builds the initial Plan; Replanner produces replacement remaining-steps
after a step failure. Both share JSON-fence parsing and structural validation.
"""

import json
import re

from langfuse import observe

from chat_bi_agent.agents.p2.prompts.planner_few_shots import FEW_SHOTS
from chat_bi_agent.agents.p2.prompts.planner_system import PLANNER_SYSTEM_PROMPT
from chat_bi_agent.agents.p2.prompts.replanner_system import REPLANNER_SYSTEM_PROMPT
from chat_bi_agent.agents.p2.types import P2Plan, PlanStep, StepResult
from chat_bi_agent.agents.shared.schema_linker import SchemaLinker
from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass
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


def _parse_plan_json(raw: str) -> dict:
    m = JSON_FENCE_RE.search(raw)
    candidate = m.group(1) if m else raw.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise PlanParseError(
            f"无法解析为 JSON: {e}; raw 前 200 字符: {raw[:200]}"
        ) from e


def _validate_plan_dict(parsed: dict) -> None:
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


def _validate_plan_structure(parsed: dict) -> None:
    """Validate top-level fields and per-step fields only (no step-count check).

    Used by Replanner where the count constraint applies to the combined total
    (executed + new), not to the new steps alone.
    """
    if "plan_type" not in parsed or "steps" not in parsed:
        raise PlanValidationError(
            f"缺少顶层字段; 实际: {list(parsed.keys())}"
        )
    steps = parsed["steps"]
    if not isinstance(steps, list):
        raise PlanValidationError("steps 必须是 list")
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise PlanValidationError(f"step[{i}] 不是 dict")
        for f in REQUIRED_STEP_FIELDS:
            if f not in s:
                raise PlanValidationError(
                    f"step[{i}] 缺少字段 {f!r}; 实际: {list(s.keys())}"
                )


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

        parsed = _parse_plan_json(chat_result.content)
        _validate_plan_dict(parsed)

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


class Replanner:
    """Generate replacement remaining-steps after a step failure."""

    def __init__(
        self,
        schema_linker: SchemaLinker,
        loader: SchemaLoader,
        top_k: int = 8,
    ):
        self.schema_linker = schema_linker
        self.loader = loader
        self.top_k = top_k

    @observe(name="p2_replanner")
    def replan(
        self,
        original_plan: P2Plan,
        failed_at_index: int,
        failed_step: PlanStep,
        error_class: SQLErrorClass,
        error_msg: str,
        executed_steps: list[StepResult],
    ) -> list[PlanStep]:
        matches = self.schema_linker.link(original_plan.question)
        top_names = [m.name for m in matches[: self.top_k]]
        schema_ddl = "\n\n".join(self.loader.get_ddl_text(n) for n in top_names)

        user_prompt = self._build_user_prompt(
            original_plan=original_plan,
            failed_at_index=failed_at_index,
            failed_step=failed_step,
            error_class=error_class,
            error_msg=error_msg,
            executed_steps=executed_steps,
            schema_ddl=schema_ddl,
        )

        chat_result = qwen_client.chat(
            system_prompt=REPLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        parsed = _parse_plan_json(chat_result.content)
        _validate_plan_structure(parsed)
        new_count = len(parsed["steps"])
        total = len(executed_steps) + new_count
        if total < MIN_STEPS or total > MAX_STEPS:
            raise PlanValidationError(
                f"executed({len(executed_steps)}) + new({new_count}) = {total} "
                f"越界 [{MIN_STEPS}, {MAX_STEPS}]"
            )

        return [
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

    def _build_user_prompt(
        self,
        original_plan: P2Plan,
        failed_at_index: int,
        failed_step: PlanStep,
        error_class: SQLErrorClass,
        error_msg: str,
        executed_steps: list[StepResult],
        schema_ddl: str,
    ) -> str:
        executed_summary = "\n".join(
            f"- {s.step.id} ({s.step.rationale}): "
            f"{'OK, ' + str(len(s.rows or [])) + ' rows' if not s.skipped else 'SKIPPED'}"
            for s in executed_steps
        ) or "（无已成功步骤）"
        return (
            f"原始用户问题：{original_plan.question}\n\n"
            f"原始 plan_type：{original_plan.plan_type}\n\n"
            f"已成功执行的步骤：\n{executed_summary}\n\n"
            f"失败的步骤：\n"
            f"  id: {failed_step.id}\n"
            f"  question: {failed_step.question}\n"
            f"  rationale: {failed_step.rationale}\n\n"
            f"失败位置：第 {failed_at_index + 1} 步（索引 {failed_at_index}）\n\n"
            f"失败原因：\n  error_class: {error_class.value}\n"
            f"  error_msg: {error_msg}\n\n"
            f"可用 schema（top {self.top_k}）：\n{schema_ddl}\n\n"
            f"请输出修正后的剩余步骤 JSON Plan。"
        )
