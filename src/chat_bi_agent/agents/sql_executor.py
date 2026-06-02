"""执行 SQL：只读 PG 用户 + SELECT 白名单 + 错误分类。"""

import os
import re
from dataclasses import dataclass
from enum import Enum

import psycopg2
from psycopg2.extras import RealDictCursor
from langfuse import observe


class UnsafeSQLError(Exception):
    """SQL 包含禁止的关键字（DML/DDL）。"""


class SQLErrorClass(str, Enum):
    SYNTAX_ERROR = "SYNTAX_ERROR"
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    OTHER = "OTHER"


FORBIDDEN_PATTERN = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|UPDATE|INSERT|ALTER|GRANT|REVOKE|CREATE|COPY|VACUUM|MERGE)\b",
    re.IGNORECASE,
)


class SQLExecutor:
    def __init__(self):
        self.host = os.environ.get("PG_HOST", "localhost")
        self.port = int(os.environ.get("PG_PORT", "5432"))
        self.database = os.environ.get("PG_DATABASE", "chatbi")
        self.user = os.environ.get("PG_READONLY_USER", "chatbi_readonly")
        self.password = os.environ.get("PG_READONLY_PASSWORD", "readonly_dev")

    def _is_safe(self, sql: str) -> bool:
        if FORBIDDEN_PATTERN.search(sql):
            return False
        stripped = sql.strip().lower()
        # 必须以 select 或 with 开头（with 用于 CTE）
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
        if "syntax error" in msg:
            return SQLErrorClass.SYNTAX_ERROR
        if "relation" in msg and "does not exist" in msg:
            return SQLErrorClass.UNKNOWN_TABLE
        if "column" in msg and "does not exist" in msg:
            return SQLErrorClass.UNKNOWN_COLUMN
        return SQLErrorClass.OTHER
