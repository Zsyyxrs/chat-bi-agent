"""run_p1_eval 的 metric_router 汇总段结构与数字。"""

from chat_bi_agent.runners.run_p1_eval import _summarize_metric_router


def _mk(route, score, fail=None):
    return {
        "route": route,
        "score": score,
        "prefilter_cosine": 0.85,
        "metric_fail_reason": fail,
    }


def test_summarize_metric_router_disabled_returns_none_section():
    result = _summarize_metric_router(
        per_question=[_mk("nl2sql", 0.9)],
        enabled=False,
        catalog_path=None,
        threshold=None,
    )
    assert result is None


def test_summarize_metric_router_three_route_classes():
    per_q = [
        _mk("metric", 0.9),
        _mk("metric", 0.85),
        _mk("metric_then_nl2sql", 0.8, fail="no_metric"),
        _mk("nl2sql", 0.95),
        _mk("nl2sql", 0.9),
    ]
    result = _summarize_metric_router(
        per_question=per_q,
        enabled=True,
        catalog_path="config/metrics.yaml",
        threshold=0.7,
    )
    assert result["enabled"] is True
    assert result["catalog_path"] == "config/metrics.yaml"
    assert result["prefilter_threshold"] == 0.7
    assert result["n_total"] == 5
    assert result["n_route_metric"] == 2
    assert result["n_route_metric_then_nl2sql"] == 1
    assert result["n_route_nl2sql"] == 2
    assert result["n_prefilter_hit"] == 3  # metric + metric_then_nl2sql
    assert result["metric_hit_rate"] == 0.4  # 2/5
    assert result["prefilter_hit_rate"] == 0.6  # 3/5
    assert result["precision_when_hit"] == round((0.9 + 0.85) / 2, 4)
    assert result["precision_when_fallback"] == 0.8
    assert result["precision_when_bypass"] == round((0.95 + 0.9) / 2, 4)
    assert result["fallback_rate"] == round(1 / 3, 4)
    assert result["fail_reason_breakdown"]["no_metric"] == 1
    assert result["fail_reason_breakdown"]["validator_fail"] == 0


def test_summarize_metric_router_all_bypass():
    per_q = [_mk("nl2sql", 0.9), _mk("nl2sql", 0.8)]
    result = _summarize_metric_router(
        per_question=per_q,
        enabled=True,
        catalog_path="x",
        threshold=0.7,
    )
    assert result["n_route_metric"] == 0
    assert result["metric_hit_rate"] == 0.0
    assert result["precision_when_hit"] is None  # 无命中样本
    assert result["precision_when_fallback"] is None
    assert result["fallback_rate"] is None  # 无 prefilter hit 分母


def test_summarize_metric_router_all_hit():
    per_q = [_mk("metric", 0.95), _mk("metric", 0.9)]
    result = _summarize_metric_router(
        per_question=per_q,
        enabled=True,
        catalog_path="x",
        threshold=0.7,
    )
    assert result["n_route_nl2sql"] == 0
    assert result["precision_when_bypass"] is None
    assert result["metric_hit_rate"] == 1.0
