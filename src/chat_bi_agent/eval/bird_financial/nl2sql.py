"""BIRD-specific NL→SQL generator.

Deliberately separate from ``chat_bi_agent.agents.p1.sql_generator`` — that module's
system prompt is deeply tied to our own banking schema (branch_id encodings,
customer_tier enums, ``fct_*`` naming). Reusing it verbatim would inject wrong
assumptions on BIRD.

What we do reuse:
- ``chat_bi_agent.llm.qwen_client.chat`` for the LLM call.
- The ``json`` fenced block + three-field output contract, since it's a clean
  structured-output pattern.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from chat_bi_agent.llm import qwen_client

SYSTEM_PROMPT = (
    "You are an expert SQL analyst. Given a database schema and a natural-language "
    "question, produce ONE SQLite SELECT query that answers it.\n"
    "\n"
    "Strict rules:\n"
    "1. Output exactly one JSON object wrapped in a ```json``` code fence. No prose.\n"
    "2. The JSON object has three keys: `thought` (short English reasoning), "
    "`tables_used` (list of table names), and `sql` (a single SQLite SELECT statement).\n"
    "3. Target dialect is SQLite. Use SQLite-flavored syntax:\n"
    "   - Backticks or double quotes for identifiers; single quotes for string literals.\n"
    "   - Use CAST(... AS REAL) for float division; integer / integer is integer division.\n"
    "   - Use STRFTIME / date functions if needed; no PostgreSQL-only functions.\n"
    "4. Use ONLY tables and columns present in the provided schema. Never invent names.\n"
    "5. Read the `Evidence` block carefully — it defines column meanings and value enums "
    "that are not obvious from the schema alone (e.g. enum codes, formulas, unit hints). "
    "Treat evidence as authoritative and prefer values it names verbatim.\n"
    "6. When the question asks a ratio/percentage, wrap the numerator in CAST(... AS REAL) "
    "to force float division.\n"
    "7. If a JOIN is needed, use explicit INNER JOIN ... ON with equality on FK/PK columns.\n"
    "8. Do not use DDL/DML (CREATE/INSERT/UPDATE/DELETE/DROP/ALTER/PRAGMA/ATTACH).\n"
    "\n"
    "Example output format:\n"
    "```json\n"
    "{\n"
    '  "thought": "Filter accounts by district region and frequency code.",\n'
    '  "tables_used": ["account", "district"],\n'
    '  "sql": "SELECT COUNT(a.account_id) FROM district d '
    "INNER JOIN account a ON d.district_id = a.district_id "
    "WHERE d.A3 = 'east Bohemia' AND a.frequency = 'POPLATEK PO OBRATU'\"\n"
    "}\n"
    "```\n"
)

_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class NL2SQLParseError(Exception):
    """LLM output was not a well-formed JSON with the required fields."""


@dataclass
class NL2SQLResult:
    sql: str
    thought: str
    tables_used: list[str]
    raw_response: str
    prompt_tokens: int
    completion_tokens: int


def _build_user_prompt(schema_block: str, question: str, evidence: str) -> str:
    parts = [
        "Schema:",
        "",
        schema_block,
        "",
        f"Question: {question}",
    ]
    if evidence:
        # Cap evidence length so it doesn't crowd out the schema.
        cropped = evidence.strip()
        if len(cropped) > 500:
            cropped = cropped[:497] + "..."
        parts.append(f"Evidence: {cropped}")
    parts.append("")
    parts.append("Return the JSON object now.")
    return "\n".join(parts)


def _parse(raw: str) -> tuple[str, str, list[str]]:
    m = _JSON_FENCE_RE.search(raw)
    candidate = m.group(1) if m else raw.strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise NL2SQLParseError(f"not valid JSON: {e}; raw first 200 chars: {raw[:200]}") from e
    for key in ("thought", "tables_used", "sql"):
        if key not in data:
            raise NL2SQLParseError(f"missing key {key}; got {list(data.keys())}")
    if not isinstance(data["tables_used"], list):
        raise NL2SQLParseError("tables_used must be a list")
    if not isinstance(data["sql"], str) or not data["sql"].strip():
        raise NL2SQLParseError("sql must be a non-empty string")
    return str(data["sql"]).strip(), str(data["thought"]), list(data["tables_used"])


def generate_sql(
    schema_block: str,
    question: str,
    evidence: str,
    temperature: float = 0.0,
) -> NL2SQLResult:
    """Single-shot NL→SQL. No retry loop — BIRD scoring absorbs failures."""
    user_prompt = _build_user_prompt(schema_block, question, evidence)
    chat_result = qwen_client.chat(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=temperature,
    )
    sql, thought, tables = _parse(chat_result.content)
    return NL2SQLResult(
        sql=sql,
        thought=thought,
        tables_used=tables,
        raw_response=chat_result.content,
        prompt_tokens=chat_result.prompt_tokens,
        completion_tokens=chat_result.completion_tokens,
    )
