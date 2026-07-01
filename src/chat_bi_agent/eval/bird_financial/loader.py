"""Load BIRD dev questions and the tied-answer patch."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Difficulty = Literal["simple", "moderate", "challenging"]


@dataclass(frozen=True)
class BirdQuestion:
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: Difficulty


def load_financial_questions(dev_json_path: Path) -> list[BirdQuestion]:
    """Load all `db_id == "financial"` questions from BIRD's dev.json, in file order."""
    data = json.loads(Path(dev_json_path).read_text(encoding="utf-8"))
    out: list[BirdQuestion] = []
    for row in data:
        if row.get("db_id") != "financial":
            continue
        out.append(
            BirdQuestion(
                question_id=int(row["question_id"]),
                db_id=row["db_id"],
                question=str(row["question"]),
                evidence=str(row.get("evidence") or ""),
                gold_sql=str(row["SQL"]),
                difficulty=row.get("difficulty", "moderate"),
            )
        )
    return out


def load_tied_append(path: Path) -> dict[int, list[str]]:
    """Load BIRD's tied-answer patch: {question_id: [alternate_gold_sql, ...]}.

    The upstream file is a list of records; each record shares the same schema as dev.json,
    so we index by ``question_id`` and collect all alternate SQLs (including the primary
    tie's own SQL, which is fine — the scorer just OR-matches).
    """
    if not Path(path).exists():
        return {}
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[int, list[str]] = {}
    for row in rows:
        qid = int(row["question_id"])
        sql = str(row["SQL"])
        out.setdefault(qid, []).append(sql)
    return out
