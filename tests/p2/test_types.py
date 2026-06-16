"""Test P2 data structures, especially evaluator interface mapping."""

from chat_bi_agent.agents.p2.types import (
    AnalysisReport,
    Fact,
    Insight,
    P2Plan,
    PlanStep,
    StepResult,
)


def _make_step(sid: str, q: str, rationale: str) -> PlanStep:
    return PlanStep(id=sid, question=q, rationale=rationale)


def test_to_eval_input_maps_all_evaluator_fields():
    """AnalysisReport.to_eval_input() returns the exact dict shape expected by
    MultiStepAnalysisEvaluator.evaluate_response()."""
    plan = P2Plan(
        question="春节对比",
        plan_type="temporal_comparison",
        steps=[
            _make_step("step1", "前段查询", "建立基线"),
            _make_step("step2", "后段查询", "对比组"),
        ],
    )
    step_results = [
        StepResult(
            step=plan.steps[0],
            sql="SELECT 1",
            rows=[{"x": 1}],
            error_class=None,
            error_msg=None,
            skipped=False,
        ),
        StepResult(
            step=plan.steps[1],
            sql=None,
            rows=None,
            error_class=None,
            error_msg="failed",
            skipped=True,
        ),
    ]
    facts = [
        Fact(
            metric="withdraw_total_amount",
            dimension={"period": "before"},
            value=1000.0,
            source_step="step1",
        ),
        Fact(
            metric="withdraw_count", dimension={"period": "before"}, value=10, source_step="step1"
        ),
    ]
    insights = [
        Insight(statement="春节期间现金支取增长25%", supporting_facts=[0, 1], confidence="high"),
    ]
    report = AnalysisReport(
        question="春节对比",
        question_id="multi_step_q001",
        plan=plan,
        step_results=step_results,
        facts=facts,
        insights=insights,
        final_answer="对比分析显示春节期间...",
    )

    eval_input = report.to_eval_input()

    assert eval_input["question_id"] == "multi_step_q001"
    assert eval_input["agent_response"] == "对比分析显示春节期间..."
    # Skipped steps are excluded from mentioned_steps
    assert eval_input["mentioned_steps"] == ["建立基线"]
    # Metrics deduplicated and sorted
    assert eval_input["mentioned_metrics"] == ["withdraw_count", "withdraw_total_amount"]
    assert eval_input["extracted_insights"] == ["春节期间现金支取增长25%"]


def test_plan_step_defaults_are_empty_lists():
    s = PlanStep(id="step1", question="q", rationale="r")
    assert s.depends_on == []
    assert s.context_keys == []
    assert s.expected_metrics == []
