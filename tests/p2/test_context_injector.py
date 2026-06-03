"""Context injector: pure function tests, no mocks."""

from chat_bi_agent.agents.p2.context_injector import inject_context
from chat_bi_agent.agents.p2.types import PlanStep, StepResult


def _sr(sid: str, rows: list[dict] | None) -> StepResult:
    return StepResult(
        step=PlanStep(id=sid, question="q", rationale="r"),
        sql="SELECT 1",
        rows=rows,
        error_class=None,
        error_msg=None,
        skipped=False,
    )


def test_inject_returns_question_unchanged_when_no_context_keys():
    step = PlanStep(id="step2", question="原始问题", rationale="r")
    out = inject_context(step, {})
    assert out == "原始问题"


def test_inject_appends_context_block_when_present():
    step = PlanStep(
        id="step2",
        question="查询客户的产品偏好",
        rationale="r",
        context_keys=["step1.rows.customer_ids"],
    )
    prior = {
        "step1": _sr("step1", [{"customer_id": "C001"}, {"customer_id": "C002"}]),
    }
    out = inject_context(step, prior)
    assert "查询客户的产品偏好" in out
    assert "背景" in out
    assert "step1" in out
    assert "C001" in out


def test_inject_handles_missing_prior_step_gracefully():
    step = PlanStep(
        id="step2",
        question="q",
        rationale="r",
        context_keys=["step9.rows.foo"],
    )
    out = inject_context(step, {})
    assert "q" in out


def test_inject_truncates_long_row_lists():
    step = PlanStep(
        id="step2",
        question="q",
        rationale="r",
        context_keys=["step1.rows"],
    )
    long_rows = [{"id": f"C{i:04d}"} for i in range(100)]
    prior = {"step1": _sr("step1", long_rows)}
    out = inject_context(step, prior)
    assert "C0000" in out
    assert "100" in out or "省略" in out or "..." in out
