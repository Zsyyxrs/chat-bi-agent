"""P1NL2SQLAgent 集成测试：mock 掉 LLM/PG，验证编排逻辑 + Result 字段。"""

from unittest.mock import MagicMock

from chat_bi_agent.agents.p1_nl2sql_agent import P1AgentResult, P1NL2SQLAgent
from chat_bi_agent.agents.schema_linker import TableMatch
from chat_bi_agent.agents.sql_generator import SQLGenResult


def test_run_returns_full_result_on_success():
    agent = P1NL2SQLAgent.__new__(P1NL2SQLAgent)  # 跳过 __init__
    agent.schema_linker = MagicMock()
    agent.sql_generator = MagicMock()
    agent.sql_executor = MagicMock()
    agent.loader = MagicMock()

    agent.schema_linker.link.return_value = [
        TableMatch(name="dim_customer", score=0.9, domain="客户"),
        TableMatch(name="dim_branch", score=0.7, domain="机构"),
    ]
    agent.loader.get_ddl_text.return_value = "-- ddl text"
    agent.sql_generator.generate.return_value = SQLGenResult(
        sql="SELECT 1",
        rows=[{"a": 1}],
        error=None,
        thought="T",
        tables_used=["dim_customer"],
        attempts=1,
    )

    result = agent.run("q001", "测试问题")

    assert isinstance(result, P1AgentResult)
    assert result.question_id == "q001"
    assert result.sql == "SELECT 1"
    assert result.rows == [{"a": 1}]
    assert result.execution_error is None
    assert result.thought == "T"
    assert result.attempts == 1
    assert result.schema_link_top_k == ["dim_customer", "dim_branch"]
    assert result.total_latency_ms > 0


def test_run_returns_error_on_all_attempts_fail():
    agent = P1NL2SQLAgent.__new__(P1NL2SQLAgent)
    agent.schema_linker = MagicMock()
    agent.sql_generator = MagicMock()
    agent.sql_executor = MagicMock()
    agent.loader = MagicMock()

    agent.schema_linker.link.return_value = [TableMatch(name="x", score=0.5, domain="d")]
    agent.loader.get_ddl_text.return_value = "ddl"
    agent.sql_generator.generate.return_value = SQLGenResult(
        sql="SELECT bad",
        rows=None,
        error="syntax error at or near 'bad'",
        thought="T",
        tables_used=["x"],
        attempts=3,
    )

    result = agent.run("q999", "坏问题")

    assert result.sql == "SELECT bad"
    assert result.rows is None
    assert "syntax" in result.execution_error
    assert result.attempts == 3
