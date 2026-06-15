"""Integration test: reseed DB end-to-end and assert verify_events passes for anxin.

Gated by RUN_INTEGRATION=1. Local invocation:
    RUN_INTEGRATION=1 pytest tests/data/test_seed_integration.py -v -s
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 to enable DB integration tests",
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=REPO_ROOT)


def test_reseed_and_verify_anxin():
    # Reset schema
    _run([
        "docker", "exec", "-i", "chatbi-pg",
        "psql", "-U", "chatbi", "-d", "chatbi",
        "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
    ])
    for sql_file in ["01_schema.sql", "02_indexes.sql"]:
        with open(REPO_ROOT / "docker" / "postgres" / "init" / sql_file) as f:
            subprocess.run(
                ["docker", "exec", "-i", "chatbi-pg", "psql", "-U", "chatbi", "-d", "chatbi"],
                stdin=f, check=True, capture_output=True,
            )

    # Reseed with events
    _run([
        "python", "-m", "chat_bi_agent.data.seed",
        "--port", "5433",
        "--rows", "20000",
        "--with-events", "--truncate",
    ])

    # Verify anxin
    result = subprocess.run(
        ["python", "scripts/verify_events.py", "--event-id", "anxin_90_expire"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, f"verify_events failed: {result.stdout}\n{result.stderr}"


def test_anchor_customer_count():
    """After reseed, verify is_event_anchor count meets each event's min_customers."""
    cmd = [
        "docker", "exec", "chatbi-pg", "psql", "-U", "chatbi", "-d", "chatbi", "-t", "-c",
        "SELECT COUNT(*) FROM dim_customer WHERE is_event_anchor = TRUE;",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    count = int(result.stdout.strip())
    # 4 events: anxin 20 + qixi 20 + lpr 30 + spring 50 = 120 minimum
    assert count >= 100, f"expected ≥100 anchor customers, got {count}"
