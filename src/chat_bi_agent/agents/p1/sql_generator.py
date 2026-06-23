"""NL → SQL：通义 qwen-max + 结构化 JSON 输出（幂等单次生成；不再含重试循环）。

重试/反思逻辑由 P1NL2SQLAgent + Reflector 编排，generator 只关心：
- system prompt
- user prompt 拼装（含可选 repair_hint）
- 调 LLM
- 解析 JSON（失败抛 InvalidJsonError）
"""

import json
import re
from dataclasses import dataclass

from langfuse import observe

from chat_bi_agent.llm import qwen_client

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
    "8. 数据范围：fct_* 表的 dt 列覆盖 2025-01-01 至 2026-09-30。"
    "如果用户问题没有指定年份，请默认 2026 年。\n"
    "9. 中文日期短语必须解读为具体范围：\n"
    "   - 上旬=当月 1-10 日；中旬=11-20 日；下旬=21 日至月末\n"
    "   - 月初=前 5 天；月末=后 5 天；月中=11-20 日\n"
    "   - X 月前后 N 天=围绕 X 月某关键日期前后各 N 天（题里若没指日，取月中 15 日）\n"
    "10. 列名严格按 schema，禁止使用未定义的别名/简写：\n"
    "    - 用 dim_customer.customer_tier，不要写成 c.tier\n"
    "    - JOIN 时显式 ON 列等值，不要假设隐式连接\n"
    "11. 两期对比（环比/同比/月度对比等）的输出列必须用 current_/prior_ 前缀：\n"
    "    - 当期列：current_<metric>（如 current_balance、current_market_value）\n"
    "    - 对照期列：prior_<metric>（如 prior_balance、prior_market_value）\n"
    "    - 差值与百分比可命名为 <metric>_change、<metric>_change_pct\n"
    "    - 不要用月份缩写当列名（如 apr_mv/may_mv），下游解析依赖 current_/prior_ 前缀\n"
    "12. 枚举字段的 WHERE 比较必须使用 schema 描述里给出的英文枚举代码，禁止用中文：\n"
    "    - customer_tier：HIGH_NET_WORTH / AFFLUENT / MASS / BASIC（题面"
    "'高净值/私行'→HIGH_NET_WORTH）\n"
    "    - account_type：CURRENT / SAVING / LOAN / CARD / INVESTMENT"
    "（题面'活期/活期存款'→CURRENT）\n"
    "    - product_category：DEPOSIT / LOAN / CARD / FUND / WEALTH / INSURANCE\n"
    "    - 不要写 WHERE customer_tier='高净值' 或 IN ('CURRENT','活期存款')"
    "——括号里的中文只是注释，不是 DB 里的值\n"
    "13. 题面只给城市/分行名（如'上海'/'杭州'/'南京分行'）而没给 branch_id 编码时，"
    "**必须** JOIN dim_branch 用 city 或 branch_name 过滤——"
    "**严禁**忽略分行名当成全行 bank-wide 查询（这会让本应聚焦特定分行的信号被全行池稀释甚至反向）：\n"
    "    - 正确：JOIN dim_branch b ON ... WHERE b.city = '上海'\n"
    "    - 正确：JOIN dim_branch b ON ... WHERE b.city IN ('杭州','南京')\n"
    "    - 错误：WHERE branch_id = 'BR_SH_0001'（这些 ID 不存在；实际 schema 用 BR_CITY_XXXX 编码）\n"
    "    - 错误：忽略\"杭州和南京分行\"直接 SELECT SUM(balance) FROM ... 不加分支过滤\n"
    "    - 多个分行用 IN：题面\"杭州和南京分行\" → WHERE b.city IN ('杭州','南京')\n"
    "\n"
    "输出示例：\n"
    "```json\n"
    "{\n"
    '  "thought": "问的是某分行的客户，dim_customer 单表即可",\n'
    '  "tables_used": ["dim_customer"],\n'
    '  "sql": "SELECT customer_id, customer_name FROM dim_customer'
    " WHERE branch_id = 'BR_CITY_0006'\"\n"
    "}\n"
    "```\n"
)


@dataclass
class SQLGenResult:
    sql: str
    thought: str
    tables_used: list[str]
    raw_response: str


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
            raise InvalidJsonError(f"无法解析为 JSON: {e}; raw 前 200 字符: {raw[:200]}") from e
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
        repair_hint: str | None,
    ) -> str:
        head = f"可用 schema：\n\n{schema_ddl}\n\n用户问题：{question}\n"
        if repair_hint is None:
            return head + "\n请输出 JSON。"
        return head + f"\n{repair_hint}\n\n请重新输出 JSON。"

    @observe(name="sql_generation")
    def generate(
        self,
        question: str,
        schema_ddl: str,
        repair_hint: str | None = None,
    ) -> SQLGenResult:
        user_prompt = self._build_user_prompt(question, schema_ddl, repair_hint)
        chat_result = qwen_client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        parsed = self._parse(chat_result.content)
        return SQLGenResult(
            sql=parsed.sql,
            thought=parsed.thought,
            tables_used=parsed.tables_used,
            raw_response=chat_result.content,
        )
