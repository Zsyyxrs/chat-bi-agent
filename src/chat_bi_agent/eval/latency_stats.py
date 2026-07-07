"""延迟统计：avg / p50 / p95 / p99 / max。

背景（ADR-012 postmortem）：之前 result JSON 只落 avg_latency_ms，被 3 道 300s
超时拉爆成 53s 均值，导致我们把 preview 模型的"减速"误判成 few-shot 让 model
思考更快。加 p50/p95 后离群点一眼可见。

用法：
    from chat_bi_agent.eval.latency_stats import latency_percentiles
    stats = latency_percentiles([q["latency_ms"] for q in per_question])
    # → {"n": 106, "avg": 32300, "p50": 18500, "p95": 62000, "p99": 300000, "max": 300000}
"""

from __future__ import annotations

import math


def latency_percentiles(values_ms: list[int | float]) -> dict:
    """输入毫秒 latency 列表 → avg/p50/p95/p99/max 字典。空列表返回全 0。"""
    n = len(values_ms)
    if n == 0:
        return {"n": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    xs = sorted(int(v) for v in values_ms)
    return {
        "n": n,
        "avg": int(sum(xs) / n),
        "p50": int(_pct(xs, 0.50)),
        "p95": int(_pct(xs, 0.95)),
        "p99": int(_pct(xs, 0.99)),
        "max": xs[-1],
    }


def _pct(sorted_xs: list[int], q: float) -> float:
    """线性插值 percentile（与 numpy 默认 method='linear' 一致）。"""
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    idx = q * (len(sorted_xs) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_xs[lo]
    frac = idx - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac
