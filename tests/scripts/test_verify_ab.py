"""verify_ab.py 单测：CRITICAL 检测、EXPECTED_DIFFER 声明、warning 触发。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "verify_ab", REPO_ROOT / "scripts" / "verify_ab.py"
)
_verify_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_verify_ab)
compare = _verify_ab.compare


def _mk_result(
    commit_hash: str = "aaaa11112222",
    model: str = "qwen3.7-max",
    dialect: str = "sqlite",
    few_shot=None,
    dev_json_md5: str = "aaa",
    sqlite_md5: str = "bbb",
    dirty: bool = False,
    ex: float = 0.5,
) -> dict:
    return {
        "benchmark": "bird_financial",
        "variant": "p1_agent",
        "dialect": dialect,
        "few_shot": few_shot or {"enabled": False, "k": 3, "min_similarity": 0.55},
        "model": model,
        "dev_json_md5": dev_json_md5,
        "sqlite_md5": sqlite_md5,
        "run_metadata": {
            "commit_hash": commit_hash,
            "commit_hash_full": commit_hash + "x" * 28,
            "commit_dirty": dirty,
            "config_hash": "cfg1",
            "python_version": "3.13.5",
            "host": "mac",
            "run_ts_utc": "2026-07-07T00:00:00+00:00",
        },
        "summary": {"ex_overall": ex},
    }


def test_clean_ab_only_declared_differ_returns_0(capsys):
    a = _mk_result(dialect="sqlite")
    b = _mk_result(dialect="postgres")  # only dialect differs
    rc = compare(a, b, expected_differ=["dialect"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "可归因" in out


def test_model_mismatch_returns_1(capsys):
    a = _mk_result(model="qwen3.7-max")
    b = _mk_result(model="qwen3.7-max-preview")
    rc = compare(a, b, expected_differ=["few_shot"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out
    assert "归因失效" in out


def test_commit_hash_mismatch_returns_1(capsys):
    a = _mk_result(commit_hash="aaaa11112222")
    b = _mk_result(commit_hash="bbbb33334444")
    rc = compare(a, b, expected_differ=[])
    assert rc == 1
    out = capsys.readouterr().out
    assert "commit_hash" in out
    assert "MISMATCH" in out


def test_dirty_workspace_triggers_warning(capsys):
    a = _mk_result(dirty=True)
    b = _mk_result(dirty=False)
    rc = compare(a, b, expected_differ=[])
    # dirty 不影响 critical，但要 warn
    assert rc == 0
    out = capsys.readouterr().out
    assert "dirty" in out
    assert "警告" in out


def test_expected_differ_but_same_is_flagged_suspicious(capsys):
    """声明该异的字段实际相同 = 可疑（改配置没生效）"""
    a = _mk_result(dialect="sqlite")
    b = _mk_result(dialect="sqlite")  # dialect 声明差异，但实际相同
    rc = compare(a, b, expected_differ=["dialect"])
    assert rc == 0  # 不算 critical fail
    out = capsys.readouterr().out
    assert "A/B 未生效" in out or "相同" in out


def test_missing_run_metadata_still_compares_toplevel(capsys):
    """老 result 缺 run_metadata → warning 但顶层字段还能比。"""
    a = _mk_result()
    del a["run_metadata"]
    b = _mk_result()
    rc = compare(a, b, expected_differ=[])
    out = capsys.readouterr().out
    assert "缺 run_metadata" in out
    # model 相同 sqlite_md5 相同 → 顶层无 critical → rc 0
    assert rc == 0


def test_undeclared_toplevel_diff_shown_but_not_critical(capsys):
    """few_shot 变了但没声明 --expected-differ → 列在'未分类差异'里，不 crit。"""
    a = _mk_result(few_shot={"enabled": True, "min_similarity": 0.55})
    b = _mk_result(few_shot={"enabled": True, "min_similarity": 0.4})
    rc = compare(a, b, expected_differ=[])
    out = capsys.readouterr().out
    assert "未分类差异" in out
    assert "few_shot" in out
    assert rc == 0  # 顶层 CRITICAL 字段相同 → 通过
