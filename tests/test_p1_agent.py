"""P1NL2SQLAgent 编排测试：mock SchemaLinker/SQLGenerator/SQLValidator/SQLExecutor/Reflector。

不依赖真实 LLM、PG、Langfuse 后端（@observe 装饰对 mock 无影响）。
"""

from unittest.mock import patch

import pytest

from chat_bi_agent.agents.p1_nl2sql_agent import P1AgentResult, P1NL2SQLAgent
from chat_bi_agent.agents.reflector import ReflectAction, ReflectDecision
from chat_bi_agent.agents.sql_executor import SQLErrorClass
from chat_bi_agent.agents.sql_generator import InvalidJsonError, SQLGenResult
from chat_bi_agent.agents.sql_validator import ValidationResult


class _StubMatch:
    def __init__(self, name):
        self.name = name


def _make_agent_with_mocks():
    """构造 agent 并把所有依赖换成 mock；schema_ddl 用 stub。"""
    with patch("chat_bi_agent.agents.p1_nl2sql_agent.SchemaLoader") as ml, \
         patch("chat_bi_agent.agents.p1_nl2sql_agent.SchemaLinker") as msl, \
         patch("chat_bi_agent.agents.p1_nl2sql_agent.SQLGenerator") as msg, \
         patch("chat_bi_agent.agents.p1_nl2sql_agent.SQLValidator") as msv, \
         patch("chat_bi_agent.agents.p1_nl2sql_agent.SQLExecutor") as mse, \
         patch("chat_bi_agent.agents.p1_nl2sql_agent.Reflector") as mr:
        loader_instance = ml.return_value
        loader_instance.load.return_value = None
        loader_instance.build_index.return_value = None
        loader_instance.get_ddl_text.return_value = "CREATE TABLE dummy(x INT)"

        agent = P1NL2SQLAgent(top_k=4)
        agent.schema_linker = msl.return_value
        agent.sql_generator = msg.return_value
        agent.sql_validator = msv.return_value
        agent.sql_executor = mse.return_value
        agent.reflector = mr.return_value

        agent.schema_linker.link.return_value = [
            _StubMatch("dim_branch"), _StubMatch("dim_customer"),
        ]
        return agent


def test_happy_path_single_attempt():
    agent = _make_agent_with_mocks()
    agent.sql_generator.generate.return_value = SQLGenResult(
        sql="SELECT 1", thought="t", tables_used=["dim_branch"], raw_response="raw",
    )
    agent.sql_validator.validate.return_value = ValidationResult(ok=True, error=None)
    agent.sql_executor.execute.return_value = ([{"a": 1}], None)

    r = agent.run(question_id="q1", question="x")
    assert isinstance(r, P1AgentResult)
    assert r.attempts == 1
    assert r.rows == [{"a": 1}]
    assert r.execution_error is None
    assert r.error_class is None
    assert r.reflect_history == []
    agent.sql_generator.generate.assert_called_once()
    agent.sql_validator.validate.assert_called_once()
    agent.sql_executor.execute.assert_called_once()
    agent.reflector.reflect.assert_not_called()


def test_unknown_table_then_success_two_attempts():
    agent = _make_agent_with_mocks()
    agent.sql_generator.generate.side_effect = [
        SQLGenResult(sql="SELECT * FROM foo", thought="t", tables_used=["foo"], raw_response="r1"),
        SQLGenResult(sql="SELECT * FROM dim_branch", thought="t", tables_used=["dim_branch"], raw_response="r2"),  # noqa: E501
    ]
    agent.sql_validator.validate.return_value = ValidationResult(ok=True, error=None)
    agent.sql_executor.execute.side_effect = [
        (None, 'relation "foo" does not exist'),
        ([{"a": 1}], None),
    ]
    agent.sql_executor.classify_error.return_value = SQLErrorClass.UNKNOWN_TABLE
    agent.reflector.reflect.return_value = ReflectDecision(
        action=ReflectAction.RETRY, repair_hint="只能用 dim_branch",
    )

    r = agent.run(question_id="q1", question="x")
    assert r.attempts == 2
    assert r.rows == [{"a": 1}]
    assert r.error_class is None
    assert len(r.reflect_history) == 1
    assert r.reflect_history[0]["err_class"] == "UNKNOWN_TABLE"
    # 第二次 generate 必须接到 repair_hint
    _, kwargs = agent.sql_generator.generate.call_args_list[1]
    assert kwargs.get("repair_hint") == "只能用 dim_branch"


def test_timeout_gives_up_immediately():
    agent = _make_agent_with_mocks()
    agent.sql_generator.generate.return_value = SQLGenResult(
        sql="SELECT pg_sleep(99)", thought="t", tables_used=["dim_branch"], raw_response="r",
    )
    agent.sql_validator.validate.return_value = ValidationResult(ok=True, error=None)
    agent.sql_executor.execute.return_value = (
        None, "canceling statement due to statement timeout",
    )
    agent.sql_executor.classify_error.return_value = SQLErrorClass.TIMEOUT
    agent.reflector.reflect.return_value = ReflectDecision(
        action=ReflectAction.GIVE_UP, repair_hint=None,
    )

    r = agent.run(question_id="q1", question="x")
    assert r.attempts == 1
    assert r.rows is None
    assert r.error_class == SQLErrorClass.TIMEOUT
    assert len(r.reflect_history) == 1
    assert r.reflect_history[0]["action"] == "GIVE_UP"
    agent.sql_generator.generate.assert_called_once()


def test_validator_fail_then_success():
    agent = _make_agent_with_mocks()
    agent.sql_generator.generate.side_effect = [
        SQLGenResult(sql="DROP TABLE x", thought="t", tables_used=[], raw_response="r1"),
        SQLGenResult(sql="SELECT 1", thought="t", tables_used=["dim_branch"], raw_response="r2"),
    ]
    agent.sql_validator.validate.side_effect = [
        ValidationResult(ok=False, error="包含禁止操作：Drop"),
        ValidationResult(ok=True, error=None),
    ]
    agent.sql_executor.execute.return_value = ([{"a": 1}], None)
    agent.reflector.reflect.return_value = ReflectDecision(
        action=ReflectAction.RETRY, repair_hint="禁 DML/DDL",
    )

    r = agent.run(question_id="q1", question="x")
    assert r.attempts == 2
    assert r.rows == [{"a": 1}]
    assert r.reflect_history[0]["err_class"] == "VALIDATOR_FAIL"
    # validator 拒掉后 executor 在第一轮不应被调
    assert agent.sql_executor.execute.call_count == 1


def test_invalid_json_then_success():
    agent = _make_agent_with_mocks()
    agent.sql_generator.generate.side_effect = [
        InvalidJsonError("bad json"),
        SQLGenResult(sql="SELECT 1", thought="t", tables_used=["dim_branch"], raw_response="r2"),
    ]
    agent.sql_validator.validate.return_value = ValidationResult(ok=True, error=None)
    agent.sql_executor.execute.return_value = ([{"a": 1}], None)
    agent.reflector.reflect.return_value = ReflectDecision(
        action=ReflectAction.RETRY, repair_hint="请用 ```json``` 包裹",
    )

    r = agent.run(question_id="q1", question="x")
    assert r.attempts == 2
    assert r.rows == [{"a": 1}]
    assert r.reflect_history[0]["err_class"] == "INVALID_JSON"


def test_three_attempts_all_fail():
    agent = _make_agent_with_mocks()
    agent.sql_generator.generate.return_value = SQLGenResult(
        sql="SELECT * FROM foo", thought="t", tables_used=["foo"], raw_response="r",
    )
    agent.sql_validator.validate.return_value = ValidationResult(ok=True, error=None)
    agent.sql_executor.execute.return_value = (None, 'relation "foo" does not exist')
    agent.sql_executor.classify_error.return_value = SQLErrorClass.UNKNOWN_TABLE
    # 第 1/2 次 RETRY，第 3 次 GIVE_UP（也可以全 RETRY，由 for 循环上限阻断）
    agent.reflector.reflect.side_effect = [
        ReflectDecision(action=ReflectAction.RETRY, repair_hint="h1"),
        ReflectDecision(action=ReflectAction.RETRY, repair_hint="h2"),
        ReflectDecision(action=ReflectAction.GIVE_UP, repair_hint=None),
    ]

    r = agent.run(question_id="q1", question="x")
    assert r.attempts == 3
    assert r.rows is None
    assert r.execution_error == 'relation "foo" does not exist'
    assert r.error_class == SQLErrorClass.UNKNOWN_TABLE
    assert len(r.reflect_history) == 3


def test_empty_schema_link_raises():
    agent = _make_agent_with_mocks()
    agent.schema_linker.link.return_value = []
    with pytest.raises(RuntimeError):
        agent.run(question_id="q1", question="x")
