"""P3 Root Cause Analysis orchestrator: fixed 5-step pipeline."""

import time
from pathlib import Path
from typing import Any

from langfuse import observe

from chat_bi_agent.agents.p3.drill_executor import run_drill_down
from chat_bi_agent.agents.p3.drilldown_selector import select_drilldown_dims
from chat_bi_agent.agents.p3.event_matcher import match_events
from chat_bi_agent.agents.p3.fact_anchor import run_fact_anchor
from chat_bi_agent.agents.p3.synthesizer import synthesize_narrative
from chat_bi_agent.agents.p3.types import RCAReport


class P3RootCauseAnalysisAgent:
    """Fixed 5-step RCA pipeline.

    Steps:
      1. fact_anchor   — P1NL2SQLAgent → FactAnchor (or None → abort)
      2. select_drilldown_dims — LLM → list[DrillRequest] (fallback to DEFAULT_DIMS[:2])
      3. drill_down    — per dim: P1 + Pareto TopN; partial failures allowed
      4. match_events  — deterministic time-window overlap against events YAML
      5. synthesize    — LLM narrative (fallback to template on LLM error)
    """

    def __init__(
        self,
        p1_agent: Any,
        llm_client: Any,
        events_dir: Path,
    ):
        self.p1_agent = p1_agent
        self.llm_client = llm_client
        self.events_dir = events_dir

    @observe(name="p3_rca_run")
    def run(self, question_id: str, question: str) -> RCAReport:
        t0 = time.time()

        # Step 1: fact_anchor
        anchor = run_fact_anchor(
            question_id=question_id, question=question, p1_agent=self.p1_agent
        )
        if anchor is None:
            return RCAReport(
                question_id=question_id,
                question=question,
                fact_anchor=None,
                drill_results=[],
                matched_events=[],
                narrative="",
                trace_id=None,
                latency_ms=int((time.time() - t0) * 1000),
                error="fact_anchor failed: P1 returned no rows",
            )

        # Step 2: select drill-down dimensions
        requests = select_drilldown_dims(
            question=question,
            fact_anchor=anchor,
            llm_client=self.llm_client,
        )

        # Step 3: drill down per dimension
        drill_results = run_drill_down(
            question_id=question_id,
            requests=requests,
            p1_agent=self.p1_agent,
        )

        # Step 4: match events
        matched = match_events(anchor, self.events_dir)

        # Step 5: synthesize narrative
        narrative = synthesize_narrative(
            question=question,
            fact_anchor=anchor,
            drill_results=drill_results,
            matched_events=matched,
            llm_client=self.llm_client,
        )

        return RCAReport(
            question_id=question_id,
            question=question,
            fact_anchor=anchor,
            drill_results=drill_results,
            matched_events=matched,
            narrative=narrative,
            trace_id=None,
            latency_ms=int((time.time() - t0) * 1000),
            error=None,
        )
