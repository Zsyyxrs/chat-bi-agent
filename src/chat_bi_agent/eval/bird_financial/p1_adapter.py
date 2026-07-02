"""Run the production P1NL2SQLAgent against BIRD-financial, unchanged except for
executor and schema.

The point of this adapter is to isolate ONE variable — the P1 orchestration and
its Chinese-banking system prompt — and measure how much it costs on a
cross-domain benchmark. Everything else that would obscure the comparison
(SchemaLinker retrieval against our own YAML, the psycopg2 executor, our own
DDL text) is stubbed with BIRD equivalents:

- SchemaLoader → per-table stub built from BIRD's dev_tables.json + description CSVs
- SchemaLinker → returns *all* 8 BIRD tables verbatim (small schema, no ranking needed)
- SQLExecutor → SQLite-backed adapter that mimics the (rows, error_msg) contract
- SQLGenerator / SQLValidator / Reflector → **unchanged** (this is the whole point)

The result is a P1NL2SQLAgent instance whose ``run(question_id, question)`` works
verbatim; caller composes it with the BIRD EX scorer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from chat_bi_agent.agents.p1.nl2sql_agent import MAX_ATTEMPTS, P1NL2SQLAgent
from chat_bi_agent.agents.p1.reflector import Reflector
from chat_bi_agent.agents.p1.sql_generator import SQLGenerator
from chat_bi_agent.agents.p1.sql_validator import SQLValidator
from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass, SQLExecutor
from chat_bi_agent.eval.bird_financial.schema_prompt import build_financial_schema_block
from chat_bi_agent.eval.bird_financial.sqlite_executor import (
    BirdSQLiteExecutor,
    ExecutorRuntimeError,
    ExecutorSyntaxError,
    ExecutorTimeout,
    ExecutorUnsafeSQL,
)

# ---------------------------- schema stubs ----------------------------


def _split_schema_block_by_table(block: str) -> dict[str, str]:
    """Split the single schema block into one chunk per ``Table: NAME`` section."""
    per_table: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in block.splitlines():
        if line.startswith("Table: "):
            if current is not None:
                per_table[current] = "\n".join(buf).rstrip()
            current = line[len("Table: "):].strip()
            buf = [line]
        elif current is not None:
            buf.append(line)
    if current is not None:
        per_table[current] = "\n".join(buf).rstrip()
    return per_table


class _BirdSchemaLoaderStub:
    """Fulfills the subset of SchemaLoader used by P1NL2SQLAgent.run()."""

    def __init__(self, tables_json: Path, description_dir: Path):
        block = build_financial_schema_block(tables_json, description_dir)
        self._ddl_by_table = _split_schema_block_by_table(block)
        self.table_names = list(self._ddl_by_table.keys())

    def get_ddl_text(self, table_name: str) -> str:
        return self._ddl_by_table.get(table_name, f"-- unknown table: {table_name}")


class _BirdSchemaLinkerStub:
    """Returns all 8 BIRD financial tables. No embedding call, no retrieval."""

    def __init__(self, table_names: list[str]):
        self._matches = [
            SimpleNamespace(name=t, score=1.0, domain="bird_financial") for t in table_names
        ]

    def link(self, question: str) -> list:  # noqa: ARG002  (unused kwarg by design)
        return list(self._matches)


# ---------------------------- executor adapter ----------------------------


@dataclass
class BirdExecutorAdapterStats:
    """Post-run stats for one adapter call. Used by tests, not by P1 itself."""

    latency_ms: int
    error_source: str | None  # 'unsafe' | 'syntax' | 'timeout' | 'runtime' | None


class _BirdSQLExecutorAdapter:
    """Mimics ``SQLExecutor.execute()`` semantics on top of BirdSQLiteExecutor.

    - Success returns ``(rows_as_list_of_dicts, None)``.
    - Failure returns ``(None, error_msg)`` where ``error_msg`` is shaped so
      P1's ``classify_error`` maps it to the closest ``SQLErrorClass``.
    """

    def __init__(self, bird_executor: BirdSQLiteExecutor):
        self._bird = bird_executor
        self.last_stats: BirdExecutorAdapterStats | None = None

    def execute(self, sql: str) -> tuple[list[dict] | None, str | None]:
        try:
            result = self._bird.execute(sql)
        except ExecutorUnsafeSQL as e:
            self.last_stats = BirdExecutorAdapterStats(latency_ms=0, error_source="unsafe")
            # Map to P1's OTHER class by returning a message it won't match
            return None, f"unsafe sql rejected: {e}"
        except ExecutorSyntaxError as e:
            self.last_stats = BirdExecutorAdapterStats(latency_ms=0, error_source="syntax")
            msg = str(e).lower()
            # Route "no such column/table" through classify_error's UNKNOWN_* branches
            if "no such column" in msg:
                return None, f'column "unknown" does not exist ({e})'
            if "no such table" in msg:
                return None, f'relation "unknown" does not exist ({e})'
            return None, f"syntax error near: {e}"
        except ExecutorTimeout as e:
            self.last_stats = BirdExecutorAdapterStats(latency_ms=0, error_source="timeout")
            # Reuse PG's exact timeout phrase so classify_error hits TIMEOUT branch
            return None, f"canceling statement due to statement timeout ({e})"
        except ExecutorRuntimeError as e:
            self.last_stats = BirdExecutorAdapterStats(latency_ms=0, error_source="runtime")
            return None, f"runtime error: {e}"

        self.last_stats = BirdExecutorAdapterStats(latency_ms=result.latency_ms, error_source=None)
        # BIRD returns list[tuple]; P1 downstream only checks rows is not None (agent
        # doesn't act on row content itself). Wrap as dicts to satisfy list[dict] type.
        rows_as_dicts = [
            {f"col_{i}": v for i, v in enumerate(row)} for row in result.rows
        ]
        return rows_as_dicts, None

    @staticmethod
    def classify_error(error_msg: str) -> SQLErrorClass:
        # Delegate to P1's classifier — messages above are shaped to match its branches.
        return SQLExecutor.classify_error(error_msg)


# ---------------------------- agent factory ----------------------------


def build_p1_bird_agent(
    tables_json: Path,
    description_dir: Path,
    sqlite_db: Path,
    sql_timeout_s: float = 30.0,
    dialect: str = "sqlite",
) -> P1NL2SQLAgent:
    """Construct a P1NL2SQLAgent with BIRD stubs, bypassing its own __init__.

    Uses ``object.__new__`` to skip the default constructor (which would load our
    Chinese schema YAML and build a slow embedding index for tables we don't want
    to expose). Dialect defaults to ``"sqlite"`` for BIRD; pass ``"postgres"`` to
    reproduce the pre-dialect-fix baseline (which lost ~12 EX pts to PG-only
    syntax leaking into SQLite executions).
    """
    agent = object.__new__(P1NL2SQLAgent)
    agent.dialect = dialect
    loader = _BirdSchemaLoaderStub(tables_json, description_dir)
    agent.loader = loader
    agent.schema_linker = _BirdSchemaLinkerStub(loader.table_names)
    agent.sql_generator = SQLGenerator(dialect=dialect)
    agent.sql_validator = SQLValidator(dialect=dialect)
    agent.sql_executor = _BirdSQLExecutorAdapter(
        BirdSQLiteExecutor(sqlite_db, timeout_s=sql_timeout_s)
    )
    agent.reflector = Reflector(max_attempts=MAX_ATTEMPTS, dialect=dialect)
    return agent
