"""MetricRouter：embedding cosine prefilter + resolve 一体化，never raises。"""

import math
from unittest.mock import MagicMock

import pytest

from chat_bi_agent.agents.p1 import metric_resolver as _mr_module
from chat_bi_agent.agents.p1.metric_resolver import (
    MetricCatalog,
    MetricResolverError,
    MetricRouter,
    MetricSpec,
    RouteResult,
)


@pytest.fixture(autouse=True)
def _restore_resolve_to_spec_and_sql():
    """Restore the module-level _resolve_to_spec_and_sql after each test that rebinds it."""
    original = _mr_module._resolve_to_spec_and_sql
    yield
    _mr_module._resolve_to_spec_and_sql = original


def _tiny_catalog(tmp_path):
    yml = tmp_path / "m.yaml"
    yml.write_text(
        """
version: 1
metrics:
  - id: deposit_balance
    display_name: 存款余额
    aliases: [存款余额, 日均存款]
    fact_table: fct_balance_daily
    fact_alias: fbd
    metric_expr: AVG(fbd.balance)
    metric_alias: avg_bal
    hard_filters: []
    date_column: fbd.dt
    dim_catalog: {}
    filter_catalog: {}
  - id: customer_count
    display_name: 客户数
    aliases: [客户数, 客户数量]
    fact_table: dim_customer
    fact_alias: dc
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    dim_catalog: {}
    filter_catalog: {}
""",
        encoding="utf-8",
    )
    return MetricCatalog.from_yaml(yml)


def _fake_embed(question_vec, alias_vecs_by_text):
    """返 embed_fn(texts)：alias 按 alias_vecs_by_text 映射，question 用 question_vec。"""

    def _embed(texts):
        out = []
        for t in texts:
            if t in alias_vecs_by_text:
                out.append(alias_vecs_by_text[t])
            else:
                out.append(question_vec)
        return out

    return _embed


def test_router_builds_index_on_construct(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    aliases = ["存款余额", "日均存款", "客户数", "客户数量"]
    embed = MagicMock(side_effect=lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    router = MetricRouter(catalog=catalog, embed_fn=embed, threshold=0.7)
    # 构造时应对全部 4 条 aliases 批 embed 一次
    embed.assert_called_once()
    called_texts = embed.call_args[0][0]
    assert set(called_texts) == set(aliases)


def test_router_prefilter_hit_returns_metric_id(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    q_vec = [1.0, 0.0, 0.0]
    alias_vecs = {
        "存款余额": [1.0, 0.0, 0.0],  # cos=1.0
        "日均存款": [0.9, 0.1, 0.0],
        "客户数": [0.0, 1.0, 0.0],
        "客户数量": [0.0, 1.0, 0.0],
    }
    router = MetricRouter(
        catalog=catalog,
        embed_fn=_fake_embed(q_vec, alias_vecs),
        threshold=0.7,
    )
    # mock resolve 下游
    from chat_bi_agent.agents.p1 import metric_resolver as mr
    mr._resolve_to_spec_and_sql = MagicMock(
        return_value=(MetricSpec(metric_id="deposit_balance"), "SELECT 1")
    )
    result = router.try_route("查询存款余额")
    assert result.prefilter_hit is True
    assert result.metric_id == "deposit_balance"
    assert result.cosine == pytest.approx(1.0, abs=1e-6)
    assert result.sql == "SELECT 1"
    assert result.fail_reason is None


def test_router_prefilter_miss_returns_no_hit(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    q_vec = [0.0, 0.0, 1.0]
    alias_vecs = {
        "存款余额": [1.0, 0.0, 0.0],
        "日均存款": [1.0, 0.0, 0.0],
        "客户数": [0.0, 1.0, 0.0],
        "客户数量": [0.0, 1.0, 0.0],
    }
    router = MetricRouter(
        catalog=catalog,
        embed_fn=_fake_embed(q_vec, alias_vecs),
        threshold=0.7,
    )
    result = router.try_route("列出所有支行 ID")
    assert result.prefilter_hit is False
    assert result.metric_id is None
    assert result.sql is None
    assert result.cosine < 0.7  # 全 0 相似


def test_router_threshold_boundary_below(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    # 构造 cosine ≈ 0.69 的问题
    q_vec = [0.69, math.sqrt(1 - 0.69**2), 0.0]
    alias_vecs = {a: [1.0, 0.0, 0.0] for a in ["存款余额", "日均存款", "客户数", "客户数量"]}
    router = MetricRouter(catalog=catalog, embed_fn=_fake_embed(q_vec, alias_vecs), threshold=0.7)
    result = router.try_route("边界测试")
    assert result.prefilter_hit is False
    assert result.cosine == pytest.approx(0.69, abs=1e-3)


def test_router_threshold_boundary_above(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    q_vec = [0.71, math.sqrt(1 - 0.71**2), 0.0]
    alias_vecs = {a: [1.0, 0.0, 0.0] for a in ["存款余额", "日均存款", "客户数", "客户数量"]}
    router = MetricRouter(catalog=catalog, embed_fn=_fake_embed(q_vec, alias_vecs), threshold=0.7)
    from chat_bi_agent.agents.p1 import metric_resolver as mr
    mr._resolve_to_spec_and_sql = MagicMock(
        return_value=(MetricSpec(metric_id="deposit_balance"), "SELECT 1")
    )
    result = router.try_route("边界测试")
    assert result.prefilter_hit is True
    assert result.cosine == pytest.approx(0.71, abs=1e-3)


def test_router_resolve_no_metric_classifies_fail_reason(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    q_vec = [1.0, 0.0, 0.0]
    alias_vecs = {a: [1.0, 0.0, 0.0] for a in ["存款余额", "日均存款", "客户数", "客户数量"]}
    router = MetricRouter(catalog=catalog, embed_fn=_fake_embed(q_vec, alias_vecs), threshold=0.7)
    from chat_bi_agent.agents.p1 import metric_resolver as mr
    mr._resolve_to_spec_and_sql = MagicMock(
        side_effect=MetricResolverError("no metric matched (LLM returned metric_id=null)")
    )
    result = router.try_route("这个问题无 metric")
    assert result.prefilter_hit is True
    assert result.sql is None
    assert result.fail_reason == "no_metric"


def test_router_resolve_enum_error_classifies(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    q_vec = [1.0, 0.0, 0.0]
    alias_vecs = {a: [1.0, 0.0, 0.0] for a in ["存款余额", "日均存款", "客户数", "客户数量"]}
    router = MetricRouter(catalog=catalog, embed_fn=_fake_embed(q_vec, alias_vecs), threshold=0.7)
    from chat_bi_agent.agents.p1 import metric_resolver as mr
    mr._resolve_to_spec_and_sql = MagicMock(
        side_effect=MetricResolverError("bad enum value '不存在' for filter customer_tier; expected ...")
    )
    result = router.try_route("XXX")
    assert result.fail_reason == "enum_out_of_range"


def test_router_resolve_unknown_dim_classifies(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    q_vec = [1.0, 0.0, 0.0]
    alias_vecs = {a: [1.0, 0.0, 0.0] for a in ["存款余额", "日均存款", "客户数", "客户数量"]}
    router = MetricRouter(catalog=catalog, embed_fn=_fake_embed(q_vec, alias_vecs), threshold=0.7)
    from chat_bi_agent.agents.p1 import metric_resolver as mr
    mr._resolve_to_spec_and_sql = MagicMock(
        side_effect=MetricResolverError("unknown dim 'not_a_dim' for metric deposit_balance")
    )
    result = router.try_route("YYY")
    assert result.fail_reason == "unknown_dim"


def test_router_resolve_unsupported_op_classifies(tmp_path):
    catalog = _tiny_catalog(tmp_path)
    q_vec = [1.0, 0.0, 0.0]
    alias_vecs = {a: [1.0, 0.0, 0.0] for a in ["存款余额", "日均存款", "客户数", "客户数量"]}
    router = MetricRouter(catalog=catalog, embed_fn=_fake_embed(q_vec, alias_vecs), threshold=0.7)
    from chat_bi_agent.agents.p1 import metric_resolver as mr
    mr._resolve_to_spec_and_sql = MagicMock(
        side_effect=MetricResolverError("unsupported_op: empty IN for filter customer_tier")
    )
    result = router.try_route("ZZZ")
    assert result.fail_reason == "unsupported_op"
