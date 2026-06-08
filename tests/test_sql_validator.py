"""SQLValidator 单测：sqlglot AST 静态检查。无 PG 依赖。"""

import pytest

from chat_bi_agent.agents.sql_validator import SQLValidator, ValidationResult


@pytest.fixture
def validator():
    return SQLValidator()


def test_allows_simple_select(validator):
    r = validator.validate("SELECT customer_id FROM dim_customer")
    assert r.ok is True
    assert r.error is None


def test_allows_cte_with(validator):
    sql = "WITH t AS (SELECT 1 AS x) SELECT x FROM t"
    assert validator.validate(sql).ok is True


def test_rejects_insert(validator):
    r = validator.validate("INSERT INTO dim_customer VALUES (1)")
    assert r.ok is False
    assert r.error is not None


def test_rejects_update(validator):
    assert validator.validate("UPDATE dim_customer SET x = 1").ok is False


def test_rejects_delete(validator):
    assert validator.validate("DELETE FROM dim_customer WHERE 1=1").ok is False


def test_rejects_drop(validator):
    assert validator.validate("DROP TABLE dim_customer").ok is False


def test_rejects_truncate(validator):
    assert validator.validate("TRUNCATE TABLE dim_customer").ok is False


def test_rejects_alter(validator):
    assert validator.validate("ALTER TABLE dim_customer ADD COLUMN x INT").ok is False


def test_rejects_create(validator):
    assert validator.validate("CREATE TABLE x (id INT)").ok is False


def test_rejects_grant(validator):
    assert validator.validate("GRANT SELECT ON dim_customer TO foo").ok is False


def test_rejects_multi_statement_with_drop(validator):
    """多语句：第一条 SELECT 第二条 DROP，必须被拒。"""
    r = validator.validate("SELECT 1; DROP TABLE dim_customer")
    assert r.ok is False


def test_drop_inside_comment_not_rejected(validator):
    """注释里的 DROP 不应误判 —— 这是正则黑名单的硬伤，sqlglot 必须修掉。"""
    r = validator.validate("/* DROP TABLE foo */ SELECT 1")
    assert r.ok is True


def test_drop_inside_string_literal_not_rejected(validator):
    """字符串里的 DROP 不应误判。"""
    r = validator.validate("SELECT 'DROP TABLE foo' AS msg")
    assert r.ok is True


def test_unparseable_sql_rejected(validator):
    r = validator.validate("SELECT FROM WHERE")
    assert r.ok is False
    assert r.error is not None


def test_validation_result_dataclass():
    """ValidationResult 是 dataclass，字段名固定。"""
    r = ValidationResult(ok=True, error=None)
    assert r.ok is True
    assert r.error is None
