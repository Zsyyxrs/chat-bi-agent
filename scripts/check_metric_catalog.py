#!/usr/bin/env python3
"""校验 config/metrics.yaml 与 schema_docs.yaml 的一致性，并打印指标血缘。

**改完 metrics.yaml 就跑这个。** 由来见 ADR-013 Update 2026-08-12：模板里的列名
只有真正 execute 才会被校验，当时 6 指标 × 全部 dim/filter 共 48 个组合里有 18 个
是坏的。这里做的是那次结论的静态版本——不打 DB，纯对照 schema_docs.yaml，所以
`tests/p1/test_metric_lineage.py::test_real_catalog_matches_real_schema` 能把它
当常规单测跑；本脚本是同一套检查的人读版，外加血缘表。

用法：
    python scripts/check_metric_catalog.py              # 校验 + 血缘
    python scripts/check_metric_catalog.py --lineage    # 只看血缘
    python scripts/check_metric_catalog.py --quiet      # 只在有 error 时输出

退出码：有 error 返 1，只有 latent 或全干净返 0。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chat_bi_agent.agents.p1.metric_lineage import metric_lineage, validate_catalog
from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog
from chat_bi_agent.schema.loader import SchemaLoader

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO_ROOT / "config" / "metrics.yaml"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--lineage", action="store_true", help="只打印血缘，跳过校验")
    ap.add_argument("--quiet", action="store_true", help="没有 error 就不输出")
    args = ap.parse_args()

    catalog = MetricCatalog.from_yaml(args.catalog)
    loader = SchemaLoader()
    loader.load()

    if args.lineage:
        _print_lineage(catalog)
        return 0

    issues = validate_catalog(catalog, loader)
    errors = [i for i in issues if i.severity == "error"]
    latent = [i for i in issues if i.severity == "latent"]

    if errors:
        print(f"✗ {len(errors)} 处引用对不上 schema（会出现在真实 SQL 里）：")
        for i in errors:
            print(f"    {i.metric_id:28s} {i.ref:42s} {i.message}")
    elif not args.quiet:
        print(f"✓ {len(catalog.metrics)} 个指标的表名/列名全部对得上 schema")

    if latent and not args.quiet:
        print(
            f"\n· {len(latent)} 处潜伏项——挂在**不可达的 join** 上，当前拼不进 SQL，"
            "但谁给它加一条 requires_join 就会炸："
        )
        for i in latent:
            print(f"    {i.metric_id:28s} {i.ref:42s} {i.message}")

    if not args.quiet:
        print()
        _print_lineage(catalog)

    return 1 if errors else 0


def _print_lineage(catalog: MetricCatalog) -> None:
    print("指标血缘（必然依赖 = 每次查询都用；条件依赖 = 选了某个 dim/filter 才用）")
    for metric in catalog.metrics:
        lin = metric_lineage(metric)
        print(f"\n  {lin.metric_id}")
        print(f"    必然  {', '.join(lin.required_tables)}")
        for table, triggers in sorted(lin.conditional_tables.items()):
            print(f"    条件  {table:24s} ← {', '.join(triggers)}")


if __name__ == "__main__":
    sys.exit(main())
