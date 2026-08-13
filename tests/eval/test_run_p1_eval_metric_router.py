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


# ---------------------- 路由准确率（对 ground truth）----------------------


def _mkr(route, score, expected_route, fail=None):
    return {
        "route": route,
        "score": score,
        "prefilter_cosine": 0.8,
        "metric_fail_reason": fail,
        "expected_route": expected_route,
    }


def test_routing_accuracy_counts_tp_fp_fn_tn():
    """有了 expected_route 才能算准确率，而不只是"触发了多少次"。"""
    from chat_bi_agent.runners.run_p1_eval import _summarize_metric_router

    per_q = [
        _mkr("metric", 1.0, "metric"),  # TP：该走且走了
        _mkr("metric_then_nl2sql", 0.9, "metric"),  # FN：该走但没走通
        _mkr("nl2sql", 0.9, "metric"),  # FN：该走但 prefilter 没命中
        _mkr("metric", 0.5, "nl2sql"),  # FP：不该走却走了
        _mkr("nl2sql", 0.9, "nl2sql"),  # TN
        _mkr("metric_then_nl2sql", 0.9, "nl2sql"),  # TN（prefilter 误触但已回退）
    ]
    r = _summarize_metric_router(per_question=per_q, enabled=True, catalog_path="x", threshold=0.6)
    acc = r["routing_accuracy"]
    assert acc["n_labeled"] == 6
    assert acc["true_positive"] == 1
    assert acc["false_positive"] == 1
    assert acc["false_negative"] == 2
    assert acc["true_negative"] == 2
    assert acc["precision"] == 0.5  # 1/(1+1)
    assert acc["recall"] == round(1 / 3, 4)
    # 两条都不该过阈值却过了：一条走成了 metric，一条误触后回退。
    # 回退那条答案不受影响，但仍算 prefilter 误触——它白花了一次 LLM 调用。
    assert acc["prefilter_false_positive"] == 2


def test_routing_accuracy_absent_when_no_labels():
    """老评测集没有 expected_route，不该硬算。"""
    from chat_bi_agent.runners.run_p1_eval import _summarize_metric_router

    r = _summarize_metric_router(
        per_question=[_mk("metric", 1.0)], enabled=True, catalog_path="x", threshold=0.7
    )
    assert r["routing_accuracy"] is None
