#!/usr/bin/env python3
"""把 example_pool_prod.jsonl 按 MetricRouter 能否接住做三档分流，并检出口径漂移。

**catalog 每次扩容就重跑这个。** 它回答两个问题：
  1. 池子里哪些问题语义层已经能接住（接住的就不必再占 few-shot 的位置）
  2. 接住的那些，SQL 口径对不对

第 2 问比第 1 问重要。2026-08-28 首轮分流的 A 档 8 条里有 2 条是**跑得通、
返回非空、但数字错**的假阳性：`campaign_conversion_amount` 的 filter_catalog
缺 `response_type`，LLM 只能用 campaign_name 顶替，于是漏掉 CONVERTED 口径。
执行反馈捕不到这种错——所以这里拿 gold SQL 的字面量做交叉比对。

三档：
    A  prefilter 命中且 resolve 成功    → 语义层接住了（再看有无口径漂移）
    B  prefilter 命中但 resolve 拒绝    → 二次把关拦下，看 fail_reason 判对错
    C  prefilter 未命中                 → recall 问题，看 cosine 离阈值多远

用法：
    python scripts/triage_example_pool.py                    # 全量跑 + 报告
    python scripts/triage_example_pool.py --probe            # 带值域探针（按生产接线）
    python scripts/triage_example_pool.py --out triage.json  # 另存明细
    python scripts/triage_example_pool.py --report triage.json  # 不跑，只重读明细出报告

`--report` 不打 LLM/embedding，改完 catalog 前后对比很省钱。

退出码：有口径漂移返 1（A 档存在假阳性），否则 0。
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from chat_bi_agent.agents.p1.metric_resolver import (  # noqa: E402
    MetricCatalog,
    MetricRouter,
)
from chat_bi_agent.llm import qwen_client  # noqa: E402

# 分流诊断以「总行审计身份」跑：不带身份的话，受行级权限管控的指标会被记成
# 路由失败，把权限拒绝误读成召回不行——诊断本身就错了。
TRIAGE_SESSION_PROPS: dict[str, object] = {
    "branch_scope": "ALL",
    "branch_id": "__unused_by_hq__",
}

REPO = Path(__file__).resolve().parents[1]
DEFAULT_POOL = REPO / "data" / "example_pool_prod.jsonl"
DEFAULT_CATALOG = REPO / "config" / "metrics.yaml"

_STRING_LITERAL_RE = re.compile(r"'([^']*)'")
# ISO 日期不参与口径比对：`< DATE '2027-01-01'` 与 `<= DATE '2026-12-31'` 等价，
# 逐字面量比对判不了半开/闭区间。时间窗在 spec 里本来就看得见。
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T].*)?$")


def caliber_drift(gold_sql: str, gen_sql: str) -> list[str]:
    """比对两条 SQL 的口径字面量，返回人读的告警（空 = 口径一致）。

    只比字符串字面量，不比结构：别名、JOIN 写法、`COUNT(*)` vs `COUNT(col)`
    都属于等价改写，不该报。漏了或多了一个 `'CONVERTED'` 这种才是真问题。
    """

    def lits(sql: str) -> set[str]:
        return {v for v in _STRING_LITERAL_RE.findall(sql) if not _ISO_DATE_RE.match(v)}

    gold, gen = lits(gold_sql), lits(gen_sql)
    drift = []
    for v in sorted(gold - gen):
        drift.append(f"gold 有而生成没有：{v!r}——口径漏了，结果偏大")
    for v in sorted(gen - gold):
        drift.append(f"生成有而 gold 没有：{v!r}——口径多了，结果偏小")
    return drift


def build_router(catalog, embed_fn, threshold: float = 0.63, top_k: int = 8, probe_fn=None):
    """按生产接线造 MetricRouter。`probe_fn` 是 string filter 的值域探针。

    不注入探针时 resolve 少一层「值不存在就退回 NL2SQL」，分档偏宽松——拿偏宽松的
    分档决定「哪些题移出 few-shot 池」会两头落空：移出去了，生产上却被探针拒绝退回
    NL2SQL，此时 few-shot 也已经没了。所以要拿分流结果动池子，就该带 `--probe` 跑。
    """
    return MetricRouter(
        catalog=catalog,
        embed_fn=embed_fn,
        threshold=threshold,
        probe_fn=probe_fn,
        top_k=top_k,
    )


def tier_of(rec: dict) -> str:
    if rec["routed"]:
        return "A"
    return "B" if rec["prefilter_hit"] else "C"


def run(pool: Path, catalog_path: Path, threshold: float, top_k: int, probe: bool) -> list[dict]:
    rows = [json.loads(line) for line in pool.open(encoding="utf-8") if line.strip()]
    catalog = MetricCatalog.from_yaml(catalog_path)
    probe_fn = None
    if probe:
        from chat_bi_agent.agents.shared.sql_executor import SQLExecutor

        probe_fn = SQLExecutor().execute
        print("[triage] 值域探针 ON（按生产接线；需要 PG 可连）", flush=True)
    else:
        print("[triage] 值域探针 OFF——分档偏宽松，别拿这个结果动 few-shot 池", flush=True)
    router = build_router(
        catalog=catalog,
        embed_fn=qwen_client.embed,
        threshold=threshold,
        top_k=top_k,
        probe_fn=probe_fn,
    )

    out: list[dict] = []
    for i, r in enumerate(rows):
        t0 = time.perf_counter()
        gold = " ".join(r["sql"].split())
        try:
            rr = router.try_route(r["question"], session_props=TRIAGE_SESSION_PROPS)
            sql = " ".join(rr.sql.split()) if rr.sql else None
            rec = {
                "idx": i,
                "example_id": r["example_id"],
                "question": r["question"],
                "gold_sql": gold,
                "cosine": round(rr.cosine, 4),
                "prefilter_hit": rr.prefilter_hit,
                "metric_id": rr.metric_id,
                "fail_reason": rr.fail_reason,
                "routed": rr.sql is not None and rr.spec is not None,
                "spec": (rr.spec.__dict__ if rr.spec else None),
                "sql": sql,
                "drift": caliber_drift(gold, sql) if sql else [],
            }
        except Exception as e:  # noqa: BLE001 — 分流是诊断工具，一条炸了不该中断全量
            rec = {
                "idx": i,
                "example_id": r["example_id"],
                "question": r["question"],
                "gold_sql": gold,
                "cosine": None,
                "prefilter_hit": None,
                "metric_id": None,
                "fail_reason": f"EXC:{type(e).__name__}: {e}",
                "routed": False,
                "spec": None,
                "sql": None,
                "drift": [],
            }
        rec["ms"] = int((time.perf_counter() - t0) * 1000)
        out.append(rec)
        flag = "⚠" if rec["drift"] else " "
        print(
            f"[{i + 1:2d}/{len(rows)}] {tier_of(rec)}{flag} cos={rec['cosine']} "
            f"{rec['metric_id'] or '-':26s} {rec['question'][:34]}",
            flush=True,
        )
    return out


def split_pool(
    pool_rows: list[dict], recs: list[dict], adjudicated: set[str]
) -> tuple[list[dict], list[dict]]:
    """按分流结果把池子劈成 (留下, 移出)。移出 = 语义层已经确定性接住的那些。

    只有 A 档且口径干净的才移出。**漂移的默认留下**：漂移可能意味着 catalog 有
    缺口、生成的 SQL 是错的，移出去等于既没有 governed 模板又没有 few-shot 兜底。
    `adjudicated` 是人已经逐条裁决过「以 catalog 为准，gold 才是错的那个」的
    example_id——只有这种才允许带着漂移移出。

    裁决名单里出现池子里没有的 id 直接抛：打错字会让一条本该移出的题悄悄留下，
    而这种静默偏差正是这套工具要消灭的。
    """
    by_id = {r["example_id"] for r in pool_rows}
    unknown = sorted(adjudicated - by_id)
    if unknown:
        raise ValueError(f"裁决名单里有池子里不存在的 example_id: {unknown}")

    migrate_ids = {
        r["example_id"]
        for r in recs
        if r["routed"] and (not r["drift"] or r["example_id"] in adjudicated)
    }
    keep = [r for r in pool_rows if r["example_id"] not in migrate_ids]
    migrate = [r for r in pool_rows if r["example_id"] in migrate_ids]
    return keep, migrate


def report(recs: list[dict], threshold: float) -> int:
    tiers = {t: [r for r in recs if tier_of(r) == t] for t in "ABC"}
    drifted = [r for r in tiers["A"] if r["drift"]]
    clean = len(tiers["A"]) - len(drifted)

    print(f"\n{'=' * 72}\n分流结果（共 {len(recs)} 条）\n{'=' * 72}")
    print(
        f"  A 命中且 resolve 成功   {len(tiers['A']):2d}  其中口径干净 {clean}，漂移 {len(drifted)}"
    )
    print(f"  B 命中但 resolve 拒绝   {len(tiers['B']):2d}")
    print(f"  C prefilter 未命中      {len(tiers['C']):2d}")

    if drifted:
        print(f"\n⚠ A 档口径漂移 {len(drifted)} 条——SQL 跑得通但数字错，执行反馈捕不到：")
        for r in drifted:
            print(f"\n  [{r['idx']}] {r['metric_id']}  {r['question'][:50]}")
            for d in r["drift"]:
                print(f"      {d}")
            print(f"      gold: {r['gold_sql'][:150]}")
            print(f"      gen : {r['sql'][:150]}")

    if tiers["B"]:
        print("\nB 档——prefilter 命中但被 resolve 拒绝（逐条判断是正确拒绝还是能力缺口）：")
        for r in tiers["B"]:
            print(
                f"  [{r['idx']:2d}] cos={r['cosine']} {r['metric_id'] or '-':26s} "
                f"{r['fail_reason'] or '-':12s} {r['question'][:44]}"
            )

    if tiers["C"]:
        print(f"\nC 档——prefilter 未命中（阈值 {threshold}，按 cosine 倒序，越靠前越可惜）：")
        for r in sorted(tiers["C"], key=lambda x: -(x["cosine"] or 0)):
            print(f"  [{r['idx']:2d}] cos={r['cosine']} {r['question'][:56]}")

    print(f"\n可移出 few-shot 池的候选：A 档口径干净的 {clean} 条")
    return 1 if drifted else 0


def migrate_pool(pool: Path, archive: Path, recs: list[dict], adjudicated: set[str]) -> int:
    """执行迁出：改写 pool，把移出的追加进 archive。返回退出码。"""
    pool_rows = [json.loads(line) for line in pool.open(encoding="utf-8") if line.strip()]
    keep, migrate = split_pool(pool_rows, recs, adjudicated)
    if not migrate:
        print("\n[migrate] 没有可移出的条目，池子未改动")
        return 0

    def dump(rows: list[dict], path: Path, mode: str) -> None:
        with path.open(mode, encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    archived = set()
    if archive.exists():
        archived = {
            json.loads(line)["example_id"]
            for line in archive.open(encoding="utf-8")
            if line.strip()
        }
    fresh = [r for r in migrate if r["example_id"] not in archived]
    dump(fresh, archive, "a" if archive.exists() else "w")
    dump(keep, pool, "w")

    print(f"\n[migrate] {pool} : {len(pool_rows)} → {len(keep)} 条（移出 {len(migrate)}）")
    print(f"[migrate] 归档 {archive} 追加 {len(fresh)} 条（已在档 {len(migrate) - len(fresh)} 条）")
    for r in migrate:
        mark = "＊裁决" if r["example_id"] in adjudicated else "    "
        print(f"  {mark} {r['example_id']}  {r['question'][:50]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--pool", type=Path, default=DEFAULT_POOL)
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--threshold", type=float, default=0.63, help="prefilter cosine 阈值")
    ap.add_argument("--top-k", type=int, default=8, help="写进抽取 prompt 的候选指标数")
    ap.add_argument(
        "--probe",
        action="store_true",
        help="注入 string filter 值域探针（需 PG 可连）。要拿结果动 few-shot 池就必须带",
    )
    ap.add_argument("--out", type=Path, help="把明细写到这个 json")
    ap.add_argument("--report", type=Path, help="不跑路由，直接读这个 json 出报告")
    ap.add_argument(
        "--migrate",
        action="store_true",
        help="把 A 档口径干净的题真的移出 --pool，写进 --migrate-archive。写盘操作",
    )
    ap.add_argument(
        "--migrate-archive",
        type=Path,
        default=REPO / "data" / "example_pool_metric_governed.jsonl",
        help="移出去的题存这里（不丢，可回滚；也留一份审计）",
    )
    ap.add_argument(
        "--adjudicated",
        nargs="*",
        default=[],
        metavar="EXAMPLE_ID",
        help="人已裁决「以 catalog 为准」的漂移条目 example_id，允许带漂移移出",
    )
    args = ap.parse_args()

    if args.report:
        recs = json.loads(args.report.read_text(encoding="utf-8"))
        for r in recs:  # 老明细可能没有 drift 字段，就地补算
            if "drift" not in r:
                r["drift"] = caliber_drift(r["gold_sql"], r["sql"]) if r.get("sql") else []
    else:
        recs = run(args.pool, args.catalog, args.threshold, args.top_k, args.probe)
        if args.out:
            args.out.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n明细已写入 {args.out}")

    rc = report(recs, args.threshold)

    if args.migrate:
        rc = migrate_pool(args.pool, args.migrate_archive, recs, set(args.adjudicated))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
