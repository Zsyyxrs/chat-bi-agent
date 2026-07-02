"""Reflector：错误分类 → RETRY/GIVE_UP 决策 + repair hint 拼装。无 LLM、无 IO。"""

import re
from dataclasses import dataclass
from enum import Enum

from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass

# Cross-dialect syntactic tells. If prev_sql matches AND target dialect is the
# opposite family, treat as DIALECT_MISMATCH.
_PG_ONLY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bEXTRACT\s*\(\s*\w+\s+FROM\b", re.IGNORECASE),
     "EXTRACT(YEAR/MONTH/DAY FROM col) 是 PG 独有；SQLite 用 STRFTIME('%Y', col) / '%m' / '%d'"),
    (re.compile(r"\bDATE\s+'\d{4}-\d{2}-\d{2}'"),
     "DATE 'YYYY-MM-DD' 字面量是 PG 独有；SQLite 直接写 'YYYY-MM-DD'（无 DATE 前缀）"),
    (re.compile(r"\bILIKE\b", re.IGNORECASE),
     "ILIKE 是 PG 独有；SQLite 用 LOWER(col) LIKE LOWER('...') 或 col LIKE '...' COLLATE NOCASE"),
    (re.compile(r"\bDATE_PART\s*\(", re.IGNORECASE),
     "DATE_PART() 是 PG 独有；SQLite 用 STRFTIME(...)"),
    (re.compile(r"\bTO_CHAR\s*\(", re.IGNORECASE),
     "TO_CHAR() 是 PG 独有；SQLite 用 STRFTIME() 格式化"),
]

_SQLITE_ONLY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bSTRFTIME\s*\(", re.IGNORECASE),
     "STRFTIME() 是 SQLite 独有；PG 用 EXTRACT(YEAR FROM col) 或 TO_CHAR()"),
    (re.compile(r"\bIIF\s*\(", re.IGNORECASE),
     "IIF() 是 SQLite/T-SQL 独有；PG 用 CASE WHEN ... THEN ... ELSE ... END"),
]


def _detect_dialect_mismatch(prev_sql: str | None, target_dialect: str) -> list[str]:
    """Return a list of dialect-fix hints if prev_sql clashes with target_dialect.

    Empty list means "no mismatch detected".
    """
    if not prev_sql:
        return []
    hints: list[str] = []
    if target_dialect == "sqlite":
        # SQLite target — prev_sql should not carry PG-only patterns
        for pat, fix in _PG_ONLY_PATTERNS:
            if pat.search(prev_sql):
                hints.append(fix)
    elif target_dialect == "postgres":
        for pat, fix in _SQLITE_ONLY_PATTERNS:
            if pat.search(prev_sql):
                hints.append(fix)
    return hints


class ReflectAction(str, Enum):
    RETRY = "RETRY"
    GIVE_UP = "GIVE_UP"


@dataclass
class ReflectDecision:
    action: ReflectAction
    repair_hint: str | None
    # If Reflector re-classified SYNTAX_ERROR → DIALECT_MISMATCH after inspecting
    # prev_sql, this holds the effective class for logging / reflect_history.
    effective_err_class: SQLErrorClass | None = None


class Reflector:
    """无 LLM、无 IO；纯函数风格便于测试。

    决策表（见 spec §4）：
    - TIMEOUT 永远 GIVE_UP
    - attempt >= max_attempts 永远 GIVE_UP
    - 其他错误类 RETRY，并拼一段定向 repair hint
    - SYNTAX_ERROR 时会检查 prev_sql，若命中跨方言模式（如 SQLite 目标下用了 EXTRACT/
      DATE 'YYYY-MM-DD'/ILIKE），升级为 DIALECT_MISMATCH 并给方言特定 hint
    """

    def __init__(self, max_attempts: int = 3, dialect: str = "postgres"):
        self.max_attempts = max_attempts
        self.dialect = dialect

    def reflect(
        self,
        err_class: SQLErrorClass,
        err_msg: str,
        prev_sql: str | None,
        top_k_tables: list[str],
        attempt: int,
    ) -> ReflectDecision:
        """决策当前错误是 RETRY 还是 GIVE_UP，并在 RETRY 时拼修复 hint。"""
        # 1. TIMEOUT 立即放弃，重试也是再 timeout
        if err_class == SQLErrorClass.TIMEOUT:
            return ReflectDecision(action=ReflectAction.GIVE_UP, repair_hint=None)

        # 2. attempt 已达/超上限：放弃
        if attempt >= self.max_attempts:
            return ReflectDecision(action=ReflectAction.GIVE_UP, repair_hint=None)

        # 3. 若是 SYNTAX_ERROR 且 prev_sql 命中跨方言模式，升级为 DIALECT_MISMATCH
        effective_class = err_class
        if err_class == SQLErrorClass.SYNTAX_ERROR:
            dialect_fixes = _detect_dialect_mismatch(prev_sql, self.dialect)
            if dialect_fixes:
                effective_class = SQLErrorClass.DIALECT_MISMATCH
                hint = self._build_dialect_hint(err_msg, dialect_fixes)
                return ReflectDecision(
                    action=ReflectAction.RETRY,
                    repair_hint=hint,
                    effective_err_class=effective_class,
                )

        # 4. 按 err_class 拼 hint，RETRY
        hint = self._build_hint(err_class, err_msg, top_k_tables)
        return ReflectDecision(action=ReflectAction.RETRY, repair_hint=hint)

    def _build_dialect_hint(self, err_msg: str, fixes: list[str]) -> str:
        bullets = "\n".join(f"- {f}" for f in fixes)
        return (
            f"上次 SQL 用了目标方言（{self.dialect}）不认的语法，导致 syntax error：\n"
            f"{bullets}\n"
            f"完整错误：{err_msg}\n"
            f"请按上述规则重写 SQL。"
        )

    def _build_hint(
        self,
        err_class: SQLErrorClass,
        err_msg: str,
        top_k_tables: list[str],
    ) -> str:
        if err_class == SQLErrorClass.INVALID_JSON:
            return (
                "上次输出不是合法 JSON，请严格用 ```json``` 代码块包裹，"
                "必须包含 thought、tables_used、sql 三个字段。"
            )
        if err_class == SQLErrorClass.VALIDATOR_FAIL:
            return (
                f"上次 SQL 没通过解析：{err_msg}。仅允许 SELECT 或 WITH 顶层语句，"
                "禁止任何 DML/DDL（INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/"
                "GRANT/COPY/MERGE）。"
            )
        if err_class == SQLErrorClass.SYNTAX_ERROR:
            return f"上次 SQL 语法错（{self.dialect}）：{err_msg}。请修正。"
        if err_class == SQLErrorClass.UNKNOWN_COLUMN:
            return f"上次用了不存在的列，请仔细核对 schema 中的列名；完整错误：{err_msg}"
        if err_class == SQLErrorClass.UNKNOWN_TABLE:
            tables_str = ", ".join(top_k_tables)
            return f"上次用了不存在的表，仅允许使用以下表：{tables_str}。完整错误：{err_msg}"
        # OTHER 兜底
        return f"上次执行失败：{err_msg}。请修正。"
