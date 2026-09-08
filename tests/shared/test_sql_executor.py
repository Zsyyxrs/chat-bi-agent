"""SQLExecutor 测试：白名单 + 错误分类。

部分测试需要真实 PG，标记 @pytest.mark.integration，可用 `pytest -m "not integration"` 跳过。
"""

import pytest

from chat_bi_agent.agents.shared.sql_executor import (
    SQLErrorClass,
    SQLExecutor,
    UnsafeSQLError,
)
from tests.pg_probe import PG_UP, SKIP_REASON


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
    if not PG_UP:
        pytest.skip(SKIP_REASON)
    executor = SQLExecutor()
    rows, err = executor.execute("SELECT customer_id FROM dim_customer LIMIT 1")
    assert err is None
    assert len(rows) >= 0  # 表可能为空也 OK


@pytest.mark.integration
def test_unknown_table_against_real_pg():
    if not PG_UP:
        pytest.skip(SKIP_REASON)
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
        "chat_bi_agent.agents.shared.sql_executor.psycopg2.connect",
        fake_connect,
    )
    executor = SQLExecutor(statement_timeout_ms=10_000)
    with pytest.raises(RuntimeError):
        executor.execute("SELECT 1")
    assert "statement_timeout=10000" in captured.get("options", "")


# ---- 安全护栏改为 AST 判定 + fail-closed（2026-09-08）----
#
# 原实现是关键字正则 `\b(DROP|TRUNCATE|DELETE|...)\b`。它和评分器里那两个 bug 同族：
# 用正则读 SQL，分不清关键字出现在**语句位置**还是**字符串字面量/注释**里。
# 实测误伤：`WHERE description = 'DELETE'`、`SELECT 1 -- INSERT` 都会被拦死，
# 而它们是完全合法的只读查询。
#
# 改成 sqlglot 解析后判语句类型。**解析失败一律拒绝执行**（fail-closed）：
# 这是安全代码，"我看不懂所以放行"是最不该有的行为。代价是 sqlglot 不认的合法
# 方言写法会被误拒——可接受，因为 P1 主路径上游的 sql_validator 本来就先用
# sqlglot 解析过一遍，能到这里的 SQL 都是解析得动的。
#
# 真正的只读控制始终是 PG_READONLY_USER 这个只读角色（ADR-010），本层是纵深防御。


def test_keyword_inside_a_string_literal_is_not_a_write():
    executor = SQLExecutor()
    assert executor._is_safe("SELECT * FROM fct_transaction WHERE description = 'DELETE'") is True


def test_keyword_inside_a_comment_is_not_a_write():
    executor = SQLExecutor()
    assert executor._is_safe("SELECT 1 -- 这条不做 INSERT") is True


def test_unparseable_sql_is_refused_not_passed_through():
    """fail-closed：解析不了就拒绝。安全层不能因为「我看不懂」而放行。"""
    executor = SQLExecutor()
    assert executor._is_safe("SELECT FROM WHERE ((") is False


def test_write_hidden_inside_a_cte_is_refused():
    """PG 支持 `WITH x AS (DELETE ... RETURNING *) SELECT ...`——
    根节点是 SELECT，但它真的会删数据。只看开头两个词的护栏挡不住这个。

    不过要说清楚：被替换掉的那版词边界正则**也拦得住这条**（它匹配全串而非 startswith）。
    保留本测试是当回归锚——AST 化不能在这个方向上开倒车。"""
    executor = SQLExecutor()
    sql = "WITH gone AS (DELETE FROM dim_customer RETURNING *) SELECT * FROM gone"
    assert executor._is_safe(sql) is False


def test_multiple_statements_are_refused():
    executor = SQLExecutor()
    assert executor._is_safe("SELECT 1; DROP TABLE dim_customer") is False


def test_plain_cte_select_is_still_allowed():
    executor = SQLExecutor()
    assert executor._is_safe("WITH a AS (SELECT 1 AS x) SELECT * FROM a") is True


def test_set_operations_are_allowed():
    executor = SQLExecutor()
    assert executor._is_safe("SELECT 1 UNION ALL SELECT 2") is True


def test_non_select_command_is_refused():
    """VACUUM / CALL 这类会被 sqlglot 解析成 Command，一律拒绝。"""
    executor = SQLExecutor()
    assert executor._is_safe("VACUUM dim_customer") is False
