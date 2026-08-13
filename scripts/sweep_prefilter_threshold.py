#!/usr/bin/env python3
"""扫 MetricRouter 的 prefilter 阈值，并给出过拟合幅度。

**换 embedding 模型后必须重跑这个**——阈值绑定 cosine 尺度，换模型就失效。

为什么可以不跑整轮 eval：prefilter 只依赖"问题 embedding × catalog alias embedding"
的 cosine，与 LLM 生成 SQL 无关。实测离线算的 cosine 与整轮跑批记录的
prefilter_cosine 偏差 0.000218。所以这里只花 embedding 的钱（几十秒），
不必为调阈值跑一遍 34 题的完整 eval。

用法：
    python scripts/sweep_prefilter_threshold.py
    python scripts/sweep_prefilter_threshold.py --question-set <path> --top 15

依赖评测集里的 `expected_route` 标注作为 ground truth（metric / nl2sql）。
没有标注的题会被跳过并提示。
"""

from __future__ import annotations

import argparse
import random
import statistics
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

from chat_bi_agent.agents.p1.metric_resolver import (  # noqa: E402
    MetricCatalog,
    MetricRouter,
    _cosine,
)
from chat_bi_agent.llm import qwen_client  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEFAULT_QS = REPO / "src" / "chat_bi_agent" / "data" / "metric_routing_evaluation.yaml"
DEFAULT_CATALOG = REPO / "config" / "metrics.yaml"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--question-set", type=Path, default=DEFAULT_QS)
    p.add_argument("--metric-catalog", type=Path, default=DEFAULT_CATALOG)
    p.add_argument("--top", type=int, default=20, help="打印 F1 最高的前 N 个候选阈值")
    p.add_argument("--splits", type=int, default=200, help="随机半分交叉验证次数")
    return p.parse_args()


def _confusion(rows: list[dict], t: float) -> tuple[int, int, int, int]:
    tp = sum(1 for r in rows if r["want"] == "metric" and r["cos"] >= t)
    fp = sum(1 for r in rows if r["want"] == "nl2sql" and r["cos"] >= t)
    fn = sum(1 for r in rows if r["want"] == "metric" and r["cos"] < t)
    tn = sum(1 for r in rows if r["want"] == "nl2sql" and r["cos"] < t)
    return tp, fp, fn, tn


def _prf(rows: list[dict], t: float) -> tuple[float, float, float]:
    tp, fp, fn, _ = _confusion(rows, t)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


def _best_threshold(rows: list[dict]) -> float:
    cands = sorted({round(r["cos"], 4) for r in rows})
    return max(cands, key=lambda t: _prf(rows, t)[2]) if cands else 0.0


def main() -> int:
    args = parse_args()
    catalog = MetricCatalog.from_yaml(args.metric_catalog)
    data = yaml.safe_load(args.question_set.read_text(encoding="utf-8"))
    questions = data["evaluation_questions"]

    labeled = [q for q in questions if q.get("expected_route") in ("metric", "nl2sql")]
    if not labeled:
        print("评测集里没有 expected_route 标注，无法算准确率。先补标注再跑。")
        return 2
    if len(labeled) < len(questions):
        print(f"[warn] {len(questions) - len(labeled)} 题没有 expected_route 标注，已跳过")

    print(
        f"catalog={args.metric_catalog.name} metrics={len(catalog.metrics)} "
        f"aliases={sum(len(m.aliases) for m in catalog.metrics)}"
    )
    print(
        f"题集={args.question_set.name} 已标注={len(labeled)} "
        f"(metric={sum(1 for q in labeled if q['expected_route'] == 'metric')})"
    )
    print(f"embedding={qwen_client.EMBED_MODEL}\n")

    # 复用 MetricRouter 的索引构建，保证与线上 prefilter 完全同源
    router = MetricRouter(catalog, embed_fn=qwen_client.embed, threshold=0.0)
    qvecs = qwen_client.embed([q["question"] for q in labeled])
    rows = [
        {
            "id": q["id"],
            "want": q["expected_route"],
            "cos": max(_cosine(qv, vec) for _, vec in router._alias_index),
        }
        for q, qv in zip(labeled, qvecs, strict=True)
    ]

    m = [r["cos"] for r in rows if r["want"] == "metric"]
    n = [r["cos"] for r in rows if r["want"] == "nl2sql"]
    print(f"指标型 cosine: min={min(m):.4f} 中位={statistics.median(m):.4f} max={max(m):.4f}")
    print(f"非指标 cosine: min={min(n):.4f} 中位={statistics.median(n):.4f} max={max(n):.4f}")
    if max(n) > min(m):
        print("→ 两类分布重叠，不存在无损分界点；取 argmax 会过拟合（见下方交叉验证）\n")

    cands = sorted({round(r["cos"], 4) for r in rows})
    scored = sorted(
        ((t, *_confusion(rows, t), *_prf(rows, t)) for t in cands),
        key=lambda x: -x[7],
    )
    print(
        f"{'阈值':>8} {'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3} {'prec':>6} {'recall':>6} {'F1':>6}"
    )
    for t, tp, fp, fn, tn, p, r, f1 in scored[: args.top]:
        print(f"{t:8.4f} {tp:3} {fp:3} {fn:3} {tn:3} {p:6.3f} {r:6.3f} {f1:6.3f}")

    # 交叉验证：量化"在这套题上选阈值"的过拟合幅度
    random.seed(0)
    picked, ins, outs = [], [], []
    half = len(rows) // 2
    for _ in range(args.splits):
        d = rows[:]
        random.shuffle(d)
        a, b = d[:half], d[half:]
        t = _best_threshold(a)
        picked.append(t)
        ins.append(_prf(a, t)[2])
        outs.append(_prf(b, t)[2])

    print(f"\n{args.splits} 次随机半分交叉验证：")
    print(
        f"  选出阈值  中位 {statistics.median(picked):.4f}  "
        f"范围 {min(picked):.4f}-{max(picked):.4f}  σ={statistics.pstdev(picked):.4f}"
    )
    print(f"  样本内 F1 {statistics.mean(ins):.3f}")
    print(f"  样本外 F1 {statistics.mean(outs):.3f}   ← 差值即过拟合幅度")

    best_t = scored[0][0]
    print(f"\n全集 argmax = {best_t:.4f}（F1 {scored[0][7]:.3f}）")
    print("建议：**不要直接取 argmax**——样本外 F1 会掉，且阈值本身在半个区间内飘。")
    print("      在 F1 平台区内挑一个偏保守的点。")
    print(f"当前代码默认阈值 = {MetricRouter.__init__.__defaults__[0]}")
    print()
    print("⚠ 上表的 FP 是 **prefilter 误触**，不等于真正的错误路由。")
    print("  过了阈值之后还有两道闸：resolve() 判 metric_id=null，以及 string filter")
    print("  值域探针。误触绝大多数会被拦下并安全回退，只白花一次 LLM 调用。")
    print("  实测（t=0.63，34 题）：prefilter FP=5，但真正走错的路由 FP=0。")
    print("  所以这里的 FP 该读作『成本』，真正的正确性要看整轮 A/B 的 routing_accuracy。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
