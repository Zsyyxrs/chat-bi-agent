"""Read-only SQLite executor with per-query timeout.

Used both for the model-generated SQL and the gold SQL. Rows are returned as tuples
(order-preserving list of tuples), because BIRD's EX metric compares by column
position, not column name.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class ExecutorError(Exception):
    """Base class for BirdSQLiteExecutor errors."""


class ExecutorTimeout(ExecutorError):
    pass


class ExecutorSyntaxError(ExecutorError):
    pass


class ExecutorRuntimeError(ExecutorError):
    pass


class ExecutorUnsafeSQL(ExecutorError):
    """Rejected before touching the DB — SQL contains banned keywords."""


# Anything that could mutate is forbidden even though the connection is opened read-only
# — early-exit gives a clearer error message than SQLite's "attempt to write" surface.
_FORBIDDEN = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|UPDATE|INSERT|ALTER|GRANT|REVOKE|CREATE|COPY|VACUUM|MERGE|ATTACH|DETACH|PRAGMA)\b",
    re.IGNORECASE,
)


@dataclass
class ExecResult:
    rows: list[tuple]
    latency_ms: int


class BirdSQLiteExecutor:
    def __init__(self, db_path: Path, timeout_s: float = 30.0):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(f"SQLite DB not found: {self.db_path}")
        self.timeout_s = float(timeout_s)

    def _connect(self) -> sqlite3.Connection:
        # `mode=ro` = read-only; `uri=True` required for the file: URI syntax.
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout_s)
        # progress_handler interrupts long-running statements when the elapsed
        # sqlite VM instruction count crosses the threshold. sqlite3.connect's
        # `timeout` argument only guards busy-locks, not query duration, so we
        # add an interrupt watchdog via set_progress_handler for real cutoff.
        return conn

    def execute(self, sql: str) -> ExecResult:
        """Run SQL and return rows as tuples.

        Raises ExecutorUnsafeSQL / ExecutorTimeout / ExecutorSyntaxError / ExecutorRuntimeError.
        """
        if _FORBIDDEN.search(sql):
            raise ExecutorUnsafeSQL(f"SQL contains banned keyword: {sql[:200]}")

        import time

        conn = self._connect()
        # Watchdog: fire conn.interrupt() from another thread once timeout elapses.
        import threading

        cancelled = threading.Event()

        def _watchdog() -> None:
            if not cancelled.wait(self.timeout_s):
                # not cancelled by main thread → time is up → interrupt
                try:
                    conn.interrupt()
                except Exception:
                    pass

        watcher = threading.Thread(target=_watchdog, daemon=True)
        watcher.start()

        start = time.perf_counter()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "interrupted" in msg.lower():
                raise ExecutorTimeout(f"query exceeded {self.timeout_s}s") from e
            # SQLite groups syntax + no-such-table/column under OperationalError;
            # distinguish by message content.
            lowered = msg.lower()
            if "syntax error" in lowered or "unrecognized token" in lowered:
                raise ExecutorSyntaxError(msg) from e
            if "no such" in lowered:  # no such table / column / function
                raise ExecutorSyntaxError(msg) from e
            raise ExecutorRuntimeError(msg) from e
        except sqlite3.DatabaseError as e:
            raise ExecutorRuntimeError(str(e)) from e
        finally:
            cancelled.set()
            conn.close()

        latency_ms = max(1, int((time.perf_counter() - start) * 1000))
        # Normalize rows to tuples of native Python values (SQLite already returns tuples).
        rows_tuples = [tuple(r) for r in rows]
        return ExecResult(rows=rows_tuples, latency_ms=latency_ms)
