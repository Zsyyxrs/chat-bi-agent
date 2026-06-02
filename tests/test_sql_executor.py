"""SQLExecutor 测试：白名单 + 错误分类。

部分测试需要真实 PG，标记 @pytest.mark.integration，可用 `pytest -m "not integration"` 跳过。
"""

import os

import pytest

from chat_bi_agent.agents.sql_executor import (
    SQLExecutor,
    UnsafeSQLError,
    SQLErrorClass,
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
