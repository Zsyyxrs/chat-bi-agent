#!/usr/bin/env python3
"""离线构建值索引快照：把维度表里被标记的列的 DISTINCT 取值拉下来存成 JSON。

哪些列会被索引，由 `schema_docs.yaml` 里列级的 `value_index: true` 决定——
索引范围跟着 schema 定义走，不在脚本里另立一份名单。

用法：
    python scripts/build_value_index.py                    # 写默认路径
    python scripts/build_value_index.py --out /tmp/vi.json
    python scripts/build_value_index.py --max-values 500   # 单列取值上限

数据变动后需要重跑。P1 运行时只读快照，不连库——缓存缺失时 ValueIndex 退化成
空索引，值检索静默关闭而不是让 agent 崩掉。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from chat_bi_agent.agents.shared.sql_executor import SQLExecutor  # noqa: E402
from chat_bi_agent.schema.loader import SchemaLoader  # noqa: E402
from chat_bi_agent.schema.value_index import DEFAULT_CACHE_PATH  # noqa: E402


def indexed_columns(loader: SchemaLoader) -> list[tuple[str, str]]:
    """从 schema 定义里挑出标了 value_index: true 的列。"""
    out: list[tuple[str, str]] = []
    for doc in loader.docs:
        for col in doc.columns:
            if col.get("value_index"):
                out.append((doc.name, col["name"]))
    return out


def build(max_values: int) -> dict[str, list[str]]:
    loader = SchemaLoader()
    loader.load()  # 只读 YAML，不建 embedding 索引——这里用不上向量
    executor = SQLExecutor(statement_timeout_ms=30_000)

    snapshot: dict[str, list[str]] = {}
    for table, column in indexed_columns(loader):
        sql = (
            f"SELECT DISTINCT {column} AS v FROM {table} "
            f"WHERE {column} IS NOT NULL ORDER BY 1 LIMIT {max_values}"
        )
        rows, err = executor.execute(sql)
        if err is not None:
            raise RuntimeError(f"{table}.{column} 取值失败: {err}")
        values = [str(r["v"]) for r in (rows or []) if str(r["v"]).strip()]
        snapshot[f"{table}.{column}"] = values
        print(f"  {table}.{column:<22} {len(values):>4} 个取值")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument(
        "--max-values",
        type=int,
        default=2000,
        help="单列取值上限，防止误标高基数列把快照撑爆（默认 2000）",
    )
    args = parser.parse_args()

    print("从 schema_docs.yaml 读取被标记的列，逐列拉 DISTINCT 取值：")
    snapshot = build(args.max_values)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    total = sum(len(v) for v in snapshot.values())
    print(f"\n已写入 {args.out}（{len(snapshot)} 列 / {total} 个取值）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
