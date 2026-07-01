"""EX (Execution Accuracy) scoring for BIRD.

BIRD's EX compares the *row sets* produced by the model's SQL and the gold SQL.
Row order and column names are irrelevant; column *position* matters. We follow
BIRD's official ``evaluation.py`` semantics:

    ex == 1  iff  set(predicted_rows) == set(gold_rows)   [or any tied alternate]

Some questions have multiple valid gold SQLs (``dev_tied_append.json``); the model
is credited if it matches any one of them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def _canonicalize_row(row: tuple) -> tuple:
    """Normalize a single row for set-based comparison.

    - ``float`` values that are whole numbers get folded onto ``int`` so
      ``5.0`` and ``5`` compare equal (BIRD's official evaluator does the same).
    - ``None`` is preserved (SQLite NULLs).
    - Bytes get decoded (SQLite blobs — none expected in financial, safety net).
    """
    out: list = []
    for v in row:
        if isinstance(v, float) and v.is_integer():
            out.append(int(v))
        elif isinstance(v, bytes):
            out.append(v.decode("utf-8", errors="replace"))
        else:
            out.append(v)
    return tuple(out)


def rows_hash(rows: Iterable[tuple]) -> str:
    """Stable short hash of a row set — used for at-a-glance diffing in results JSON."""
    canon = sorted(repr(_canonicalize_row(r)) for r in rows)
    h = hashlib.sha1("\n".join(canon).encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"sha1:{h[:12]}"


def rows_equal(predicted: list[tuple], gold: list[tuple]) -> bool:
    """Row-set equality with float/int folding."""
    p = {_canonicalize_row(r) for r in predicted}
    g = {_canonicalize_row(r) for r in gold}
    return p == g


def score_ex(
    predicted: list[tuple],
    gold: list[tuple],
    tied_alternates: list[list[tuple]] | None = None,
) -> int:
    """Return 1 iff predicted matches gold or any tied alternate; else 0."""
    if rows_equal(predicted, gold):
        return 1
    for alt in tied_alternates or []:
        if rows_equal(predicted, alt):
            return 1
    return 0
