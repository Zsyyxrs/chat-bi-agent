#!/usr/bin/env python3
"""把 few-shot 池的内容与治理决策固化进 git，并能从快照重建池子。

**为什么需要它**：`data/example_pool*.jsonl` 因为 embedding blob 被 gitignore
（~4MB/1k 行）。于是 2026-08-28 那轮分流的成果——16 条生产池 / 14 条已交给
语义层的归档 / 1 条隔离，以及「idx14 的口径以 catalog 为准」这类人工裁决——
只存在于一台机器上。换机器或重建容器就全没了，而重建它要重跑分流（打 LLM）
再让人重新裁决一遍。

embedding 占了文件 98% 的体积，去掉之后三个池子总共 13KB，进 git 毫无压力；
embedding 是模型的确定性产物，`--restore` 时重算即可。

**这不是备份，是台账。** 快照里 `bucket` 字段记的是治理决定（这条题归生产池、
已交给语义层、还是被隔离），那才是重建时最贵的东西。

用法：
    python scripts/pool_snapshot.py --save      # 三个池子 → 快照（改完池子就跑，然后提交）
    python scripts/pool_snapshot.py --check     # 比对快照与现状，有漂移退 1
    python scripts/pool_snapshot.py --restore   # 快照 → 重建三个池子（重新 embed）

`--check` 不打 LLM，可以随时跑。`--restore` 要 embedding 额度。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "data" / "pool_snapshot.jsonl"  # 注意：不叫 example_pool*，否则被 gitignore
BUCKETS = {
    "prod": REPO / "data" / "example_pool_prod.jsonl",
    "metric_governed": REPO / "data" / "example_pool_metric_governed.jsonl",
    "quarantine": REPO / "data" / "example_pool_quarantine.jsonl",
}


def build_snapshot(pools: dict[str, list[dict]]) -> list[dict]:
    """三个桶的行 → 一份无 embedding、按 (bucket, example_id) 排序的快照。

    排序是为了 git diff 稳定：不排序的话每次 `--save` 都会产生一坨与内容无关的
    行移动，评审时没人看得出真正改了什么。
    """
    rows: list[dict] = []
    for bucket, items in pools.items():
        for r in items:
            lean = {k: v for k, v in r.items() if k != "embedding"}
            rows.append({"bucket": bucket, **lean})
    rows.sort(key=lambda r: (r["bucket"], r["example_id"]))
    return rows


def diff_snapshot(snapshot: list[dict], pools: dict[str, list[dict]]) -> dict:
    """比对快照与现状。三种漂移都要报，**换桶是最隐蔽的那种**——
    条数对得上，但某条题从「生产池」挪到了「已交给语义层」，治理含义完全不同。
    """
    snap = {r["example_id"]: r["bucket"] for r in snapshot}
    live = {r["example_id"]: b for b, items in pools.items() for r in items}
    added = sorted(set(live) - set(snap))
    removed = sorted(set(snap) - set(live))
    moved = sorted(
        (eid, snap[eid], live[eid]) for eid in set(snap) & set(live) if snap[eid] != live[eid]
    )
    return {"added": added, "removed": removed, "moved": moved}


def restore_buckets(snapshot: list[dict]) -> dict[str, list[dict]]:
    """快照 → 按桶分组的行（去掉 bucket 字段，它是快照的元数据不是池子内容）。"""
    out: dict[str, list[dict]] = {}
    for r in snapshot:
        row = {k: v for k, v in r.items() if k != "bucket"}
        out.setdefault(r["bucket"], []).append(row)
    return out


# ---- I/O ---------------------------------------------------------------------


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _live_pools() -> dict[str, list[dict]]:
    return {b: _read(p) for b, p in BUCKETS.items()}


def cmd_save() -> int:
    pools = _live_pools()
    rows = build_snapshot(pools)
    _write(SNAPSHOT, rows)
    counts = {b: len(v) for b, v in pools.items()}
    print(f"[snapshot] 已写入 {SNAPSHOT}：{len(rows)} 条 {counts}")
    print("[snapshot] 记得 git add 它——这份台账就是换机器后唯一能恢复治理决策的东西")
    return 0


def cmd_check() -> int:
    snapshot = _read(SNAPSHOT)
    if not snapshot:
        print(f"[snapshot] 快照不存在：{SNAPSHOT}，先跑 --save", file=sys.stderr)
        return 2
    d = diff_snapshot(snapshot, _live_pools())
    if not any(d.values()):
        print(f"[snapshot] 与快照一致（{len(snapshot)} 条）")
        return 0
    print("[snapshot] ✗ 与快照有漂移：")
    for eid in d["added"]:
        print(f"    + 现状多出 {eid}（快照里没有——nightly promote 灌进来的？）")
    for eid in d["removed"]:
        print(f"    - 现状缺失 {eid}（被删了，还是这台机器上的池子没恢复？）")
    for eid, was, now in d["moved"]:
        print(f"    ~ {eid} 换桶：{was} → {now}")
    print("[snapshot] 确认这些改动是有意的，就跑 --save 再提交；否则跑 --restore 回滚")
    return 1


def cmd_restore() -> int:
    snapshot = _read(SNAPSHOT)
    if not snapshot:
        print(f"[snapshot] 快照不存在：{SNAPSHOT}", file=sys.stderr)
        return 2
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    from chat_bi_agent.llm import qwen_client

    buckets = restore_buckets(snapshot)
    questions = [r["question"] for rows in buckets.values() for r in rows]
    print(f"[snapshot] 重算 {len(questions)} 条 embedding ...", flush=True)
    vecs: list[list[float]] = []
    for i in range(0, len(questions), 10):
        vecs += qwen_client.embed(questions[i : i + 10])
    it = iter(vecs)
    for bucket, rows in buckets.items():
        for r in rows:
            r["embedding"] = next(it)
        _write(BUCKETS[bucket], rows)
        print(f"[snapshot] {BUCKETS[bucket]} ← {len(rows)} 条")
    for bucket, path in BUCKETS.items():
        if bucket not in buckets and path.exists():
            print(f"[snapshot] ⚠ 快照里没有 {bucket}，{path} 未改动")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--save", action="store_true", help="三个池子 → 快照")
    g.add_argument("--check", action="store_true", help="比对，有漂移退 1（不打 LLM）")
    g.add_argument("--restore", action="store_true", help="快照 → 重建池子（需 embedding 额度）")
    args = ap.parse_args()
    return cmd_save() if args.save else cmd_check() if args.check else cmd_restore()


if __name__ == "__main__":
    raise SystemExit(main())
