"""P2MultiStepAnalysisAgent: orchestrates Planner → Executor → 3-stage Synthesis."""

import time

from langfuse import observe

from chat_bi_agent.agents.p1_nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.p2.context_injector import inject_context
from chat_bi_agent.agents.p2.fact_extractor import FactExtractor
from chat_bi_agent.agents.p2.insight_synthesizer import InsightSynthesizer
from chat_bi_agent.agents.p2.planner import (
    Planner,
    PlanParseError,
    PlanValidationError,
    Replanner,
)
from chat_bi_agent.agents.p2.report_writer import ReportWriter
from chat_bi_agent.agents.p2.types import (
    AnalysisReport,
    P2Plan,
    PlanStep,
    StepResult,
)
from chat_bi_agent.agents.schema_linker import SchemaLinker
from chat_bi_agent.schema.loader import SchemaLoader


class P2MultiStepAnalysisAgent:
    """Plan-and-Execute multi-step analysis agent.

    Reuses P1NL2SQLAgent as the atomic SQL execution layer.
    """

    MAX_REPLAN = 1

    def __init__(
        self,
        p1_agent: P1NL2SQLAgent,
        schema_linker: SchemaLinker,
        loader: SchemaLoader,
        top_k: int = 8,
    ):
        self.p1 = p1_agent
        self.planner = Planner(schema_linker, loader, top_k)
        self.replanner = Replanner(schema_linker, loader, top_k)
        self.fact_extractor = FactExtractor()
        self.insight_synthesizer = InsightSynthesizer()
        self.report_writer = ReportWriter()

    @observe(name="p2_analysis_run")
    def run(self, question_id: str, question: str) -> AnalysisReport:
        t0 = time.perf_counter()

        plan = self.planner.plan(question)

        executed: list[StepResult] = []
        prior_results: dict[str, StepResult] = {}
        replan_count = 0
        i = 0

        while i < len(plan.steps):
            step = plan.steps[i]
            enriched = inject_context(step, prior_results)
            sub_qid = f"{question_id}__{step.id}"

            p1_result = self.p1.run(question_id=sub_qid, question=enriched)

            sr = _p1_result_to_step_result(step, p1_result)

            if not sr.skipped and sr.error_class is None:
                executed.append(sr)
                prior_results[step.id] = sr
                i += 1
                continue

            if replan_count < self.MAX_REPLAN:
                try:
                    new_remaining = self.replanner.replan(
                        original_plan=plan,
                        failed_at_index=i,
                        failed_step=step,
                        error_class=sr.error_class,
                        error_msg=sr.error_msg or "",
                        executed_steps=executed,
                    )
                except (PlanParseError, PlanValidationError):
                    sr.skipped = True
                    executed.append(sr)
                    i += 1
                    continue

                plan = P2Plan(
                    question=plan.question,
                    plan_type=plan.plan_type,
                    steps=plan.steps[:i] + new_remaining,
                )
                replan_count += 1
                continue

            sr.skipped = True
            executed.append(sr)
            i += 1

        facts = self.fact_extractor.extract(executed)
        insights = self.insight_synthesizer.synthesize(question, facts)
        final_answer = self.report_writer.write(question, plan, facts, insights)

        total_ms = (time.perf_counter() - t0) * 1000.0

        return AnalysisReport(
            question=question,
            question_id=question_id,
            plan=plan,
            step_results=executed,
            facts=facts,
            insights=insights,
            final_answer=final_answer,
            replan_count=replan_count,
            total_latency_ms=total_ms,
        )


def _p1_result_to_step_result(step: PlanStep, p1_result) -> StepResult:
    """Convert a P1AgentResult into a StepResult. A step is OK iff
    execution_error is None AND error_class is None."""
    ok = (p1_result.execution_error is None) and (p1_result.error_class is None)
    return StepResult(
        step=step,
        sql=p1_result.sql,
        rows=p1_result.rows if ok else None,
        error_class=p1_result.error_class,
        error_msg=p1_result.execution_error,
        skipped=False,
        latency_ms=float(p1_result.total_latency_ms),
    )
