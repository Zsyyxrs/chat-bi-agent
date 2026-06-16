"""P3 RCA data structures. No LLM/IO dependencies."""

from dataclasses import dataclass, field
from typing import Literal

from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass


@dataclass
class FactAnchor:
    metric_name: str
    time_window: str
    current_value: float
    prior_value: float | None
    change_pct: float | None
    direction: Literal["up", "down", "flat"]
    sql: str
    rows: list[dict]


@dataclass
class DrillRequest:
    dimension: str
    nl_question: str


@dataclass
class DrillResult:
    dimension: str
    nl_question: str
    sql: str
    rows: list[dict]
    pareto_top_k: list[dict]
    error_class: SQLErrorClass | None = None
    skipped: bool = False


@dataclass
class MatchedEvent:
    event_id: str
    event_name: str
    effective_date: str
    relevance: str


@dataclass
class RCAReport:
    question_id: str
    question: str
    fact_anchor: FactAnchor | None
    drill_results: list[DrillResult] = field(default_factory=list)
    matched_events: list[MatchedEvent] = field(default_factory=list)
    narrative: str = ""
    conclusion: str = ""
    trace_id: str | None = None
    latency_ms: int = 0
    error: str | None = None

    def to_eval_input(self) -> dict:
        """Map to RCAEvaluator.evaluate_response kwargs.

        See src/chat_bi_agent/eval/rca_evaluator.py — accepts:
          (question_id, agent_response, agent_extracted_dimensions=None,
           agent_identified_event=None, agent_conclusion="")
        """
        dims: dict[str, list[str]] = {}
        for dr in self.drill_results:
            if dr.skipped:
                continue
            values = [
                str(item.get("key")) for item in dr.pareto_top_k if item.get("key") is not None
            ]
            if values:
                dims.setdefault(dr.dimension, []).extend(values)

        identified_event = self.matched_events[0].event_id if self.matched_events else None

        return {
            "question_id": self.question_id,
            "agent_response": self.narrative,
            "agent_extracted_dimensions": dims,
            "agent_identified_event": identified_event,
            "agent_conclusion": self.conclusion,
        }
