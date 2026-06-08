"""SQLExecutor 测试：白名单 + 错误分类。

部分测试需要真实 PG，标记 @pytest.mark.integration，可用 `pytest -m "not integration"` 跳过。
"""

import os

import pytest

from chat_bi_agent.agents.sql_executor import (
    SQLErrorClass,
    SQLExecutor,
    UnsafeSQLError,
)


def test_blocks_drop():
    executor = SQLExecutor()
    with pytest.raises(UnsafeSQLError):
        executor.execute("DROP TABLE dim_customer")


def test_blocks_update():
    executor = SQLExecutor()
    with pytest.raises(UnsafeSQLError):
        executor.execute("UPDATE dim_customer SET customer_name='x'")


def test_blocks_insert():
    executor = SQLExecutor()
    with pytest.raises(UnsafeSQLError):
        executor.execute("INSERT INTO dim_customer VALUES (1)")


def test_blocks_delete():
    executor = SQLExecutor()
    with pytest.raises(UnsafeSQLError):
        executor.execute("DELETE FROM dim_customer WHERE 1=1")


def test_blocks_mixed_case_drop():
    executor = SQLExecutor()
    with pytest.raises(UnsafeSQLError):
        executor.execute("dRoP table x")


def test_allows_select():
    executor = SQLExecutor()
    # 不会抛 UnsafeSQLError；但因为没真连 PG，可能抛连接错或别的
    # 这里我们只验证语法白名单层放行
    assert executor._is_safe("SELECT 1") is True
    assert executor._is_safe("  select id from t") is True


def test_classify_syntax_error():
    msg = 'syntax error at or near "SELEC"'
    assert SQLExecutor.classify_error(msg) == SQLErrorClass.SYNTAX_ERROR


def test_classify_unknown_table():
    msg = 'relation "foo" does not exist'
    assert SQLExecutor.classify_error(msg) == SQLErrorClass.UNKNOWN_TABLE


def test_classify_unknown_column():
    msg = 'column "bar" does not exist'
    assert SQLExecutor.classify_error(msg) == SQLErrorClass.UNKNOWN_COLUMN


def test_classify_other():
    msg = "division by zero"
    assert SQLExecutor.classify_error(msg) == SQLErrorClass.OTHER


@pytest.mark.integration
def test_select_against_real_pg():
    """需要 docker-compose 启动 + chatbi_readonly 角色存在 + dim_customer 有数据。"""
    if not os.environ.get("PG_HOST"):
        pytest.skip("PG 未配置")
    executor = SQLExecutor()
    rows, err = executor.execute("SELECT customer_id FROM dim_customer LIMIT 1")
    assert err is None
    assert len(rows) >= 0  # 表可能为空也 OK


@pytest.mark.integration
def test_unknown_table_against_real_pg():
    if not os.environ.get("PG_HOST"):
        pytest.skip("PG 未配置")
    executor = SQLExecutor()
    rows, err = executor.execute("SELECT * FROM nonexistent_table_xyz")
    assert rows is None
    assert err is not None
    assert SQLExecutor.classify_error(err) == SQLErrorClass.UNKNOWN_TABLE


# --- P2 新增：timeout + TIMEOUT 分类 ---


def test_executor_accepts_statement_timeout_param():
    executor = SQLExecutor(statement_timeout_ms=5000)
    assert executor.statement_timeout_ms == 5000


def test_executor_default_timeout_is_10s():
    executor = SQLExecutor()
    assert executor.statement_timeout_ms == 10_000


def test_classify_timeout():
    msg = "canceling statement due to statement timeout"
    assert SQLExecutor.classify_error(msg) == SQLErrorClass.TIMEOUT


def test_classify_timeout_takes_priority_over_syntax():
    """timeout 文本即使含 'syntax' 子串也归 TIMEOUT 而非 SYNTAX_ERROR。"""
    msg = "canceling statement due to statement timeout near 'syntax'"
    assert SQLExecutor.classify_error(msg) == SQLErrorClass.TIMEOUT


def test_sql_error_class_has_new_members():
    """扩展枚举：TIMEOUT / INVALID_JSON / VALIDATOR_FAIL 必须存在。"""
    assert SQLErrorClass.TIMEOUT.value == "TIMEOUT"
    assert SQLErrorClass.INVALID_JSON.value == "INVALID_JSON"
    assert SQLErrorClass.VALIDATOR_FAIL.value == "VALIDATOR_FAIL"


def test_connect_options_includes_statement_timeout(monkeypatch):
    """psycopg2.connect 收到的 options 字符串必须含 statement_timeout=10000。"""
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop here — we only care about kwargs")

    monkeypatch.setattr(
        "chat_bi_agent.agents.sql_executor.psycopg2.connect",
        fake_connect,
    )
    executor = SQLExecutor(statement_timeout_ms=10_000)
    with pytest.raises(RuntimeError):
        executor.execute("SELECT 1")
    assert "statement_timeout=10000" in captured.get("options", "")
