"""执行 SQL：只读 PG 用户 + SELECT 白名单 + 错误分类 + statement_timeout。"""

import os
import re
from enum import Enum

import psycopg2
from langfuse import observe
from psycopg2.extras import RealDictCursor

from chat_bi_agent.config import PG_STATEMENT_TIMEOUT_MS


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


# SET / EXECUTE / CALL are not listed: each execute() call uses a fresh connection,
# so session effects (search_path tampering, prepared statements) cannot persist.
FORBIDDEN_PATTERN = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|UPDATE|INSERT|ALTER|GRANT|REVOKE|CREATE|COPY|VACUUM|MERGE)\b",
    re.IGNORECASE,
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
        if FORBIDDEN_PATTERN.search(sql):
            return False
        stripped = sql.strip().lower()
        return stripped.startswith("select") or stripped.startswith("with")

    @observe(name="sql_execution")
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
                rows = [dict(r) for r in cur.fetchall()]
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
