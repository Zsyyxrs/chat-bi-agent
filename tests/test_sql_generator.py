"""SQLGenerator 测试：mock LLM，专注 prompt 拼接 + JSON 解析 + 重试逻辑。"""

from unittest.mock import MagicMock, patch

import pytest

from chat_bi_agent.agents.sql_generator import (
    InvalidJsonError,
    SQLGenerator,
    SQLGenResult,
)


@pytest.fixture
def gen():
    return SQLGenerator()


def _fake_chat_result(content: str):
    """模拟 qwen_client.chat() 的返回。"""
    m = MagicMock()
    m.content = content
    m.prompt_tokens = 100
    m.completion_tokens = 50
    return m


def test_parse_valid_json(gen):
    raw = '```json\n{"thought": "x", "tables_used": ["t1"], "sql": "SELECT 1"}\n```'
    parsed = gen._parse(raw)
    assert parsed.thought == "x"
    assert parsed.tables_used == ["t1"]
    assert parsed.sql == "SELECT 1"


def test_parse_json_without_code_fence(gen):
    raw = '{"thought": "x", "tables_used": ["t1"], "sql": "SELECT 1"}'
    parsed = gen._parse(raw)
    assert parsed.sql == "SELECT 1"


def test_parse_invalid_json_raises(gen):
    with pytest.raises(InvalidJsonError):
        gen._parse("this is not json")


def test_parse_missing_field_raises(gen):
    raw = '```json\n{"thought": "x", "sql": "SELECT 1"}\n```'
    with pytest.raises(InvalidJsonError):
        gen._parse(raw)


def test_first_attempt_success(gen):
    with patch("chat_bi_agent.agents.sql_generator.qwen_client.chat") as mock_chat:
        mock_chat.return_value = _fake_chat_result(
            '```json\n{"thought": "T", "tables_used": ["t"], "sql": "SELECT 1"}\n```'
        )
        result = gen.generate(
            question="问题",
            schema_ddl="-- ddl",
            execute_fn=lambda sql: ([], None),
        )
    assert isinstance(result, SQLGenResult)
    assert result.attempts == 1
    assert result.sql == "SELECT 1"
    assert result.rows == []
    assert result.error is None


def test_retry_on_invalid_json_then_success(gen):
    """第一次输出非法 JSON，第二次正常。"""
    with patch("chat_bi_agent.agents.sql_generator.qwen_client.chat") as mock_chat:
        mock_chat.side_effect = [
            _fake_chat_result("not json"),
            _fake_chat_result('```json\n{"thought": "T", "tables_used": ["t"], "sql": "SELECT 1"}\n```'),  # noqa: E501
        ]
        result = gen.generate(
            question="问题",
            schema_ddl="-- ddl",
            execute_fn=lambda sql: ([], None),
        )
    assert result.attempts == 2
    assert result.sql == "SELECT 1"
    assert result.error is None


def test_retry_on_execution_error_then_success(gen):
    """第一次执行报 unknown column，第二次修正成功。"""
    sqls = []
    def fake_exec(sql):
        sqls.append(sql)
        if len(sqls) == 1:
            return None, 'column "foo" does not exist'
        return [{"x": 1}], None

    with patch("chat_bi_agent.agents.sql_generator.qwen_client.chat") as mock_chat:
        mock_chat.side_effect = [
            _fake_chat_result('```json\n{"thought":"T","tables_used":["t"],"sql":"SELECT foo FROM t"}\n```'),  # noqa: E501
            _fake_chat_result('```json\n{"thought":"T","tables_used":["t"],"sql":"SELECT bar FROM t"}\n```'),  # noqa: E501
        ]
        result = gen.generate(
            question="问题",
            schema_ddl="-- ddl",
            execute_fn=fake_exec,
        )
    assert result.attempts == 2
    assert result.sql == "SELECT bar FROM t"
    assert result.rows == [{"x": 1}]


def test_all_attempts_fail_returns_last_error(gen):
    with patch("chat_bi_agent.agents.sql_generator.qwen_client.chat") as mock_chat:
        mock_chat.side_effect = [
            _fake_chat_result('```json\n{"thought":"T","tables_used":["t"],"sql":"SELECT bad"}\n```'),  # noqa: E501
        ] * 3
        result = gen.generate(
            question="问题",
            schema_ddl="-- ddl",
            execute_fn=lambda sql: (None, "syntax error at ..."),
        )
    assert result.attempts == 3
    assert result.sql == "SELECT bad"
    assert result.rows is None
    assert result.error is not None
    assert "syntax error" in result.error


def test_retry_prompt_includes_error_class_for_unknown_column(gen):
    captured_prompts = []

    def capture(system_prompt, user_prompt, **kwargs):
        captured_prompts.append(user_prompt)
        return _fake_chat_result(
            '```json\n{"thought":"T","tables_used":["t"],"sql":"SELECT foo FROM t"}\n```'
        )

    with patch("chat_bi_agent.agents.sql_generator.qwen_client.chat", side_effect=capture):
        gen.generate(
            question="问题",
            schema_ddl="-- ddl",
            execute_fn=lambda sql: (None, 'column "foo" does not exist'),
        )

    assert "column" in captured_prompts[1].lower() or "列" in captured_prompts[1]
