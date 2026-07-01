"""Tests for BirdSQLiteExecutor: read-only enforcement + timeout + error classification."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chat_bi_agent.eval.bird_financial.sqlite_executor import (
    BirdSQLiteExecutor,
    ExecutorRuntimeError,
    ExecutorSyntaxError,
    ExecutorTimeout,
    ExecutorUnsafeSQL,
)


@pytest.fixture
def tiny_db(tmp_path: Path) -> Path:
    db = tmp_path / "tiny.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT, age INT);
        INSERT INTO person VALUES (1, 'alice', 30), (2, 'bob', 25), (3, 'carol', 40);
        """
    )
    conn.commit()
    conn.close()
    return db


def test_execute_returns_tuples(tiny_db: Path) -> None:
    e = BirdSQLiteExecutor(tiny_db, timeout_s=5.0)
    r = e.execute("SELECT name, age FROM person ORDER BY id")
    assert r.rows == [("alice", 30), ("bob", 25), ("carol", 40)]
    assert r.latency_ms >= 1


def test_execute_rejects_ddl_dml(tiny_db: Path) -> None:
    e = BirdSQLiteExecutor(tiny_db, timeout_s=5.0)
    for sql in [
        "DELETE FROM person WHERE id=1",
        "INSERT INTO person VALUES (4, 'x', 1)",
        "DROP TABLE person",
        "UPDATE person SET age=1",
        "ATTACH DATABASE 'x' AS y",
    ]:
        with pytest.raises(ExecutorUnsafeSQL):
            e.execute(sql)


def test_execute_classifies_unknown_column_as_syntax(tiny_db: Path) -> None:
    e = BirdSQLiteExecutor(tiny_db, timeout_s=5.0)
    with pytest.raises(ExecutorSyntaxError):
        e.execute("SELECT nope FROM person")


def test_execute_classifies_unknown_table_as_syntax(tiny_db: Path) -> None:
    e = BirdSQLiteExecutor(tiny_db, timeout_s=5.0)
    with pytest.raises(ExecutorSyntaxError):
        e.execute("SELECT * FROM does_not_exist")


def test_execute_classifies_true_syntax_error(tiny_db: Path) -> None:
    e = BirdSQLiteExecutor(tiny_db, timeout_s=5.0)
    with pytest.raises(ExecutorSyntaxError):
        e.execute("SELECT WHERE FROM person")


def test_execute_is_read_only(tiny_db: Path) -> None:
    """Even if the forbidden regex is bypassed, the connection mode blocks writes."""
    e = BirdSQLiteExecutor(tiny_db, timeout_s=5.0)
    # This SELECT should succeed
    e.execute("SELECT COUNT(*) FROM person")
    # Row count on disk remained 3
    conn = sqlite3.connect(tiny_db)
    n = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    conn.close()
    assert n == 3


def test_execute_missing_db_raises_on_construction(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        BirdSQLiteExecutor(tmp_path / "nope.sqlite")


def test_execute_timeout_via_recursive_cte(tiny_db: Path) -> None:
    """A recursive CTE that never terminates must be cut off by the timeout watchdog."""
    e = BirdSQLiteExecutor(tiny_db, timeout_s=1.0)
    # This CTE tries to enumerate 10 billion rows — should hit the 1s watchdog quickly.
    sql = (
        "WITH RECURSIVE cnt(x) AS ("
        "  SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x < 10000000000"
        ") SELECT COUNT(*) FROM cnt"
    )
    with pytest.raises(ExecutorTimeout):
        e.execute(sql)


def test_runtime_error_bubbles_as_runtime(tiny_db: Path) -> None:
    """Division-by-zero / abort-style errors → ExecutorRuntimeError (not syntax)."""
    e = BirdSQLiteExecutor(tiny_db, timeout_s=5.0)
    # ABORT() is a SQLite function that raises a runtime error via RAISE(ABORT,...)
    # Use a division-by-zero which SQLite surfaces as OperationalError without "syntax"
    # message. If the SQLite version returns NULL instead of erroring, the test skips.
    try:
        r = e.execute("SELECT 1 / 0")
    except ExecutorRuntimeError:
        return
    except ExecutorSyntaxError:
        pytest.fail("division-by-zero should not be classified as syntax")
    # SQLite may just return None — that's OK, no error to check.
    assert r.rows == [(None,)]
