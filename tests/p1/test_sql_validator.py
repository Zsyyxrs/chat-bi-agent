"""SQLValidator 单测：sqlglot AST 静态检查。无 PG 依赖。"""

import pytest

from chat_bi_agent.agents.p1.sql_validator import SQLValidator, ValidationResult


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


# ── 数据读取型函数黑名单 ──────────────────────────────────────────────
# 顶层白名单只管「语句类型」，管不到「SELECT 里调了什么函数」。
# 这类函数从 MDL/catalog 之外读字节：本地文件（路径穿越）、对象存储/URL
# （SSRF、数据外泄）、其它库（横向移动）。参考 WrenAI policy.py 的同类防护。


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('/etc/shadow')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(1, '/tmp/leak')",
        "SELECT * FROM pg_ls_dir('/')",
    ],
)
def test_rejects_data_reader_functions(validator, sql):
    r = validator.validate(sql)
    assert r.ok is False
    assert "禁止函数" in (r.error or "")


def test_data_reader_rejected_case_insensitively(validator):
    r = validator.validate("SELECT PG_READ_FILE('/etc/passwd')")
    assert r.ok is False


def test_data_reader_rejected_in_nested_position(validator):
    """非 FROM 位置也要拦——投影、WHERE、子查询里都算。"""
    sql = "SELECT id FROM dim_customer WHERE name = pg_read_file('/etc/passwd')"
    assert validator.validate(sql).ok is False


def test_data_reader_rejected_inside_subquery(validator):
    sql = "SELECT * FROM (SELECT dblink('h', 'SELECT 1') AS c) t"
    assert validator.validate(sql).ok is False


def test_ordinary_functions_still_allowed(validator):
    """别误伤正常聚合/日期函数。"""
    sql = (
        "SELECT AVG(balance), DATE_TRUNC('month', dt), COUNT(DISTINCT customer_id) "
        "FROM fct_balance_daily GROUP BY 2"
    )
    assert validator.validate(sql).ok is True


# ── 跨方言执行/外泄型函数 ────────────────────────────────────────────
# 2026-09-02：对照 SQLBot 的分库危险函数表（backend/apps/db/db.py:1025）
# 补齐我们缺的三类。原有黑名单偏 PG/DuckDB，MySQL/MSSQL/Oracle 一个没有——
# `dialect` 是构造参数，将来指到别的库时那几类就是裸奔。


@pytest.mark.parametrize(
    "sql",
    [
        # MySQL —— 读本地文件
        "SELECT load_file('/etc/passwd')",
        # SQL Server —— 命令执行 / 动态 SQL / 外部数据源
        "SELECT xp_cmdshell('whoami')",
        "SELECT sp_executesql('SELECT 1')",
        "SELECT * FROM openrowset('SQLNCLI', 'server=evil', 'SELECT 1')",
        "SELECT * FROM opendatasource('SQLNCLI', 'server=evil').db.dbo.t",
        "SELECT * FROM openquery(linked, 'SELECT 1')",
        # PostgreSQL —— 原黑名单漏掉的文件/进程面
        "SELECT pg_ls_logdir()",
        "SELECT pg_file_read('/etc/passwd', 0, 100)",
        "SELECT pg_terminate_backend(123)",
    ],
)
def test_rejects_cross_dialect_dangerous_functions(validator, sql):
    r = validator.validate(sql)
    assert r.ok is False
    assert "禁止函数" in (r.error or "")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT utl_http.request('http://evil/x') FROM dual",
        "SELECT utl_file.fopen('D', 'f', 'r') FROM dual",
        "SELECT dbms_pipe.receive_message('x') FROM dual",
        "SELECT dbms_lock.sleep(10) FROM dual",
    ],
)
def test_rejects_oracle_package_calls(validator, sql):
    """Oracle 的 `包名.函数名` 在 sqlglot 里是 Dot(Identifier, Anonymous)。

    按函数名匹配拦不到——`utl_http.request` 的 Anonymous 只叫 `request`，
    而 `request` 本身不能进黑名单（误伤面太大）。必须按**包名前缀**拦。
    """
    r = validator.validate(sql)
    assert r.ok is False
    assert "禁止" in (r.error or "")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT version()",
        "SELECT current_user",
        "SELECT current_database()",
        "SELECT inet_server_addr()",
    ],
)
def test_rejects_recon_functions(validator, sql):
    """侦察类函数：对指标口径驱动的 BI 查询零业务价值，但会泄露库版本/账号/网络面。

    SQLBot 把 version/current_user/user/database 一并列为危险函数，同一判断。
    """
    assert validator.validate(sql).ok is False


def test_user_column_still_allowed(validator):
    """`user` / `session_user` 裸写在 sqlglot 里解析成 Column 而非 Func。

    即：这两个词我们**拦不到**，也不该假装拦得到——把它们塞进函数黑名单
    只会误伤名为 user 的业务列。这条测试锁死「不过度拦截」。
    """
    assert validator.validate("SELECT user FROM dim_customer").ok is True
    assert validator.validate("SELECT session_user FROM dim_customer").ok is True
