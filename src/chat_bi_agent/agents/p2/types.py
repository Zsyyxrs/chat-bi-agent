"""P2 data structures. No LLM/IO dependencies — purely typed records."""

from dataclasses import dataclass, field
from typing import Literal

from chat_bi_agent.agents.sql_executor import SQLErrorClass


@dataclass
class PlanStep:
    id: str
    question: str
    rationale: str
    depends_on: list[str] = field(default_factory=list)
    context_keys: list[str] = field(default_factory=list)
    expected_metrics: list[str] = field(default_factory=list)


@dataclass
class P2Plan:
    question: str
    plan_type: str
    steps: list[PlanStep]


@dataclass
class StepResult:
    step: PlanStep
    sql: str | None
    rows: list[dict] | None
    error_class: SQLErrorClass | None
    error_msg: str | None
    skipped: bool = False
    latency_ms: float = 0.0


@dataclass
class Fact:
    metric: str
    dimension: dict
    value: float | int | str
    source_step: str


@dataclass
class Insight:
    statement: str
    supporting_facts: list[int]
    confidence: Literal["high", "medium", "low"]


@dataclass
class AnalysisReport:
    question: str
    question_id: str
    plan: P2Plan
    step_results: list[StepResult]
    facts: list[Fact]
    insights: list[Insight]
    final_answer: str
    replan_count: int = 0
    total_latency_ms: float = 0.0

    def to_eval_input(self) -> dict:
        """Map this report into the dict shape expected by
        `MultiStepAnalysisEvaluator.evaluate_response()`."""
        return {
            "question_id": self.question_id,
            "agent_response": self.final_answer,
            "mentioned_steps": [
                s.step.rationale for s in self.step_results if not s.skipped
            ],
            "mentioned_metrics": sorted({f.metric for f in self.facts}),
            "extracted_insights": [i.statement for i in self.insights],
        }
