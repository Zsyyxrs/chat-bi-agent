"""NL → SQL：通义 qwen-max + 结构化 JSON 输出 + 最多 3 次重试 + 错误分类。"""

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from langfuse import observe

from chat_bi_agent.agents.sql_executor import SQLErrorClass, SQLExecutor
from chat_bi_agent.llm import qwen_client

MAX_ATTEMPTS = 3


SYSTEM_PROMPT = (
    "你是一个银行业务 NL2SQL 助手。根据用户的中文问题和给定的表 schema，"
    "生成一条 PostgreSQL SELECT 查询。\n"
    "\n"
    "严格要求：\n"
    "1. 只输出一个 JSON 对象，必须用 ```json``` 代码块包裹\n"
    "2. JSON 三个字段：thought（中文思路）、tables_used（字符串数组）、sql（SQL 字符串）\n"
    "3. SQL 只能是 SELECT 或 WITH，禁止 DML/DDL\n"
    "4. SQL 只能使用下面给定的表，禁止编造表/列名\n"
    "5. 日期字段是 DATE 类型，请用 DATE 'YYYY-MM-DD' 字面量\n"
    "6. 字符串值用单引号\n"
    "7. 列名/表名严格按 schema 给定大小写\n"
    "\n"
    "输出示例：\n"
    "```json\n"
    "{\n"
    '  "thought": "问的是某分行的客户，dim_customer 单表即可",\n'
    '  "tables_used": ["dim_customer"],\n'
    "  \"sql\": \"SELECT customer_id, customer_name FROM dim_customer"
    " WHERE branch_id = 'BR_CITY_0006'\"\n"
    "}\n"
    "```\n"
)


@dataclass
class SQLGenResult:
    sql: str | None
    rows: list[dict] | None
    error: str | None
    thought: str
    tables_used: list[str]
    attempts: int
    llm_history: list[dict] = field(default_factory=list)


@dataclass
class _ParsedLLMOutput:
    thought: str
    tables_used: list[str]
    sql: str


class InvalidJsonError(Exception):
    pass


# non-greedy `\{.*?\}` 配合 ```fence``` 锚定到第一对完整的 JSON 大括号；
# 嵌套 brace（如 SQL 中含 {placeholder}）能正确匹配到 fence 之前的最后一个 `}`。
JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class SQLGenerator:

    def _parse(self, raw: str) -> _ParsedLLMOutput:
        m = JSON_FENCE_RE.search(raw)
        candidate = m.group(1) if m else raw.strip()
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as e:
            raise InvalidJsonError(f"无法解析为 JSON: {e}; raw 前 200 字符: {raw[:200]}")
        for key in ("thought", "tables_used", "sql"):
            if key not in data:
                raise InvalidJsonError(f"缺少字段 {key}; 实际: {list(data.keys())}")
        if not isinstance(data["tables_used"], list):
            raise InvalidJsonError("tables_used 必须是 list")
        return _ParsedLLMOutput(
            thought=data["thought"],
            tables_used=data["tables_used"],
            sql=data["sql"],
        )

    def _build_user_prompt(
        self,
        question: str,
        schema_ddl: str,
        prev_error: str | None,
        prev_sql: str | None,
        prev_tables: list[str] | None,
    ) -> str:
        head = f"可用 schema：\n\n{schema_ddl}\n\n用户问题：{question}\n"
        if prev_error is None:
            return head + "\n请输出 JSON。"
        err_class = SQLExecutor.classify_error(prev_error)
        if err_class == SQLErrorClass.SYNTAX_ERROR:
            hint = f"你上次生成的 SQL 语法错误：{prev_error}。请修正。"
        elif err_class == SQLErrorClass.UNKNOWN_TABLE:
            hint = f"你上次用了不存在的表，请仔细核对上方 schema 中的表名；完整错误：{prev_error}"
        elif err_class == SQLErrorClass.UNKNOWN_COLUMN:
            hint = f"你上次用了不存在的列，请仔细核对 schema 中的列名；完整错误：{prev_error}"
        else:
            hint = f"你上次执行失败：{prev_error}。请修正。"
        return head + f"\n上次尝试的 SQL：\n{prev_sql}\n\n{hint}\n\n请重新输出 JSON。"

    def _build_user_prompt_for_invalid_json(self, question: str, schema_ddl: str) -> str:
        return (
            f"可用 schema：\n\n{schema_ddl}\n\n"
            f"用户问题：{question}\n\n"
            "你上次输出不是合法 JSON 或缺字段。请严格用 ```json``` 代码块包裹，"
            "包含 thought、tables_used、sql 三个字段。"
        )

    @observe(name="sql_generation")
    def generate(
        self,
        question: str,
        schema_ddl: str,
        execute_fn: Callable[[str], tuple[list[dict] | None, str | None]],
    ) -> SQLGenResult:
        prev_error: str | None = None
        prev_sql: str | None = None
        prev_tables: list[str] | None = None
        history: list[dict] = []

        last_parsed: _ParsedLLMOutput | None = None
        last_rows: list[dict] | None = None
        last_error: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if prev_error == "__INVALID_JSON__":
                user_prompt = self._build_user_prompt_for_invalid_json(question, schema_ddl)
            else:
                user_prompt = self._build_user_prompt(
                    question, schema_ddl, prev_error, prev_sql, prev_tables,
                )

            chat_result = qwen_client.chat(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            history.append({"attempt": attempt, "raw": chat_result.content})

            try:
                parsed = self._parse(chat_result.content)
            except InvalidJsonError as e:
                prev_error = "__INVALID_JSON__"
                prev_sql = None
                last_error = str(e)
                continue

            last_parsed = parsed
            rows, exec_err = execute_fn(parsed.sql)
            last_rows = rows
            last_error = exec_err

            if exec_err is None:
                return SQLGenResult(
                    sql=parsed.sql,
                    rows=rows,
                    error=None,
                    thought=parsed.thought,
                    tables_used=parsed.tables_used,
                    attempts=attempt,
                    llm_history=history,
                )

            prev_error = exec_err
            prev_sql = parsed.sql
            prev_tables = parsed.tables_used

        return SQLGenResult(
            sql=last_parsed.sql if last_parsed else None,
            rows=last_rows,
            error=last_error,
            thought=last_parsed.thought if last_parsed else "",
            tables_used=last_parsed.tables_used if last_parsed else [],
            attempts=MAX_ATTEMPTS,
            llm_history=history,
        )
