"""Smoke tests for the schema prompt builder — uses real BIRD data if present."""

from __future__ import annotations

from pathlib import Path

import pytest

from chat_bi_agent.eval.bird_financial.schema_prompt import build_financial_schema_block

REPO_ROOT = Path(__file__).resolve().parents[3]
BIRD_DIR = REPO_ROOT / "benchmarks" / "bird"
TABLES_JSON = BIRD_DIR / "dev_tables.json"
DESC_DIR = BIRD_DIR / "dev_databases" / "financial" / "database_description"


needs_bird = pytest.mark.skipif(
    not (TABLES_JSON.exists() and DESC_DIR.exists()),
    reason="BIRD benchmark files not present at benchmarks/bird/",
)


@needs_bird
def test_block_contains_all_8_tables() -> None:
    block = build_financial_schema_block(TABLES_JSON, DESC_DIR)
    for table in ["account", "card", "client", "disp", "district", "loan", "order", "trans"]:
        assert f"Table: {table}" in block


@needs_bird
def test_block_marks_primary_keys() -> None:
    block = build_financial_schema_block(TABLES_JSON, DESC_DIR)
    # account.account_id is the account PK
    assert "account_id: integer (PK)" in block


@needs_bird
def test_block_marks_foreign_keys() -> None:
    block = build_financial_schema_block(TABLES_JSON, DESC_DIR)
    # account.district_id → district.district_id
    assert "district_id: integer (FK -> district.district_id)" in block


@needs_bird
def test_block_includes_value_enum_descriptions() -> None:
    block = build_financial_schema_block(TABLES_JSON, DESC_DIR)
    # loan.status has an enum table in the CSV — the LLM needs this
    assert "'A' stands for" in block or "A stands for" in block
