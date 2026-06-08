"""Inject prior-step results into a step's question as a text appendix.

The P1 NL2SQL agent interface (run(question_id, question)) is intentionally
unchanged. P2 packs prior-step data into the question string itself; P1's
SchemaLinker treats it as part of the query intent.

Kept short and deliberately string-only: structured context passing is a V2
goal (see spec §3 D9).
"""

import json

from chat_bi_agent.agents.p2.types import PlanStep, StepResult

MAX_ROWS_INLINED = 30


def inject_context(
    step: PlanStep,
    prior_results: dict[str, StepResult],
) -> str:
    """Return step.question, optionally appended with a `背景信息` block built
    from prior step results referenced by step.context_keys.

    context_keys use dotted paths like "step1.rows.customer_ids" — meaning
    "from step1 use the customer_ids field of rows". For MVP we just dump the
    relevant rows (truncated) and let the LLM figure out what to use.
    """
    if not step.context_keys:
        return step.question

    referenced_step_ids: set[str] = set()
    for key in step.context_keys:
        head = key.split(".", 1)[0]
        referenced_step_ids.add(head)

    context_parts: list[str] = []
    for sid in sorted(referenced_step_ids):
        prior = prior_results.get(sid)
        if prior is None or prior.rows is None:
            continue
        total = len(prior.rows)
        preview = prior.rows[:MAX_ROWS_INLINED]
        preview_json = json.dumps(preview, ensure_ascii=False, default=str)
        suffix = (
            f"（共 {total} 行，仅展示前 {len(preview)} 行）"
            if total > len(preview)
            else ""
        )
        context_parts.append(
            f"- 来自 {sid} 的 rows{suffix}: {preview_json}"
        )

    if not context_parts:
        return step.question

    return (
        f"{step.question}\n\n"
        f"背景信息（来自前置步骤的结果，用于本步参考）：\n"
        + "\n".join(context_parts)
    )
