"""MetricResolver 单测：catalog 加载、spec → SQL 模板拼装、错误路径。

LLM 提取部分（question → MetricSpec）由 stub 提供，不真调 Qwen。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from chat_bi_agent.agents.p1.metric_resolver import (
    MetricCatalog,
    MetricResolverError,
    MetricSpec,
    render_sql_from_spec,
    resolve,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
METRICS_YAML = REPO_ROOT / "config" / "metrics.yaml"


# ---------------------- catalog loading ----------------------


def test_catalog_loads_all_metrics_from_yaml():
    cat = MetricCatalog.from_yaml(METRICS_YAML)
    ids = {m.id for m in cat.metrics}
    # 至少覆盖 6 个种子指标
    assert {
        "deposit_balance",
        "loan_balance",
        "customer_aum",
        "customer_count",
        "product_count",
        "transaction_amount",
    }.issubset(ids)


def test_catalog_get_by_id():
    cat = MetricCatalog.from_yaml(METRICS_YAML)
    m = cat.get("deposit_balance")
    assert m.display_name.startswith("存款余额")
    assert "存款" in m.aliases


def test_catalog_get_unknown_raises():
    cat = MetricCatalog.from_yaml(METRICS_YAML)
    with pytest.raises(MetricResolverError):
        cat.get("no_such_metric")


# ---------------------- render_sql_from_spec ----------------------


def _get_cat() -> MetricCatalog:
    return MetricCatalog.from_yaml(METRICS_YAML)


def test_render_simplest_no_dims_no_filters():
    """product_count 无维度无过滤 → SELECT COUNT(*) FROM dim_product dp"""
    spec = MetricSpec(metric_id="product_count", dims=[], filters=[], time_window=None)
    sql = render_sql_from_spec(spec, _get_cat())
    assert "COUNT(*)" in sql
    assert "FROM dim_product dp" in sql
    # no GROUP BY
    assert "GROUP BY" not in sql
    # no unnecessary joins
    assert "JOIN" not in sql


def test_render_with_enum_filter():
    """product_count 加 product_category='WEALTH' → 出现在 WHERE"""
    spec = MetricSpec(
        metric_id="product_count",
        dims=[],
        filters=[{"col": "product_category", "op": "=", "val": "WEALTH"}],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "dp.product_category = 'WEALTH'" in sql


def test_render_rejects_bad_enum_value():
    """题面写"高净值"却传成 filters['customer_tier']='高净值' → 拒绝，防生成语义错 SQL"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[{"col": "customer_tier", "op": "=", "val": "高净值"}],
        time_window=None,
    )
    with pytest.raises(MetricResolverError, match="enum"):
        render_sql_from_spec(spec, _get_cat())


def test_render_auto_adds_required_join_for_dim():
    """deposit_balance 用 dim=branch_city → 自动加 JOIN dim_branch"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=["branch_city"],
        filters=[],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "JOIN dim_branch dbr" in sql
    assert "dbr.city" in sql
    assert "GROUP BY" in sql


def test_render_auto_adds_required_join_for_filter():
    """deposit_balance 按 branch_city='上海' 过滤 → 自动加 JOIN dim_branch"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[{"col": "branch_city", "op": "=", "val": "上海"}],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "JOIN dim_branch dbr" in sql
    assert "dbr.city = '上海'" in sql


def test_render_no_duplicate_join_when_dim_and_filter_both_need_same_join():
    """dim=customer_tier + filter=customer_tier → 只加一次 JOIN dim_customer"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=["customer_tier"],
        filters=[{"col": "customer_tier", "op": "=", "val": "HIGH_NET_WORTH"}],
        time_window=None,
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert sql.count("JOIN dim_customer dc") == 1


def test_render_time_window_uses_date_column():
    """deposit_balance date_column=fbd.dt → time_window 走 fbd.dt BETWEEN"""
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[],
        time_window={"start": "2026-05-01", "end": "2026-05-31"},
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "fbd.dt >= DATE '2026-05-01'" in sql
    assert "fbd.dt <= DATE '2026-05-31'" in sql


def test_render_time_window_ignored_when_metric_has_no_date_column():
    """customer_aum 是快照，date_column=null → 就算给了 time_window 也不加"""
    spec = MetricSpec(
        metric_id="customer_aum",
        dims=[],
        filters=[],
        time_window={"start": "2026-05-01", "end": "2026-05-31"},
    )
    sql = render_sql_from_spec(spec, _get_cat())
    assert "BETWEEN" not in sql
    assert "dt" not in sql


def test_render_preserves_hard_filters():
    """deposit_balance 的 account_type IN ('CURRENT','SAVING') 必须在 WHERE 里"""
    spec = MetricSpec(metric_id="deposit_balance", dims=[], filters=[], time_window=None)
    sql = render_sql_from_spec(spec, _get_cat())
    assert "account_type IN ('CURRENT','SAVING')" in sql


def test_render_unknown_dim_raises():
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=["not_a_dim"],
        filters=[],
        time_window=None,
    )
    with pytest.raises(MetricResolverError, match="unknown dim"):
        render_sql_from_spec(spec, _get_cat())


def test_render_unknown_filter_raises():
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[{"col": "not_a_field", "op": "=", "val": "x"}],
        time_window=None,
    )
    with pytest.raises(MetricResolverError, match="unknown filter"):
        render_sql_from_spec(spec, _get_cat())


# ---------------------- resolve() — end-to-end with mocked LLM ----------------------


def _mock_llm(spec_json: str):
    """给 qwen_client.chat 装一个假 client，返回固定 JSON。"""

    class _R:
        def __init__(self, c):
            self.content = c

    return _R(f"```json\n{spec_json}\n```")


def test_resolve_happy_path():
    """LLM 返回合法 spec → SQL 从模板拼出来。"""
    spec_json = (
        '{"metric_id":"customer_count","dims":["branch_city"],'
        '"filters":[{"col":"customer_tier","op":"=","val":"MASS"}],"time_window":null}'
    )
    with patch(
        "chat_bi_agent.agents.p1.metric_resolver.qwen_client.chat",
        return_value=_mock_llm(spec_json),
    ):
        sql = resolve(question="杭州分行的大众客户数量", catalog=_get_cat())
    assert "COUNT(DISTINCT dc.customer_id)" in sql
    assert "GROUP BY dbr.city" in sql or "GROUP BY city" in sql or "dbr.city" in sql
    assert "dc.customer_tier = 'MASS'" in sql


def test_resolve_null_metric_id_raises():
    """LLM 觉得题目不匹配任何 metric → metric_id=null → resolve 抛 NoMetricMatch。"""
    with patch(
        "chat_bi_agent.agents.p1.metric_resolver.qwen_client.chat",
        return_value=_mock_llm('{"metric_id":null,"dims":[],"filters":[],"time_window":null}'),
    ):
        with pytest.raises(MetricResolverError, match="no metric matched"):
            resolve(question="随便一个不像 metric 的问题", catalog=_get_cat())


def test_resolve_invalid_llm_json_raises():
    with patch(
        "chat_bi_agent.agents.p1.metric_resolver.qwen_client.chat",
        return_value=_mock_llm("not json"),
    ):
        with pytest.raises(MetricResolverError):
            resolve(question="q", catalog=_get_cat())
