"""Shared fixtures for P3 tests."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest
import yaml


@pytest.fixture
def fake_events_dir(tmp_path: Path) -> Path:
    """Write a synthetic events YAML matching the production schema."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    payload = {
        "events": [
            {
                "id": "anxin_90_expire",
                "name": "安鑫90天理财到期",
                "type": "PRODUCT_EXPIRY",
                "date": "2026-05-14",
                "description": "短期理财产品到期，触发赎回",
            },
            {
                "id": "spring_festival_withdrawal",
                "name": "春节现金支取高峰",
                "type": "SEASONAL",
                "date": "2026-02-15",
                "description": "春节假期现金需求",
            },
            {
                "id": "lpr_cut_q2",
                "name": "二季度 LPR 下调",
                "type": "POLICY",
                "date": "2026-06-20",
                "description": "LPR 下调 10bp",
            },
        ]
    }
    (events_dir / "product_expiry.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
    )
    return events_dir


@dataclass
class FakeP1Result:
    """Stand-in for P1AgentResult — keeps field names identical."""

    question_id: str
    sql: Optional[str] = None
    rows: Optional[list[dict]] = None
    execution_error: Optional[str] = None
    error_class: Optional[str] = None
    schema_link_top_k: list[str] = field(default_factory=list)
    thought: str = ""
    attempts: int = 1
    total_latency_ms: int = 100
    reflect_history: list[dict] = field(default_factory=list)


class FakeP1Agent:
    """Programmable fake of P1NL2SQLAgent. Returns canned results by question_id prefix."""

    def __init__(self, responses: dict[str, FakeP1Result] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str]] = []

    def run(self, question_id: str, question: str) -> FakeP1Result:
        self.calls.append((question_id, question))
        if question_id in self.responses:
            return self.responses[question_id]
        return FakeP1Result(
            question_id=question_id,
            sql="SELECT 1",
            rows=[{"v": 1.0}],
        )


@pytest.fixture
def fake_p1_agent() -> FakeP1Agent:
    return FakeP1Agent()
