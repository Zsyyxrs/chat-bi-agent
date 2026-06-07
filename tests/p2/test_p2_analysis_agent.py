"""Integration tests for P2MultiStepAnalysisAgent. Mocks both P1 and LLM."""

from unittest.mock import MagicMock, patch

from chat_bi_agent.agents.p2.p2_analysis_agent import P2MultiStepAnalysisAgent
from chat_bi_agent.agents.p2.types import AnalysisReport
from chat_bi_agent.agents.sql_executor import SQLErrorClass

PLAN_2_STEPS = """```json
{
  "plan_type": "temporal_comparison",
  "steps": [
    {"id": "step1", "question": "查节前", "rationale": "建立基线",
     "depends_on": [], "context_keys": [], "expected_metrics": ["m1"]},
    {"id": "step2", "question": "查节中", "rationale": "对比组",
     "depends_on": [], "context_keys": [], "expected_metrics": ["m1"]}
  ]
}
```"""

REPLAN_1_STEP = """```json
{
  "plan_type": "temporal_comparison",
  "steps": [
    {"id": "step2", "question": "替换查询", "rationale": "换用其他表",
     "depends_on": [], "context_keys": [], "expected_metrics": ["m1"]}
  ]
}
```"""

FACTS_RESP = """```json
{"facts": [
  {"metric": "m1", "dimension": {"period": "before"}, "value": 100, "source_step": "step1"},
  {"metric": "m1", "dimension": {"period": "after"}, "value": 125, "source_step": "step2"}
]}
```"""

INSIGHTS_RESP = """```json
{"insights": [
  {"statement": "对比 step1 与 step2，增长 25%", "supporting_facts": [0, 1], "confidence": "high"}
]}
```"""

REPORT_RESP = "对比分析显示...因此...由于...AUM...客户...产品...风险... (final answer)"

# Canonical patch target — all p2 submodules import the same singleton
_QWEN_CHAT = "chat_bi_agent.llm.qwen_client.chat"


def _mock_chat(content: str):
    class _R:
        def __init__(self, c):
            self.content = c
    return _R(content)


def _mk_p1_result(ok: bool, sql: str | None = None,
                  rows: list[dict] | None = None,
                  error_class: SQLErrorClass | None = None,
                  error_msg: str | None = None):
    """Build a P1AgentResult lookalike (duck-typed)."""
    return MagicMock(
        sql=sql,
        rows=rows,
        execution_error=error_msg,
        error_class=error_class,
        attempts=1,
        total_latency_ms=100,
    )


def _make_agent_with_mocks():
    mock_p1 = MagicMock()
    mock_linker = MagicMock()
    m = MagicMock()
    m.name = "fct_transaction"
    mock_linker.link.return_value = [m]
    mock_loader = MagicMock()
    mock_loader.get_ddl_text.return_value = "CREATE TABLE ..."
    agent = P2MultiStepAnalysisAgent(
        p1_agent=mock_p1,
        schema_linker=mock_linker,
        loader=mock_loader,
        top_k=8,
    )
    return agent, mock_p1


def test_happy_path_returns_full_report():
    agent, mock_p1 = _make_agent_with_mocks()
    mock_p1.run.side_effect = [
        _mk_p1_result(ok=True, sql="SQL1", rows=[{"v": 100}]),
        _mk_p1_result(ok=True, sql="SQL2", rows=[{"v": 125}]),
    ]
    # Planner → FactExtractor → InsightSynthesizer → ReportWriter
    llm_responses = [
        _mock_chat(PLAN_2_STEPS),
        _mock_chat(FACTS_RESP),
        _mock_chat(INSIGHTS_RESP),
        _mock_chat(REPORT_RESP),
    ]
    with patch(_QWEN_CHAT, side_effect=llm_responses):
        report = agent.run(question_id="multi_step_q001", question="春节对比")

    assert isinstance(report, AnalysisReport)
    assert report.question_id == "multi_step_q001"
    assert len(report.step_results) == 2
    assert all(not sr.skipped for sr in report.step_results)
    assert report.replan_count == 0
    assert report.final_answer == REPORT_RESP


def test_failed_step_triggers_replan_then_succeeds():
    agent, mock_p1 = _make_agent_with_mocks()
    mock_p1.run.side_effect = [
        _mk_p1_result(ok=True, sql="SQL1", rows=[{"v": 100}]),
        _mk_p1_result(ok=False, sql="SQL2",
                      error_class=SQLErrorClass.UNKNOWN_TABLE,
                      error_msg="relation x does not exist"),
        _mk_p1_result(ok=True, sql="SQL2b", rows=[{"v": 125}]),
    ]
    # Planner → Replanner → FactExtractor → InsightSynthesizer → ReportWriter
    llm_responses = [
        _mock_chat(PLAN_2_STEPS),
        _mock_chat(REPLAN_1_STEP),
        _mock_chat(FACTS_RESP),
        _mock_chat(INSIGHTS_RESP),
        _mock_chat(REPORT_RESP),
    ]
    with patch(_QWEN_CHAT, side_effect=llm_responses):
        report = agent.run(question_id="qid", question="q")

    assert report.replan_count == 1
    assert len(report.step_results) == 2
    assert report.step_results[0].sql == "SQL1"
    assert report.step_results[1].sql == "SQL2b"


def test_replan_then_step_still_fails_marks_skipped():
    agent, mock_p1 = _make_agent_with_mocks()
    mock_p1.run.side_effect = [
        _mk_p1_result(ok=True, sql="SQL1", rows=[{"v": 100}]),
        _mk_p1_result(ok=False, error_class=SQLErrorClass.UNKNOWN_TABLE,
                      error_msg="x"),
        _mk_p1_result(ok=False, error_class=SQLErrorClass.UNKNOWN_TABLE,
                      error_msg="x"),
    ]
    # Planner → Replanner → FactExtractor → InsightSynthesizer → ReportWriter
    llm_responses = [
        _mock_chat(PLAN_2_STEPS),
        _mock_chat(REPLAN_1_STEP),
        _mock_chat(FACTS_RESP),
        _mock_chat(INSIGHTS_RESP),
        _mock_chat(REPORT_RESP),
    ]
    with patch(_QWEN_CHAT, side_effect=llm_responses):
        report = agent.run(question_id="qid", question="q")

    assert report.replan_count == 1
    assert len(report.step_results) == 2
    assert report.step_results[0].skipped is False
    assert report.step_results[1].skipped is True


def test_passes_question_id_with_step_suffix_to_p1():
    agent, mock_p1 = _make_agent_with_mocks()
    mock_p1.run.side_effect = [
        _mk_p1_result(ok=True, sql="s", rows=[]),
        _mk_p1_result(ok=True, sql="s", rows=[]),
    ]
    # Planner → FactExtractor → InsightSynthesizer → ReportWriter
    llm_responses = [
        _mock_chat(PLAN_2_STEPS),
        _mock_chat(FACTS_RESP),
        _mock_chat(INSIGHTS_RESP),
        _mock_chat(REPORT_RESP),
    ]
    with patch(_QWEN_CHAT, side_effect=llm_responses):
        agent.run(question_id="QQQ", question="q")

    calls = mock_p1.run.call_args_list
    qids = [c.kwargs.get("question_id") or c.args[0] for c in calls]
    assert qids[0].startswith("QQQ__")
    assert "step" in qids[0]
