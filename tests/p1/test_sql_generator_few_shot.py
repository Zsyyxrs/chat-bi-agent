"""SQLGenerator few-shot 注入单测：验证 examples 进 user_prompt 的位置与格式。"""

from unittest.mock import patch

from chat_bi_agent.agents.p1.sql_generator import SQLGenerator

SAMPLE_OK_RESPONSE = (
    "```json\n"
    "{\n"
    '  "thought": "示例响应",\n'
    '  "tables_used": ["t"],\n'
    '  "sql": "SELECT 1"\n'
    "}\n"
    "```\n"
)


def _mock_chat(content: str):
    class _R:
        def __init__(self, c):
            self.content = c
            self.prompt_tokens = 0
            self.completion_tokens = 0

    return _R(content)


def test_generate_without_examples_omits_examples_section():
    gen = SQLGenerator(dialect="sqlite")
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE_OK_RESPONSE)

    with patch(
        "chat_bi_agent.agents.p1.sql_generator.qwen_client.chat", side_effect=fake_chat
    ):
        gen.generate(question="q", schema_ddl="DDL")
    assert "参考示例" not in captured["user_prompt"]
    assert "示例" not in captured["user_prompt"]  # 只要不引入示例段就行


def test_generate_with_examples_includes_examples_before_question():
    gen = SQLGenerator(dialect="sqlite")
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE_OK_RESPONSE)

    examples = [
        ("How many clients?", "SELECT COUNT(*) FROM client"),
        ("Average balance", "SELECT AVG(balance) FROM account"),
    ]
    with patch(
        "chat_bi_agent.agents.p1.sql_generator.qwen_client.chat", side_effect=fake_chat
    ):
        gen.generate(
            question="用户问题在这里", schema_ddl="DDL_HERE", few_shot_examples=examples
        )

    up = captured["user_prompt"]
    # 顺序：schema → 示例段 → 用户问题
    assert up.index("DDL_HERE") < up.index("How many clients?")
    assert up.index("How many clients?") < up.index("用户问题在这里")
    assert "SELECT COUNT(*) FROM client" in up
    assert "SELECT AVG(balance) FROM account" in up


def test_generate_with_empty_examples_omits_section():
    """empty list 应等价于 None，不拼参考段。"""
    gen = SQLGenerator(dialect="sqlite")
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE_OK_RESPONSE)

    with patch(
        "chat_bi_agent.agents.p1.sql_generator.qwen_client.chat", side_effect=fake_chat
    ):
        gen.generate(question="q", schema_ddl="DDL", few_shot_examples=[])
    assert "参考示例" not in captured["user_prompt"]


def test_examples_and_repair_hint_both_present():
    """反思重试第二轮：既有 hint 又有 examples，两者都必须在 user_prompt。"""
    gen = SQLGenerator(dialect="sqlite")
    captured = {}

    def fake_chat(system_prompt, user_prompt):
        captured["user_prompt"] = user_prompt
        return _mock_chat(SAMPLE_OK_RESPONSE)

    with patch(
        "chat_bi_agent.agents.p1.sql_generator.qwen_client.chat", side_effect=fake_chat
    ):
        gen.generate(
            question="q",
            schema_ddl="DDL",
            repair_hint="RESERVED_HINT_TOKEN",
            few_shot_examples=[("prior q", "SELECT 1")],
        )
    up = captured["user_prompt"]
    assert "RESERVED_HINT_TOKEN" in up
    assert "prior q" in up
    assert "SELECT 1" in up
