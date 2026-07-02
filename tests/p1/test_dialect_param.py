"""Tests for the dialect parameterization added across P1 components.

Covers:
- SQLGenerator system prompt varies by dialect (PG vs SQLite)
- SQLValidator sqlglot dialect switches
- Reflector re-classifies SYNTAX_ERROR → DIALECT_MISMATCH when prev_sql carries
  cross-dialect syntax
- Reflector hint contains actionable per-pattern fixes
"""

from __future__ import annotations

import pytest

from chat_bi_agent.agents.p1.reflector import (
    ReflectAction,
    Reflector,
    _detect_dialect_mismatch,
)
from chat_bi_agent.agents.p1.sql_generator import SQLGenerator, _build_system_prompt
from chat_bi_agent.agents.p1.sql_validator import SQLValidator
from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass

# --------------------------- SQLGenerator ---------------------------


def test_sql_generator_default_dialect_is_postgres() -> None:
    gen = SQLGenerator()
    assert gen.dialect == "postgres"
    assert "PostgreSQL" in gen.system_prompt
    assert "DATE 'YYYY-MM-DD' 字面量" in gen.system_prompt


def test_sql_generator_sqlite_prompt_swaps_dialect_name_and_date_rule() -> None:
    gen = SQLGenerator(dialect="sqlite")
    assert gen.dialect == "sqlite"
    assert "SQLite SELECT" in gen.system_prompt
    assert "PostgreSQL SELECT" not in gen.system_prompt
    # SQLite date rule replaces PG one
    assert "STRFTIME" in gen.system_prompt
    assert "无 DATE 前缀" in gen.system_prompt
    assert "DATE 'YYYY-MM-DD' 字面量" not in gen.system_prompt


def test_sql_generator_unsupported_dialect_raises() -> None:
    with pytest.raises(ValueError, match="unsupported dialect"):
        _build_system_prompt("mysql")


# --------------------------- SQLValidator ---------------------------


def test_sql_validator_default_dialect_is_postgres_and_rejects_sqlite_backticks() -> None:
    v = SQLValidator()
    # sqlglot's postgres dialect trips on SQLite-style backtick-quoted identifiers
    r = v.validate("SELECT `a` FROM t")
    assert r.ok is False


def test_sql_validator_sqlite_accepts_sqlite_syntax() -> None:
    v = SQLValidator(dialect="sqlite")
    r = v.validate("SELECT `a` FROM t")
    assert r.ok is True


def test_sql_validator_still_blocks_dml_regardless_of_dialect() -> None:
    for d in ("postgres", "sqlite"):
        v = SQLValidator(dialect=d)
        assert v.validate("DELETE FROM t WHERE 1=1").ok is False


# --------------------------- dialect detection ---------------------------


@pytest.mark.parametrize(
    ("sql", "expected_fragment"),
    [
        ("SELECT EXTRACT(YEAR FROM col) FROM t", "STRFTIME"),
        ("SELECT * FROM loan WHERE date = DATE '1997-01-01'", "无 DATE 前缀"),
        ("SELECT * FROM t WHERE name ILIKE '%foo%'", "LOWER"),
        ("SELECT DATE_PART('year', d) FROM t", "STRFTIME"),
        ("SELECT TO_CHAR(d, 'YYYY') FROM t", "STRFTIME"),
    ],
)
def test_detect_dialect_mismatch_flags_pg_only_syntax_when_target_is_sqlite(
    sql: str, expected_fragment: str
) -> None:
    hints = _detect_dialect_mismatch(sql, target_dialect="sqlite")
    assert hints, f"expected hint for {sql!r}"
    assert any(expected_fragment in h for h in hints)


def test_detect_dialect_mismatch_flags_sqlite_only_syntax_when_target_is_postgres() -> None:
    hints = _detect_dialect_mismatch(
        "SELECT STRFTIME('%Y', d), IIF(x > 0, 'a', 'b') FROM t",
        target_dialect="postgres",
    )
    assert len(hints) == 2  # STRFTIME + IIF


def test_detect_dialect_mismatch_returns_empty_when_target_matches_syntax() -> None:
    assert _detect_dialect_mismatch("SELECT * FROM t", "sqlite") == []
    assert _detect_dialect_mismatch(None, "sqlite") == []


# --------------------------- Reflector reclassification ---------------------------


def test_reflector_upgrades_syntax_error_to_dialect_mismatch_for_extract_on_sqlite() -> None:
    r = Reflector(max_attempts=3, dialect="sqlite")
    decision = r.reflect(
        err_class=SQLErrorClass.SYNTAX_ERROR,
        err_msg='near "FROM": syntax error',
        prev_sql="SELECT EXTRACT(YEAR FROM d) FROM t",
        top_k_tables=["t"],
        attempt=1,
    )
    assert decision.action == ReflectAction.RETRY
    assert decision.effective_err_class == SQLErrorClass.DIALECT_MISMATCH
    assert "STRFTIME" in decision.repair_hint
    assert "sqlite" in decision.repair_hint  # dialect name embedded


def test_reflector_keeps_syntax_error_when_prev_sql_has_no_dialect_signal() -> None:
    r = Reflector(max_attempts=3, dialect="sqlite")
    decision = r.reflect(
        err_class=SQLErrorClass.SYNTAX_ERROR,
        err_msg='near "GROUP": syntax error',
        prev_sql="SELECT * FROM t GROUP",  # genuine syntax error, no dialect tell
        top_k_tables=["t"],
        attempt=1,
    )
    assert decision.action == ReflectAction.RETRY
    assert decision.effective_err_class is None  # no reclassification
    assert "语法错" in decision.repair_hint
    assert "sqlite" in decision.repair_hint  # dialect string interpolated


def test_reflector_timeout_still_gives_up_regardless_of_dialect() -> None:
    r = Reflector(max_attempts=3, dialect="sqlite")
    decision = r.reflect(
        err_class=SQLErrorClass.TIMEOUT,
        err_msg="canceling statement due to statement timeout",
        prev_sql="SELECT EXTRACT(YEAR FROM d) FROM t",  # would trigger dialect on SYNTAX
        top_k_tables=["t"],
        attempt=1,
    )
    assert decision.action == ReflectAction.GIVE_UP


def test_reflector_dialect_hint_lists_multiple_fixes_when_prev_sql_has_multiple_issues() -> None:
    r = Reflector(max_attempts=3, dialect="sqlite")
    prev = "SELECT EXTRACT(YEAR FROM d) FROM t WHERE name ILIKE 'x' AND d = DATE '2024-01-01'"
    decision = r.reflect(
        err_class=SQLErrorClass.SYNTAX_ERROR,
        err_msg="near FROM: syntax error",
        prev_sql=prev,
        top_k_tables=["t"],
        attempt=1,
    )
    assert decision.effective_err_class == SQLErrorClass.DIALECT_MISMATCH
    # All three patterns caught
    assert "STRFTIME" in decision.repair_hint
    assert "无 DATE 前缀" in decision.repair_hint
    assert "LOWER" in decision.repair_hint


def test_reflector_max_attempts_still_gives_up_even_on_dialect_mismatch() -> None:
    r = Reflector(max_attempts=3, dialect="sqlite")
    decision = r.reflect(
        err_class=SQLErrorClass.SYNTAX_ERROR,
        err_msg='near "FROM": syntax error',
        prev_sql="SELECT EXTRACT(YEAR FROM d) FROM t",
        top_k_tables=["t"],
        attempt=3,  # already at cap
    )
    assert decision.action == ReflectAction.GIVE_UP
