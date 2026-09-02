"""下钻维度来自 metric catalog，而不是全局硬编码白名单。

原实现：`select_drilldown_dims` 的 `available_dims` 调用方不传 → 退到全局
DEFAULT_DIMS（6 个维度，21 个指标共用一套）。LLM 于是在一个与当前指标无关的
集合里挑维度，挑中该指标根本没声明的维度时，下钻 SQL 只能靠 P1 自由发挥。

改为：fact_anchor 命中语义层时把 metric_id 带出来，用该指标 dim_catalog 里
声明过的维度作为候选集。裁错了是「没有合适维度」，不是编一个不存在的维度出来
——与 P1 的 candidate_ids 裁剪同一思路。

metric_id 拿不到时（走 nl2sql 路径 / 未挂 router）保持原行为，fail-open。
"""

from pathlib import Path
from types import SimpleNamespace

from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog
from chat_bi_agent.agents.p3.drilldown_selector import catalog_dims

CATALOG = MetricCatalog.from_yaml(Path("config/metrics.yaml"))


def _router() -> SimpleNamespace:
    return SimpleNamespace(catalog=CATALOG)


def test_返回该指标_dim_catalog_里声明的维度():
    dims = catalog_dims(_router(), "deposit_balance")
    assert dims is not None
    assert set(dims) == set(CATALOG.get("deposit_balance").dim_catalog)
    assert "branch_id" in dims


def test_不同指标拿到不同维度集():
    ids = [m.id for m in CATALOG.metrics]
    a, b = ids[0], ids[-1]
    if set(CATALOG.get(a).dim_catalog) == set(CATALOG.get(b).dim_catalog):
        return  # 两个指标维度恰好相同则本断言无意义
    assert catalog_dims(_router(), a) != catalog_dims(_router(), b)


def test_metric_id_为空时返回None_退回默认白名单():
    assert catalog_dims(_router(), None) is None


def test_router_缺失时返回None():
    assert catalog_dims(None, "deposit_balance") is None


def test_未知_metric_id_返回None_而不是抛错():
    """归因链路不该因为 catalog 查不到就整体崩掉。"""
    assert catalog_dims(_router(), "no_such_metric") is None


def test_P3_把_catalog_维度传给选择器_而非全局默认(fake_events_dir, monkeypatch):
    """接线守门：catalog_dims 单测再绿，调用方没接上也是白搭。"""
    from dataclasses import replace

    from chat_bi_agent.agents.p3 import p3_rca_agent as mod
    from chat_bi_agent.agents.p3.drilldown_selector import DEFAULT_DIMS
    from chat_bi_agent.agents.p3.types import DrillRequest

    seen: dict = {}

    def spy(*, question, fact_anchor, llm_client, available_dims=None):
        seen["dims"] = available_dims
        return [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]

    monkeypatch.setattr(mod, "select_drilldown_dims", spy)

    anchor = SimpleNamespace(
        metric_name="存款余额", time_window="2026-05", current_value=1.0,
        prior_value=2.0, change_pct=-0.5, direction="down",
        sql="SELECT 1", rows=[{"v": 1}], metric_id="deposit_balance",
    )
    monkeypatch.setattr(mod, "run_fact_anchor", lambda **kw: anchor)
    monkeypatch.setattr(mod, "run_drill_down", lambda **kw: [])
    monkeypatch.setattr(mod, "match_events", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "synthesize", lambda **kw: ("叙述", "结论"))

    p1 = SimpleNamespace(metric_router=_router())
    agent = mod.P3RootCauseAnalysisAgent(
        p1_agent=p1, llm_client=SimpleNamespace(), events_dir=fake_events_dir
    )
    agent.run(question_id="q", question="存款为什么降了")

    expected = set(CATALOG.get("deposit_balance").dim_catalog)
    assert seen["dims"] is not None, "调用方没把 catalog 维度传下去"
    assert set(seen["dims"]) == expected
    assert set(seen["dims"]) != set(DEFAULT_DIMS)
