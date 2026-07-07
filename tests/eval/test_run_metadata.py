"""run_metadata 单测：git commit hash / dirty flag / config md5 / 附加字段。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from chat_bi_agent.eval.run_metadata import (
    build_run_metadata,
    md5_file,
)


def test_md5_file_stable(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("hello world\n", encoding="utf-8")
    h1 = md5_file(p)
    h2 = md5_file(p)
    assert h1 == h2
    assert len(h1) == 32


def test_md5_file_missing_returns_none(tmp_path: Path):
    assert md5_file(tmp_path / "does_not_exist") is None


def test_build_run_metadata_shape():
    meta = build_run_metadata()
    for k in [
        "commit_hash",
        "commit_hash_full",
        "commit_dirty",
        "config_hash",
        "python_version",
        "host",
        "run_ts_utc",
    ]:
        assert k in meta
    # ts is ISO
    assert "T" in meta["run_ts_utc"]
    # commit_hash short = 7-12 chars
    assert 7 <= len(meta["commit_hash"]) <= 40


def test_build_run_metadata_with_extra_paths(tmp_path: Path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text('{"a":1}\n', encoding="utf-8")
    meta = build_run_metadata(extra_paths={"pool_hash": pool})
    assert "pool_hash" in meta
    assert len(meta["pool_hash"]) == 32


def test_build_run_metadata_extra_missing_path_records_none(tmp_path: Path):
    meta = build_run_metadata(extra_paths={"pool_hash": tmp_path / "nope"})
    assert meta["pool_hash"] is None


def test_run_metadata_is_json_serializable():
    meta = build_run_metadata()
    # must round-trip through json without TypeError
    s = json.dumps(meta)
    assert json.loads(s) == meta


def test_commit_hash_matches_git_rev_parse():
    """sanity: build_run_metadata 的 commit_hash 与 `git rev-parse HEAD` 前 12 位一致。"""
    try:
        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git not available in this environment")
    meta = build_run_metadata()
    assert meta["commit_hash_full"] == actual
    assert meta["commit_hash"] == actual[:12]
