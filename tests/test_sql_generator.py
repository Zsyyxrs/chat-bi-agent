"""SQLGenerator 单测：幂等单次生成 + repair_hint 注入。所有测试 mock qwen_client。"""

from unittest.mock import patch

import pytest

from chat_bi_agent.agents.sql_generator import (
    InvalidJsonError,
    SQLGenerator,
    SQLGenResult,
)


def _mock_chat(content: str):
    """构造一个返回固定 content 的 ChatResult 替身。"""
    class _R:
        def __init__(self, c):
            self.content = c
    return _R(content)


SAMPLE_OK_RESPONSE = (
    "```json\n"
    "{\n"
    '  "thought": "查 dim_branch 的所有分行",\n'
    '  "tables_used": ["dim_branch"],\n'
    '  "sql": "SELECT branch_id, branch_name FROM dim_branch"\n'
    "}\n"
    "```\n"
)


def test_generate_returns_sqlgenresult():
    gen = SQLGenerator()
    with patch(
        "chat_bi_agent.agents.sql_generator.qwen_client.chat",
        return_value=_mock_chat(SAMPLE_OK_RESPONSE),
    ):
        r = gen.generate(
            question="所有分行有哪些？",
            schema_ddl="CREATE TABLE dim_branch (branch_id TEXT, branch_name TEXT)",
        )
    assert isinstance(r, SQLGenResult)
    assert r.sql == "SELECT branch_id, branch_name FROM dim_branch"
    assert r.tables_used == ["dim_branch"]
    assert r.thought == "查 dim_branch 的所有分行"
    assert r.raw_response == SAMPLE_OK_RESPONSE


def test_generate_passes_repair_hint_into_user_prompt():
    """repair_hint 必须出现在传给 qwen_client.chat 的 user_prompt 中。"""
    gen = SQLGenerator()
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE_OK_RESPONSE)

    with patch(
        "chat_bi_agent.agents.sql_generator.qwen_client.chat", side_effect=fake_chat,
    ):
        gen.generate(
            question="所有分行有哪些？",
            schema_ddl="CREATE TABLE dim_branch (branch_id TEXT)",
            repair_hint="上次用了不存在的列 foo，请改用 branch_id",
        )
    assert "上次用了不存在的列 foo" in captured["user_prompt"]


def test_generate_without_hint_omits_repair_section():
    """无 repair_hint 时 user_prompt 不含 '上次' 字样（避免空 hint 段）。"""
    gen = SQLGenerator()
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE_OK_RESPONSE)

    with patch(
        "chat_bi_agent.agents.sql_generator.qwen_client.chat", side_effect=fake_chat,
    ):
        gen.generate(
            question="所有分行有哪些？",
            schema_ddl="CREATE TABLE dim_branch (branch_id TEXT)",
        )
    assert "上次" not in captured["user_prompt"]


def test_generate_raises_invalid_json_on_unparseable():
    gen = SQLGenerator()
    with patch(
        "chat_bi_agent.agents.sql_generator.qwen_client.chat",
        return_value=_mock_chat("this is not json at all"),
    ):
        with pytest.raises(InvalidJsonError):
            gen.generate(question="q", schema_ddl="ddl")


def test_generate_raises_invalid_json_on_missing_field():
    """缺 sql 字段也算 InvalidJsonError。"""
    bad = '```json\n{"thought": "t", "tables_used": []}\n```'
    gen = SQLGenerator()
    with patch(
        "chat_bi_agent.agents.sql_generator.qwen_client.chat",
        return_value=_mock_chat(bad),
    ):
        with pytest.raises(InvalidJsonError):
            gen.generate(question="q", schema_ddl="ddl")


def test_generate_parses_json_without_fence():
    """无 ```json``` fence 但内容是合法 JSON 也应解析成功。"""
    raw = '{"thought":"t","tables_used":["x"],"sql":"SELECT 1"}'
    gen = SQLGenerator()
    with patch(
        "chat_bi_agent.agents.sql_generator.qwen_client.chat",
        return_value=_mock_chat(raw),
    ):
        r = gen.generate(question="q", schema_ddl="ddl")
    assert r.sql == "SELECT 1"
