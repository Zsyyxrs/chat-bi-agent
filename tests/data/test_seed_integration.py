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
    _run(
        [
            "docker",
            "exec",
            "-i",
            "chatbi-pg",
            "psql",
            "-U",
            "chatbi",
            "-d",
            "chatbi",
            "-c",
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
        ]
    )
    for sql_file in ["01_schema.sql", "02_indexes.sql"]:
        with open(REPO_ROOT / "docker" / "postgres" / "init" / sql_file) as f:
            subprocess.run(
                ["docker", "exec", "-i", "chatbi-pg", "psql", "-U", "chatbi", "-d", "chatbi"],
                stdin=f,
                check=True,
                capture_output=True,
            )

    # Reseed with events
    _run(
        [
            "python",
            "-m",
            "chat_bi_agent.data.seed",
            "--port",
            "5433",
            "--rows",
            "20000",
            "--with-events",
            "--truncate",
        ]
    )

    # Verify anxin
    result = subprocess.run(
        ["python", "scripts/verify_events.py", "--event-id", "anxin_90_expire"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, f"verify_events failed: {result.stdout}\n{result.stderr}"


def test_product_expiry_lifecycle_clears_post_expiry_rows():
    """After reseed, no fct_balance_daily rows for anxin's expired wealth products
    should exist on dt > 2026-05-14, and pre-expiry rows must remain."""
    psql = [
        "docker",
        "exec",
        "chatbi-pg",
        "psql",
        "-U",
        "chatbi",
        "-d",
        "chatbi",
        "-t",
        "-c",
    ]
    pre = subprocess.run(
        psql
        + [
            "SELECT COUNT(*) FROM fct_balance_daily "
            "WHERE product_id IN ('PROD_WEA_0030','PROD_WEA_0031') AND dt <= DATE '2026-05-14';"
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    post = subprocess.run(
        psql
        + [
            "SELECT COUNT(*) FROM fct_balance_daily "
            "WHERE product_id IN ('PROD_WEA_0030','PROD_WEA_0031') AND dt > DATE '2026-05-14';"
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    pre_count = int(pre.stdout.strip())
    post_count = int(post.stdout.strip())
    assert pre_count > 0, "expected pre-expiry balance rows for PROD_WEA_0030/0031"
    assert post_count == 0, f"expected 0 post-expiry rows, got {post_count}"


def test_product_expiry_lifecycle_closes_accounts():
    """All accounts holding PROD_WEA_0030/0031 must have close_date <= anxin event date.

    Note: some accounts are seeded with status=CLOSED + earlier close_date by
    dimension_generator (15% closure rate). The lifecycle must not push those
    forward but must close every still-open account on the event date.
    """
    result = subprocess.run(
        [
            "docker",
            "exec",
            "chatbi-pg",
            "psql",
            "-U",
            "chatbi",
            "-d",
            "chatbi",
            "-t",
            "-c",
            "SELECT COUNT(*) FROM dim_account "
            "WHERE product_id IN ('PROD_WEA_0030','PROD_WEA_0031') "
            "AND (close_date IS NULL OR close_date > DATE '2026-05-14');",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    leftover = int(result.stdout.strip())
    assert leftover == 0, (
        f"expected all expired accounts closed by 2026-05-14, {leftover} still not"
    )


def test_anchor_customer_count():
    """After reseed, verify is_event_anchor count meets each event's min_customers."""
    cmd = [
        "docker",
        "exec",
        "chatbi-pg",
        "psql",
        "-U",
        "chatbi",
        "-d",
        "chatbi",
        "-t",
        "-c",
        "SELECT COUNT(*) FROM dim_customer WHERE is_event_anchor = TRUE;",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    count = int(result.stdout.strip())
    # 4 events: anxin 20 + qixi 20 + lpr 30 + spring 50 = 120 minimum
    assert count >= 100, f"expected ≥100 anchor customers, got {count}"
