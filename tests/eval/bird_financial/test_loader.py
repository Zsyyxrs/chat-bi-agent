"""Tests for BIRD dev.json + tied_append loader."""

from __future__ import annotations

import json
from pathlib import Path

from chat_bi_agent.eval.bird_financial.loader import (
    BirdQuestion,
    load_financial_questions,
    load_tied_append,
)


def _write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_financial_only_keeps_financial_rows(tmp_path: Path) -> None:
    dev = _write_json(
        tmp_path / "dev.json",
        [
            {
                "question_id": 1,
                "db_id": "california_schools",
                "question": "Q1",
                "evidence": "",
                "SQL": "SELECT 1",
                "difficulty": "simple",
            },
            {
                "question_id": 2,
                "db_id": "financial",
                "question": "Q2",
                "evidence": "hint",
                "SQL": "SELECT 2",
                "difficulty": "moderate",
            },
        ],
    )
    qs = load_financial_questions(dev)
    assert len(qs) == 1
    assert qs[0] == BirdQuestion(
        question_id=2,
        db_id="financial",
        question="Q2",
        evidence="hint",
        gold_sql="SELECT 2",
        difficulty="moderate",
    )


def test_load_financial_tolerates_null_evidence(tmp_path: Path) -> None:
    dev = _write_json(
        tmp_path / "dev.json",
        [
            {
                "question_id": 3,
                "db_id": "financial",
                "question": "Q3",
                "evidence": None,
                "SQL": "SELECT 3",
                "difficulty": "simple",
            }
        ],
    )
    qs = load_financial_questions(dev)
    assert qs[0].evidence == ""


def test_load_tied_append_indexes_by_question_id(tmp_path: Path) -> None:
    tied = _write_json(
        tmp_path / "tied.json",
        [
            {"question_id": 7, "db_id": "financial", "SQL": "SELECT 1"},
            {"question_id": 7, "db_id": "financial", "SQL": "SELECT 2"},
            {"question_id": 9, "db_id": "financial", "SQL": "SELECT 9"},
        ],
    )
    got = load_tied_append(tied)
    assert set(got.keys()) == {7, 9}
    assert got[7] == ["SELECT 1", "SELECT 2"]
    assert got[9] == ["SELECT 9"]


def test_load_tied_append_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_tied_append(tmp_path / "does_not_exist.json") == {}
