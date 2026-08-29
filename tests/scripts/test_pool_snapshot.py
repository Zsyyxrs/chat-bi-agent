"""池子快照：把 few-shot 池的内容与治理决策固化进 git。

存在理由：`data/example_pool*.jsonl` 因为 embedding blob 被 gitignore，于是
2026-08-28 那轮分流的成果——16 条生产池 / 14 条 governed 归档 / 1 条隔离，
以及「idx14 以 catalog 为准」这类人工裁决——只存在于一台机器上。换机器或重建
容器就全没了，而重建它需要重跑分流（打 LLM）+ 人再裁决一遍。

embedding 占了文件 98% 的体积，去掉之后三个池子总共 13.4 KB，进 git 毫无压力。
embedding 是模型的确定性产物，随时可以重算。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pool_snapshot.py"
_spec = importlib.util.spec_from_file_location("pool_snapshot", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _row(eid, q="问题", sql="SELECT 1", emb=True):
    r = {
        "example_id": eid,
        "question": q,
        "sql": sql,
        "dialect": "postgres",
        "source": "test",
        "tags": ["prod"],
        "ts": "2026-08-29T00:00:00Z",
    }
    if emb:
        r["embedding"] = [0.1] * 8
    return r


def test_snapshot_strips_embeddings_and_stamps_bucket():
    """embedding 不进快照——它是 98% 的体积，且可以确定性重算。"""
    rows = _mod.build_snapshot({"prod": [_row("a")], "quarantine": [_row("b")]})
    assert all("embedding" not in r for r in rows)
    assert {r["example_id"]: r["bucket"] for r in rows} == {"a": "prod", "b": "quarantine"}


def test_snapshot_is_sorted_for_stable_diffs():
    """按 (bucket, example_id) 排序：否则每次重存都是一坨无意义的 git diff。"""
    rows = _mod.build_snapshot({"prod": [_row("z"), _row("a")], "metric_governed": [_row("m")]})
    assert [(r["bucket"], r["example_id"]) for r in rows] == [
        ("metric_governed", "m"),
        ("prod", "a"),
        ("prod", "z"),
    ]


def test_snapshot_keeps_governance_notes():
    """隔离原因这类人工裁决必须留住——那是重建时最贵的部分。"""
    q = _row("b")
    q["quarantine_reason"] = "gold 返回 0 行"
    rows = _mod.build_snapshot({"quarantine": [q]})
    assert rows[0]["quarantine_reason"] == "gold 返回 0 行"


def test_diff_detects_added_removed_and_moved():
    """三种漂移都要报：新增、丢失、以及**换了桶**。

    换桶是最隐蔽的一种——条数对得上，但某条题从「生产池」挪到了「已交给
    语义层」，治理含义完全不同。
    """
    snap = _mod.build_snapshot({"prod": [_row("a"), _row("b")], "quarantine": [_row("c")]})
    live = {
        "prod": [_row("a")],
        "metric_governed": [_row("b")],
        "quarantine": [_row("c"), _row("d")],
    }
    d = _mod.diff_snapshot(snap, live)
    assert d["added"] == ["d"]
    assert d["removed"] == []
    assert d["moved"] == [("b", "prod", "metric_governed")]


def test_diff_clean_when_identical():
    pools = {"prod": [_row("a")], "metric_governed": [_row("b")]}
    d = _mod.diff_snapshot(_mod.build_snapshot(pools), pools)
    assert d == {"added": [], "removed": [], "moved": []}


def test_restore_regroups_rows_by_bucket():
    snap = _mod.build_snapshot({"prod": [_row("a")], "quarantine": [_row("b")]})
    out = _mod.restore_buckets(snap)
    assert sorted(out) == ["prod", "quarantine"]
    assert [r["example_id"] for r in out["prod"]] == ["a"]
    # bucket 是快照的元数据，不该落进池子文件
    assert all("bucket" not in r for rows in out.values() for r in rows)
