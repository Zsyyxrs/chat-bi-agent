"""Shared fixtures for P3 tests."""
from pathlib import Path

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
