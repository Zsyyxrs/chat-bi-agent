"""Tests for the P1-on-BIRD adapter (schema stubs + executor error mapping)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass
from chat_bi_agent.eval.bird_financial.p1_adapter import (
    _BirdSchemaLinkerStub,
    _BirdSchemaLoaderStub,
    _BirdSQLExecutorAdapter,
    _split_schema_block_by_table,
)
from chat_bi_agent.eval.bird_financial.sqlite_executor import BirdSQLiteExecutor

REPO_ROOT = Path(__file__).resolve().parents[3]
BIRD_DIR = REPO_ROOT / "benchmarks" / "bird"
TABLES_JSON = BIRD_DIR / "dev_tables.json"
DESC_DIR = BIRD_DIR / "dev_databases" / "financial" / "database_description"

needs_bird = pytest.mark.skipif(
    not (TABLES_JSON.exists() and DESC_DIR.exists()),
    reason="BIRD benchmark files not present",
)


# ---------------------------- split helper ----------------------------


def test_split_schema_block_produces_one_chunk_per_table() -> None:
    block = "Table: a\n  - x: int\nTable: b\n  - y: text\n"
    got = _split_schema_block_by_table(block)
    assert set(got.keys()) == {"a", "b"}
    assert got["a"].startswith("Table: a")
    assert got["b"].startswith("Table: b")
    assert "- x: int" in got["a"]
    assert "- y: text" in got["b"]


def test_split_schema_block_empty_input_returns_empty() -> None:
    assert _split_schema_block_by_table("") == {}


# ---------------------------- schema stubs ----------------------------


@needs_bird
def test_schema_loader_stub_covers_all_8_bird_tables() -> None:
    loader = _BirdSchemaLoaderStub(TABLES_JSON, DESC_DIR)
    expected = ["account", "card", "client", "disp", "district", "loan", "order", "trans"]
    assert loader.table_names == expected
    for name in loader.table_names:
        ddl = loader.get_ddl_text(name)
        assert ddl.startswith(f"Table: {name}"), f"{name} DDL malformed"


@needs_bird
def test_schema_linker_stub_returns_all_tables_with_score_1() -> None:
    loader = _BirdSchemaLoaderStub(TABLES_JSON, DESC_DIR)
    linker = _BirdSchemaLinkerStub(loader.table_names)
    matches = linker.link("any question")
    assert [m.name for m in matches] == loader.table_names
    assert all(m.score == 1.0 for m in matches)


# ---------------------------- executor adapter ----------------------------


@pytest.fixture
def tiny_db(tmp_path: Path) -> Path:
    db = tmp_path / "tiny.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT);"
        "INSERT INTO t VALUES (1, 'a'), (2, 'b');"
    )
    conn.commit()
    conn.close()
    return db


def test_executor_adapter_success_returns_rows_as_dicts_and_no_error(tiny_db: Path) -> None:
    a = _BirdSQLExecutorAdapter(BirdSQLiteExecutor(tiny_db, timeout_s=5.0))
    rows, err = a.execute("SELECT id, val FROM t ORDER BY id")
    assert err is None
    assert rows == [{"col_0": 1, "col_1": "a"}, {"col_0": 2, "col_1": "b"}]
    assert a.last_stats.error_source is None


def test_executor_adapter_unknown_column_maps_to_unknown_column_class(tiny_db: Path) -> None:
    a = _BirdSQLExecutorAdapter(BirdSQLiteExecutor(tiny_db, timeout_s=5.0))
    rows, err = a.execute("SELECT nope FROM t")
    assert rows is None and err is not None
    assert a.classify_error(err) == SQLErrorClass.UNKNOWN_COLUMN
    assert a.last_stats.error_source == "syntax"


def test_executor_adapter_unknown_table_maps_to_unknown_table_class(tiny_db: Path) -> None:
    a = _BirdSQLExecutorAdapter(BirdSQLiteExecutor(tiny_db, timeout_s=5.0))
    rows, err = a.execute("SELECT * FROM does_not_exist")
    assert rows is None and err is not None
    assert a.classify_error(err) == SQLErrorClass.UNKNOWN_TABLE


def test_executor_adapter_generic_syntax_maps_to_syntax_error_class(tiny_db: Path) -> None:
    a = _BirdSQLExecutorAdapter(BirdSQLiteExecutor(tiny_db, timeout_s=5.0))
    rows, err = a.execute("SELECT WHERE FROM t")
    assert rows is None and err is not None
    assert a.classify_error(err) == SQLErrorClass.SYNTAX_ERROR


def test_executor_adapter_timeout_maps_to_timeout_class(tiny_db: Path) -> None:
    a = _BirdSQLExecutorAdapter(BirdSQLiteExecutor(tiny_db, timeout_s=1.0))
    sql = (
        "WITH RECURSIVE cnt(x) AS ("
        "  SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x < 10000000000"
        ") SELECT COUNT(*) FROM cnt"
    )
    rows, err = a.execute(sql)
    assert rows is None and err is not None
    assert a.classify_error(err) == SQLErrorClass.TIMEOUT
    assert a.last_stats.error_source == "timeout"


def test_executor_adapter_unsafe_sql_returns_error_msg(tiny_db: Path) -> None:
    a = _BirdSQLExecutorAdapter(BirdSQLiteExecutor(tiny_db, timeout_s=5.0))
    rows, err = a.execute("DELETE FROM t")
    assert rows is None and err is not None and "unsafe" in err.lower()
    assert a.last_stats.error_source == "unsafe"
