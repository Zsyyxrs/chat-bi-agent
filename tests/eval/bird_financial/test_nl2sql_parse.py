"""Tests for the NL2SQL JSON-parse contract (no LLM call — parse only)."""

from __future__ import annotations

import pytest

from chat_bi_agent.eval.bird_financial.nl2sql import NL2SQLParseError, _parse


def test_parse_fenced_json_extracts_fields() -> None:
    raw = (
        'Here you go:\n```json\n{"thought": "T", "tables_used": ["a","b"], '
        '"sql": "SELECT 1"}\n```\nDone.'
    )
    sql, thought, tables = _parse(raw)
    assert sql == "SELECT 1"
    assert thought == "T"
    assert tables == ["a", "b"]


def test_parse_bare_json_also_works() -> None:
    raw = '{"thought": "", "tables_used": [], "sql": "SELECT 2"}'
    sql, _, _ = _parse(raw)
    assert sql == "SELECT 2"


def test_parse_missing_field_raises() -> None:
    raw = '{"thought": "T", "tables_used": []}'  # no sql
    with pytest.raises(NL2SQLParseError, match="missing key sql"):
        _parse(raw)


def test_parse_non_list_tables_used_raises() -> None:
    raw = '{"thought":"T","tables_used":"a","sql":"SELECT 1"}'
    with pytest.raises(NL2SQLParseError, match="tables_used must be a list"):
        _parse(raw)


def test_parse_empty_sql_raises() -> None:
    raw = '{"thought":"T","tables_used":[],"sql":"   "}'
    with pytest.raises(NL2SQLParseError, match="sql must be a non-empty string"):
        _parse(raw)


def test_parse_bad_json_raises() -> None:
    with pytest.raises(NL2SQLParseError, match="not valid JSON"):
        _parse("this is not json at all")
