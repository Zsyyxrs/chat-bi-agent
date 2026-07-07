#!/usr/bin/env python3
"""跨 run 归因守门员：比较两份 result JSON，指出哪些字段该同却不同、哪些字段该异却同。

用法：
    python scripts/verify_ab.py \\
        results/bird_financial_p1_fewshot_2026-07-06.json \\
        results/bird_financial_p1_fewshot_sim04_2026-07-06.json

    # 声明这次 A/B 只应改动 few_shot.min_similarity；其他字段变动会 CRITICAL 报警
    python scripts/verify_ab.py A.json B.json --expected-differ few_shot

退出码：
    0 = 没有 CRITICAL 不一致（run 可归因 / A/B 干净）
    1 = 有 CRITICAL 不一致（跨模型 / 跨代码 / 跨配置 → 归因失效）
    2 = 输入错误或某个 result 缺 run_metadata

# 3 类字段：
- CRITICAL：变了就不能归因（commit_hash / config_hash / model / dev_json_md5 / sqlite_md5）
- EXPECTED_DIFFER：本次 A/B 声明要变的字段（默认无；用 --expected-differ 显式指定 top-level key）
- INFO：允许自然变化的字段（run_ts_utc / host / wall_clock_seconds / avg_latency_ms 等）

# 特殊 warning：
- 任一份 result 的 commit_dirty=True → 提醒不可复现
- 任一份缺 run_metadata → 提醒是老 result，只能靠传统字段比对
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 必须一致（否则跨环境跨代码，归因失败）
CRITICAL_META_FIELDS = ["commit_hash", "config_hash"]
CRITICAL_TOPLEVEL_FIELDS = ["model", "dev_json_md5", "sqlite_md5", "benchmark", "variant"]

# 允许差异（时间戳、机器名、总耗时等）
INFO_TOPLEVEL_FIELDS = {
    "run_date_utc",
    "wall_clock_seconds",
    "per_question",
    "summary",
    "run_metadata",  # metadata 内部字段单独比较
}
INFO_META_FIELDS = {"run_ts_utc", "host", "python_version", "commit_hash_full"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("a", type=Path, help="result JSON A（通常是基线）")
    p.add_argument("b", type=Path, help="result JSON B（对照组）")
    p.add_argument(
        "--expected-differ",
        action="append",
        default=[],
        help="声明本次 A/B 期望差异的 top-level 键，可重复。"
        "例：--expected-differ dialect --expected-differ few_shot",
    )
    return p.parse_args()


def _load(path: Path) -> dict:
    if not path.exists():
        print(f"[verify-ab] ERROR: {path} 不存在", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[verify-ab] ERROR: {path} 不是合法 JSON: {e}", file=sys.stderr)
        sys.exit(2)


def _mark(same: bool, label: str) -> str:
    tick = "✓" if same else "✗"
    return f"[{label:>16s}] {tick}"


def compare(a: dict, b: dict, expected_differ: list[str]) -> int:
    """Returns count of CRITICAL mismatches (exit code)."""
    print(f"A: {a.get('run_date_utc') or a.get('ran_at')}")
    print(f"B: {b.get('run_date_utc') or b.get('ran_at')}")
    print()

    critical_bad = 0
    warnings: list[str] = []

    a_meta = a.get("run_metadata")
    b_meta = b.get("run_metadata")
    if a_meta is None:
        warnings.append("A 缺 run_metadata（老 result）——只能靠传统字段对比")
    if b_meta is None:
        warnings.append("B 缺 run_metadata（老 result）——只能靠传统字段对比")

    if a_meta and b_meta:
        if a_meta.get("commit_dirty"):
            warnings.append("A run 时工作树 dirty（有未提交改动），不可复现")
        if b_meta.get("commit_dirty"):
            warnings.append("B run 时工作树 dirty（有未提交改动），不可复现")

        print("=== CRITICAL run_metadata（必须相等） ===")
        for f in CRITICAL_META_FIELDS:
            va = a_meta.get(f)
            vb = b_meta.get(f)
            same = va == vb
            print(f"  {f:<20s} A={va!s:<50s} B={vb!s:<50s} {'✓' if same else '✗ MISMATCH'}")
            if not same:
                critical_bad += 1

        print()
        print("=== INFO run_metadata（允许差异） ===")
        for f in INFO_META_FIELDS:
            va = a_meta.get(f)
            vb = b_meta.get(f)
            same = va == vb
            print(f"  {f:<20s} A={va!s:<50s} B={vb!s:<50s} {'==' if same else '~='}")

    print()
    print("=== CRITICAL 顶层字段（必须相等） ===")
    for f in CRITICAL_TOPLEVEL_FIELDS:
        if f not in a and f not in b:
            continue
        va = a.get(f)
        vb = b.get(f)
        same = va == vb
        print(f"  {f:<20s} A={va!s:<50s} B={vb!s:<50s} {'✓' if same else '✗ MISMATCH'}")
        if not same:
            critical_bad += 1

    print()
    print(f"=== 声明的 EXPECTED_DIFFER 字段：{expected_differ or '（未声明）'} ===")
    for f in expected_differ:
        va = a.get(f)
        vb = b.get(f)
        same = va == vb
        # 声明应该差异的字段，反而相同 = 可疑（A/B 没生效）
        marker = "⚠  相同（A/B 未生效？）" if same else "✓ 有差异"
        print(f"  {f:<20s} A={va!s:<40s} B={vb!s:<40s} {marker}")

    # Cross-check：非声明差异的 top-level 字段变了但不 CRITICAL 也不 INFO
    covered = set(CRITICAL_TOPLEVEL_FIELDS) | INFO_TOPLEVEL_FIELDS | set(expected_differ)
    other_diffs = []
    for f in set(a) | set(b):
        if f in covered:
            continue
        if a.get(f) != b.get(f):
            other_diffs.append(f)
    if other_diffs:
        print()
        print("=== 未分类差异字段（review 一下是否该加入 --expected-differ） ===")
        for f in other_diffs:
            print(f"  {f}: A={a.get(f)!s:.80s}  B={b.get(f)!s:.80s}")

    if warnings:
        print()
        print("=== ⚠  警告 ===")
        for w in warnings:
            print(f"  - {w}")

    print()
    if critical_bad == 0:
        print("[verify-ab] ✓ 无 CRITICAL 不一致，A/B 可归因")
    else:
        print(f"[verify-ab] ✗ {critical_bad} 处 CRITICAL 不一致，A/B 归因失效")
    return 1 if critical_bad > 0 else 0


def main() -> int:
    args = parse_args()
    a = _load(args.a)
    b = _load(args.b)
    return compare(a, b, args.expected_differ)


if __name__ == "__main__":
    sys.exit(main())
