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
    MetricRouter(catalog=catalog, embed_fn=embed, threshold=0.7)
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
        side_effect=MetricResolverError(
            "bad enum value '不存在' for filter customer_tier; expected ..."
        )
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


def test_default_threshold_is_tuned_value(tmp_path):
    """默认阈值 0.63 是 34 题标尺上实测选出来的，不是拍脑袋的 0.7。

    实测（qwen3.7-max，commit 5da64da）：t=0.63 路由 precision 1.000、recall 0.75、
    F1 0.857，走语义层的 15 题里 2 题更好 13 题持平 0 题更差；
    t=0.70 recall 只有 0.45。改这个默认值前先重跑 A/B。
    """
    import inspect

    from chat_bi_agent.agents.p1.metric_resolver import MetricRouter

    assert inspect.signature(MetricRouter.__init__).parameters["threshold"].default == 0.63


# ---------------------- top-k 候选裁剪 ----------------------


def _k_catalog(tmp_path, n: int) -> MetricCatalog:
    """n 个指标，每个一条 alias（alias_i / metric_i），方便按 cosine 排序断言。"""
    body = "\n".join(
        f"""  - id: metric_{i}
    display_name: 指标{i}
    aliases: [alias_{i}]
    fact_table: t{i}
    fact_alias: a{i}
    metric_expr: COUNT(*)
    metric_alias: cnt
    hard_filters: []
    dim_catalog: {{}}
    filter_catalog: {{}}"""
        for i in range(n)
    )
    yml = tmp_path / f"k{n}.yaml"
    yml.write_text(f"version: 1\nmetrics:\n{body}\n", encoding="utf-8")
    return MetricCatalog.from_yaml(yml)


def _graded_embed(n: int):
    """alias_i 的 cosine 随 i 递减：alias_0 最像问题，alias_{n-1} 最不像。"""

    def _embed(texts):
        out = []
        for t in texts:
            if t.startswith("alias_"):
                i = int(t.split("_")[1])
                angle = (i / n) * (math.pi / 2)
                out.append([math.cos(angle), math.sin(angle)])
            else:
                out.append([1.0, 0.0])  # 问题向量
        return out

    return _embed


def test_prompt_lists_only_candidate_metrics(tmp_path):
    """candidate_ids 给定时，prompt 只描述这几个指标——其余不进上下文。"""
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    catalog = _tiny_catalog(tmp_path)
    prompt = _build_extractor_prompt(catalog, candidate_ids=["deposit_balance"])
    assert "deposit_balance" in prompt
    assert "customer_count" not in prompt


def test_prompt_lists_all_metrics_when_no_candidates_given(tmp_path):
    """不传 candidate_ids 时行为不变：整个目录都进 prompt。"""
    from chat_bi_agent.agents.p1.metric_resolver import _build_extractor_prompt

    catalog = _tiny_catalog(tmp_path)
    prompt = _build_extractor_prompt(catalog)
    assert "deposit_balance" in prompt
    assert "customer_count" in prompt


def test_router_passes_top_k_candidates_ranked_by_cosine(tmp_path):
    """router 只把 cosine 最高的 k 个指标交给 resolver，按相似度降序。"""
    n = 10
    catalog = _k_catalog(tmp_path, n)
    spy = MagicMock(return_value=(MetricSpec(metric_id="metric_0"), "SELECT 1"))
    router = MetricRouter(catalog=catalog, embed_fn=_graded_embed(n), threshold=0.5, top_k=3)
    _mr_module._resolve_to_spec_and_sql = spy
    router.try_route("问题")

    assert spy.call_args.kwargs["candidate_ids"] == ["metric_0", "metric_1", "metric_2"]


def test_router_top_k_larger_than_catalog_passes_every_metric(tmp_path):
    catalog = _k_catalog(tmp_path, 3)
    spy = MagicMock(return_value=(MetricSpec(metric_id="metric_0"), "SELECT 1"))
    router = MetricRouter(catalog=catalog, embed_fn=_graded_embed(3), threshold=0.5, top_k=99)
    _mr_module._resolve_to_spec_and_sql = spy
    router.try_route("问题")

    assert set(spy.call_args.kwargs["candidate_ids"]) == {"metric_0", "metric_1", "metric_2"}


def test_router_candidate_dedupes_metric_with_multiple_aliases(tmp_path):
    """一个指标多条 alias 时只应占一个候选位，不能挤掉别的指标。"""
    catalog = _tiny_catalog(tmp_path)  # 两个指标，各 2 条 alias
    spy = MagicMock(return_value=(MetricSpec(metric_id="deposit_balance"), "SELECT 1"))
    embed = _fake_embed(
        [1.0, 0.0],
        {
            "存款余额": [1.0, 0.0],
            "日均存款": [0.99, 0.14],
            "客户数": [0.7, 0.71],
            "客户数量": [0.69, 0.72],
        },
    )
    router = MetricRouter(catalog=catalog, embed_fn=embed, threshold=0.5, top_k=2)
    _mr_module._resolve_to_spec_and_sql = spy
    router.try_route("存款余额多少")

    assert spy.call_args.kwargs["candidate_ids"] == ["deposit_balance", "customer_count"]


def test_router_default_top_k_is_set(tmp_path):
    catalog = _k_catalog(tmp_path, 3)
    router = MetricRouter(catalog=catalog, embed_fn=_graded_embed(3))
    assert router.top_k == 8
