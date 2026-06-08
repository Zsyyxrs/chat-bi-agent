"""Reflector：错误分类 → RETRY/GIVE_UP 决策 + repair hint 拼装。无 LLM、无 IO。"""

from dataclasses import dataclass
from enum import Enum

from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass


class ReflectAction(str, Enum):
    RETRY = "RETRY"
    GIVE_UP = "GIVE_UP"


@dataclass
class ReflectDecision:
    action: ReflectAction
    repair_hint: str | None


class Reflector:
    """无 LLM、无 IO；纯函数风格便于测试。

    决策表（见 spec §4）：
    - TIMEOUT 永远 GIVE_UP
    - attempt >= max_attempts 永远 GIVE_UP
    - 其他错误类 RETRY，并拼一段定向 repair hint
    """

    def __init__(self, max_attempts: int = 3):
        self.max_attempts = max_attempts

    def reflect(
        self,
        err_class: SQLErrorClass,
        err_msg: str,
        prev_sql: str | None,
        top_k_tables: list[str],
        attempt: int,
    ) -> ReflectDecision:
        """决策当前错误是 RETRY 还是 GIVE_UP，并在 RETRY 时拼修复 hint。

        prev_sql 暂未参与 hint 生成（保留参数：后续若要把上次 SQL 回灌给 LLM 做 diff 提示再用）；
        当前 caller (P1NL2SQLAgent) 仍按契约传入。
        """
        # 1. TIMEOUT 立即放弃，重试也是再 timeout
        if err_class == SQLErrorClass.TIMEOUT:
            return ReflectDecision(action=ReflectAction.GIVE_UP, repair_hint=None)

        # 2. attempt 已达/超上限：放弃
        if attempt >= self.max_attempts:
            return ReflectDecision(action=ReflectAction.GIVE_UP, repair_hint=None)

        # 3. 按 err_class 拼 hint，RETRY
        hint = self._build_hint(err_class, err_msg, top_k_tables)
        return ReflectDecision(action=ReflectAction.RETRY, repair_hint=hint)

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
            return f"上次 SQL 语法错（PG）：{err_msg}。请修正。"
        if err_class == SQLErrorClass.UNKNOWN_COLUMN:
            return f"上次用了不存在的列，请仔细核对 schema 中的列名；完整错误：{err_msg}"
        if err_class == SQLErrorClass.UNKNOWN_TABLE:
            tables_str = ", ".join(top_k_tables)
            return (
                f"上次用了不存在的表，仅允许使用以下表：{tables_str}。"
                f"完整错误：{err_msg}"
            )
        # OTHER 兜底
        return f"上次执行失败：{err_msg}。请修正。"
