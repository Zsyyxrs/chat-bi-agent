"""P1 tab 接语义层路由的接线。

核心约束：**语义层是增强项，绝不能把主路径带崩**。目录缺失、embedding 调用失败、
catalog 解析出错——任何一种都必须降级成"不挂 router"，而不是让整个 P1 tab 报错。

背景：路由层在 2026-08-13 判定绿灯（precision 1.000 / recall 0.75），但一直只在
eval 里跑，生产三个 tab 都没传 metric_router——验证过的东西真实用户一次没用上。
"""

from unittest.mock import MagicMock, patch

from streamlit_app.tabs import p1_nl2sql


def test_returns_none_when_catalog_missing(tmp_path):
    with patch.object(p1_nl2sql, "_METRICS_CATALOG_PATH", tmp_path / "nope.yaml"):
        assert p1_nl2sql._build_metric_router_if_available() is None


def test_builds_router_when_catalog_present():
    with (
        patch.object(p1_nl2sql.qwen_client, "embed", side_effect=lambda t: [[1.0, 0.0] for _ in t]),
        patch.object(p1_nl2sql, "SQLExecutor", MagicMock()),
    ):
        router = p1_nl2sql._build_metric_router_if_available()
    assert router is not None
    assert len(router.catalog.metrics) > 0
    # 必须注入值域探针，否则 string filter 塞错值会静默返回空结果
    assert router.probe_fn is not None


def test_uses_tuned_default_threshold():
    """不硬编码阈值——跟着 MetricRouter 的默认值走，改默认值时不会漏改这里。"""
    with (
        patch.object(p1_nl2sql.qwen_client, "embed", side_effect=lambda t: [[1.0, 0.0] for _ in t]),
        patch.object(p1_nl2sql, "SQLExecutor", MagicMock()),
    ):
        router = p1_nl2sql._build_metric_router_if_available()
    import inspect

    from chat_bi_agent.agents.p1.metric_resolver import MetricRouter

    default = inspect.signature(MetricRouter.__init__).parameters["threshold"].default
    assert router.threshold == default


def test_degrades_to_none_when_embedding_fails():
    """embedding 挂了只能少一个增强项，不能让 P1 整个不可用。"""
    with (
        patch.object(p1_nl2sql.qwen_client, "embed", side_effect=RuntimeError("dashscope down")),
        patch.object(p1_nl2sql, "SQLExecutor", MagicMock()),
    ):
        assert p1_nl2sql._build_metric_router_if_available() is None


def test_degrades_to_none_when_catalog_malformed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("metrics: [{id: x}]", encoding="utf-8")  # 缺必填字段
    with patch.object(p1_nl2sql, "_METRICS_CATALOG_PATH", bad):
        assert p1_nl2sql._build_metric_router_if_available() is None


def test_metric_badge_text_prefers_display_name():
    """给用户看的是业务名（"存款余额（日均…）"），不是 metric_id。"""
    from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog

    cat = MetricCatalog.from_yaml(p1_nl2sql._METRICS_CATALOG_PATH)
    assert p1_nl2sql._metric_label(cat, "deposit_balance").startswith("存款余额")
    # 未知 id 不该抛异常，退回原样显示
    assert p1_nl2sql._metric_label(cat, "no_such_metric") == "no_such_metric"
    assert p1_nl2sql._metric_label(None, "deposit_balance") == "deposit_balance"
