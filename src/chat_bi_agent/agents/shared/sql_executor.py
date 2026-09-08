"""执行 SQL：只读 PG 用户 + SELECT 白名单 + 错误分类 + statement_timeout。"""

import os
import re
from decimal import Decimal
from enum import Enum

import psycopg2
import sqlglot
from psycopg2.extras import RealDictCursor

from chat_bi_agent.config import PG_STATEMENT_TIMEOUT_MS
from chat_bi_agent.obs.span_kind import observe_local


def _pyify_value(v: object) -> object:
    """把 psycopg2 从 NUMERIC 列返回的 Decimal 转成 float。
    Why: Langfuse SDK 的默认序列化器对 Decimal 走兜底分支，
    trace 里 aum/balance 等字段会存成字面字符串 "<Decimal>"，
    真实数值丢失。float 是 JSON 原生类型，能被 SDK 直接序列化。
    """
    if isinstance(v, Decimal):
        return float(v)
    return v


class UnsafeSQLError(Exception):
    """SQL 包含禁止的关键字（DML/DDL）。"""


class SQLErrorClass(str, Enum):
    SYNTAX_ERROR = "SYNTAX_ERROR"
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    TIMEOUT = "TIMEOUT"
    OTHER = "OTHER"
    INVALID_JSON = "INVALID_JSON"  # agent 层从 SQLGenerator 抛出的错误映射
    VALIDATOR_FAIL = "VALIDATOR_FAIL"  # agent 层从 SQLValidator 失败映射
    DIALECT_MISMATCH = "DIALECT_MISMATCH"  # Reflector 侧再分类：prev_sql 用了目标方言不认的语法


# SET / EXECUTE / CALL 不单列：每次 execute() 都用全新连接，会话副作用
# （改 search_path、预备语句）无法留存。
#
# 2026-09-08：从关键字正则改为 AST 判定。原实现分不清关键字出现在**语句位置**
# 还是**字符串字面量/注释**里，实测把 `WHERE description = 'DELETE'` 和
# `SELECT 1 -- INSERT` 这类合法只读查询直接拦死。它与评分器里那两个 bug 同族
# （用正则读 SQL），只是误伤方向相反。
#
# 保留正则常量只为向后兼容外部引用；判定不再走它。
FORBIDDEN_PATTERN = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|UPDATE|INSERT|ALTER|GRANT|REVOKE|CREATE|COPY|VACUUM|MERGE)\b",
    re.IGNORECASE,
)

# 允许的根节点：只读查询及其集合运算
_READ_ONLY_ROOTS = (
    sqlglot.exp.Select,
    sqlglot.exp.Union,
    sqlglot.exp.Except,
    sqlglot.exp.Intersect,
    sqlglot.exp.Subquery,
)

# 树内任意位置出现即拒绝。CTE 里藏写操作是真实可行的
# （PG 支持 `WITH x AS (DELETE ... RETURNING *) SELECT * FROM x`，
# 根节点是 SELECT 但它真的删数据），只看开头两个词的护栏挡不住。
# Command 兜住 sqlglot 解析不出具体类型的语句（VACUUM / CALL 之类）。
_WRITE_NODES = (
    sqlglot.exp.Insert,
    sqlglot.exp.Update,
    sqlglot.exp.Delete,
    sqlglot.exp.Drop,
    sqlglot.exp.Create,
    sqlglot.exp.Alter,
    sqlglot.exp.TruncateTable,
    sqlglot.exp.Merge,
    sqlglot.exp.Copy,
    sqlglot.exp.Grant,
    sqlglot.exp.Command,
)


class SQLExecutor:
    def __init__(self, statement_timeout_ms: int = PG_STATEMENT_TIMEOUT_MS):
        self.host = os.environ.get("PG_HOST", "localhost")
        self.port = int(os.environ.get("PG_PORT", "5432"))
        self.database = os.environ.get("PG_DATABASE", "chatbi")
        self.user = os.environ.get("PG_READONLY_USER", "chatbi_readonly")
        self.password = os.environ.get("PG_READONLY_PASSWORD", "readonly_dev")
        self.statement_timeout_ms = statement_timeout_ms

    def _is_safe(self, sql: str) -> bool:
        """只读白名单。**解析失败一律拒绝（fail-closed）**。

        安全层不能因为「我看不懂」而放行。代价是 sqlglot 不认的合法方言写法会被误拒——
        可接受：P1 主路径上游的 SQLValidator 本来就先用 sqlglot 解析过一遍，
        能走到这里的 SQL 都是解析得动的。

        注意本层是**纵深防御**，真正的只读控制是 PG_READONLY_USER 这个只读角色（ADR-010）。
        """
        try:
            statements = sqlglot.parse(sql, read="postgres")
        except Exception:
            return False
        # 多语句一律拒绝：`SELECT 1; DROP TABLE t` 的第一条是无辜的
        if len(statements) != 1 or statements[0] is None:
            return False
        stmt = statements[0]
        if not isinstance(stmt, _READ_ONLY_ROOTS):
            return False
        return not any(isinstance(node, _WRITE_NODES) for node in stmt.walk())

    @observe_local(name="sql_execution")
    def execute(self, sql: str) -> tuple[list[dict] | None, str | None]:
        """执行 SQL。返回 (rows, error)：
        - 成功：(rows, None)
        - 失败：(None, error_message)
        - 触发白名单：抛 UnsafeSQLError
        """
        if not self._is_safe(sql):
            raise UnsafeSQLError(f"SQL 触发安全护栏: {sql[:200]}")

        conn = None
        try:
            conn = psycopg2.connect(
                dbname=self.database,
                user=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                options=f"-c statement_timeout={self.statement_timeout_ms}",
            )
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                rows = [{k: _pyify_value(v) for k, v in r.items()} for r in cur.fetchall()]
            return rows, None
        except psycopg2.Error as e:
            err_msg = str(e).strip()
            return None, err_msg
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def classify_error(error_msg: str) -> SQLErrorClass:
        msg = error_msg.lower()
        # TIMEOUT 必须排在 SYNTAX/UNKNOWN_* 之前，避免错误文本里偶现 "syntax" 子串导致误分类
        if "canceling statement due to statement timeout" in msg:
            return SQLErrorClass.TIMEOUT
        if "syntax error" in msg:
            return SQLErrorClass.SYNTAX_ERROR
        if "column" in msg and "does not exist" in msg:
            return SQLErrorClass.UNKNOWN_COLUMN
        if "relation" in msg and "does not exist" in msg:
            return SQLErrorClass.UNKNOWN_TABLE
        return SQLErrorClass.OTHER
