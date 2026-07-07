"""latency_stats 单测：验证空列表、单元素、离群点场景。"""

from __future__ import annotations

from chat_bi_agent.eval.latency_stats import latency_percentiles


def test_empty_returns_zeros():
    r = latency_percentiles([])
    assert r == {"n": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}


def test_single_element():
    r = latency_percentiles([1000])
    assert r["n"] == 1
    assert r["avg"] == r["p50"] == r["p95"] == r["p99"] == r["max"] == 1000


def test_linear_interp_median():
    r = latency_percentiles([10, 20, 30, 40])
    assert r["p50"] == 25  # (20+30)/2


def test_outliers_diverge_avg_from_p50():
    """postmortem 场景重现：3 个 300000 ms 离群点 + 17 个 ~20000 ms。
    avg 被拉高，但 p50 仍然接近典型值。
    """
    lat = [20000] * 17 + [300000] * 3
    r = latency_percentiles(lat)
    assert r["avg"] > 60000  # 拉爆
    assert 15000 <= r["p50"] <= 25000  # 典型值仍稳
    assert r["max"] == 300000


def test_percentiles_monotone():
    lat = list(range(100))
    r = latency_percentiles(lat)
    assert r["p50"] <= r["p95"] <= r["p99"] <= r["max"]


def test_returns_ints():
    r = latency_percentiles([1.5, 2.5, 3.5])
    for k in ["avg", "p50", "p95", "p99", "max"]:
        assert isinstance(r[k], int), f"{k} 不是 int: {r[k]!r}"
