"""Reflector 决策矩阵测试：7 种 err_class × (attempt=1, attempt=3)。"""

import pytest

from chat_bi_agent.agents.reflector import ReflectAction, Reflector
from chat_bi_agent.agents.sql_executor import SQLErrorClass


@pytest.fixture
def reflector():
    return Reflector(max_attempts=3)


@pytest.fixture
def top_k_tables():
    return ["dim_customer", "dim_branch", "fact_account_balance", "dim_product"]


# --- attempt=1 (首次失败) ---

def test_invalid_json_attempt1_retries(reflector, top_k_tables):
    d = reflector.reflect(
        SQLErrorClass.INVALID_JSON, "bad json", None, top_k_tables, attempt=1,
    )
    assert d.action == ReflectAction.RETRY
    assert "JSON" in d.repair_hint


def test_validator_fail_attempt1_retries(reflector, top_k_tables):
    d = reflector.reflect(
        SQLErrorClass.VALIDATOR_FAIL, "顶层非 SELECT", "DROP TABLE x", top_k_tables, attempt=1,
    )
    assert d.action == ReflectAction.RETRY
    assert "SELECT" in d.repair_hint or "WITH" in d.repair_hint


def test_syntax_error_attempt1_retries(reflector, top_k_tables):
    d = reflector.reflect(
        SQLErrorClass.SYNTAX_ERROR, "syntax error at or near 'SELEC'",
        "SELEC 1", top_k_tables, attempt=1,
    )
    assert d.action == ReflectAction.RETRY
    assert "语法" in d.repair_hint


def test_unknown_column_attempt1_retries(reflector, top_k_tables):
    d = reflector.reflect(
        SQLErrorClass.UNKNOWN_COLUMN, 'column "bar" does not exist',
        "SELECT bar FROM dim_customer", top_k_tables, attempt=1,
    )
    assert d.action == ReflectAction.RETRY
    assert "列" in d.repair_hint


def test_unknown_table_attempt1_retries_with_top_k(reflector, top_k_tables):
    d = reflector.reflect(
        SQLErrorClass.UNKNOWN_TABLE, 'relation "foo" does not exist',
        "SELECT * FROM foo", top_k_tables, attempt=1,
    )
    assert d.action == ReflectAction.RETRY
    # repair_hint 必须包含 top_k 列表（让 LLM 不再编造表名）
    for t in top_k_tables:
        assert t in d.repair_hint


def test_other_attempt1_retries(reflector, top_k_tables):
    d = reflector.reflect(
        SQLErrorClass.OTHER, "division by zero",
        "SELECT 1/0", top_k_tables, attempt=1,
    )
    assert d.action == ReflectAction.RETRY


def test_timeout_attempt1_gives_up(reflector, top_k_tables):
    """TIMEOUT 永远 GIVE_UP，即使首次。"""
    d = reflector.reflect(
        SQLErrorClass.TIMEOUT, "canceling statement due to statement timeout",
        "SELECT count(*) FROM huge_table", top_k_tables, attempt=1,
    )
    assert d.action == ReflectAction.GIVE_UP
    assert d.repair_hint is None


# --- attempt=3 (达上限) ---

@pytest.mark.parametrize("err_class", [
    SQLErrorClass.INVALID_JSON,
    SQLErrorClass.VALIDATOR_FAIL,
    SQLErrorClass.SYNTAX_ERROR,
    SQLErrorClass.UNKNOWN_COLUMN,
    SQLErrorClass.UNKNOWN_TABLE,
    SQLErrorClass.TIMEOUT,
    SQLErrorClass.OTHER,
])
def test_attempt_at_max_always_gives_up(reflector, top_k_tables, err_class):
    d = reflector.reflect(err_class, "any error", "any sql", top_k_tables, attempt=3)
    assert d.action == ReflectAction.GIVE_UP
    assert d.repair_hint is None


def test_attempt_above_max_gives_up(reflector, top_k_tables):
    """attempt > max_attempts 也 GIVE_UP（防御性）。"""
    d = reflector.reflect(
        SQLErrorClass.SYNTAX_ERROR, "err", "sql", top_k_tables, attempt=5,
    )
    assert d.action == ReflectAction.GIVE_UP
