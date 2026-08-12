"""P1NL2SQLAgent 集成 smoke：真 Postgres + 真 catalog + mock LLM。

前置：docker compose 已起，chatbi-pg healthy。跑前手动确认：
    docker ps | grep chatbi-pg

未配 PG_HOST 时整个模块 skip；也可用 `pytest -m "not integration"` 绕开。
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from chat_bi_agent.agents.p1.metric_resolver import (
    MetricCatalog,
    MetricRouter,
    MetricSpec,
    render_sql_from_spec,
)
from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.shared.sql_executor import SQLExecutor

CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "metrics.yaml"


@pytest.fixture
def real_catalog():
    return MetricCatalog.from_yaml(CATALOG_PATH)


def _make_embed_fn(target_aliases: set[str]):
    """target metric 的 alias 返 [1,0]，其余返 [0,1]；question 也返 [1,0]。

    不靠 argmax 的 tie-break 顺序，prefilter 必须真的挑中 target。
    """

    def fake_embed(texts):
        return [[1.0, 0.0] if t in target_aliases else [0.0, 1.0] for t in texts]

    return fake_embed


@pytest.mark.integration
def test_every_metric_dim_filter_combo_executes_on_real_pg(real_catalog):
    """catalog 全组合真打 PG。

    模板里的列名/类型只有真正 execute 才会被校验——单测拼出 SQL 字符串就算过，
    但列不存在照样 100% executor_fail。2026-08-12 就是这么漏掉 3 处定义错的
    （fbd.account_type / ft.channel / dc.is_active），48 组合里 18 组挂。
    catalog 改完必须跑这个。
    """
    if not os.environ.get("PG_HOST"):
        pytest.skip("PG 未配置")

    executor = SQLExecutor()
    failures: list[str] = []
    checked = 0

    for m in real_catalog.metrics:
        tw = {"start": "2026-05-01", "end": "2026-05-31"} if m.date_column else None

        combos: list[tuple[list[str], list[dict]]] = [([], [])]
        combos += [([dim_id], []) for dim_id in m.dim_catalog]
        for fid, fdef in m.filter_catalog.items():
            if fdef.type == "enum":
                val = fdef.enum_values[0]
            elif fdef.type == "boolean":
                val = True
            elif fdef.type == "numeric":
                val = 0
            else:
                val = "X"
            combos.append(([], [{"col": fid, "op": "=", "val": val}]))

        for dims, filters in combos:
            checked += 1
            spec = MetricSpec(metric_id=m.id, dims=dims, filters=filters, time_window=tw)
            sql = render_sql_from_spec(spec, real_catalog)
            _, err = executor.execute(sql)
            if err is not None:
                failures.append(f"{m.id} dims={dims} filters={filters}: {err.splitlines()[0]}")

    assert checked >= 40, f"组合数骤降到 {checked}，catalog 可能被截断"
    assert not failures, "以下 metric 组合在真实 schema 上跑不通：\n" + "\n".join(failures)


@pytest.mark.integration
def test_metric_hit_produces_rows_from_real_pg(real_catalog):
    """deposit_balance 命中 → 拼真 SQL → 跑 Postgres 应返 rows。"""
    if not os.environ.get("PG_HOST"):
        pytest.skip("PG 未配置")

    deposit = next(m for m in real_catalog.metrics if m.id == "deposit_balance")
    router = MetricRouter(
        real_catalog,
        embed_fn=_make_embed_fn(set(deposit.aliases)),
        threshold=0.7,
    )

    # seed 数据覆盖 2025-01-01..2026-09-30，取窗内一天
    fake_spec = MetricSpec(
        metric_id="deposit_balance",
        dims=[],
        filters=[],
        time_window={"start": "2026-05-01", "end": "2026-05-01"},
    )
    # SchemaLoader.build_index() 会真打 DashScope embedding，但 metric 命中路径
    # 根本不碰 schema index——这里 stub 掉，免得测试被无关的网络抖动搞成 flaky。
    # SQLValidator / SQLExecutor 保持真实，真 Postgres 才是这个 smoke 的目的。
    with (
        patch(
            "chat_bi_agent.schema.loader.qwen_client.embed",
            side_effect=lambda texts: [[1.0, 0.0] for _ in texts],
        ),
        patch("chat_bi_agent.agents.p1.metric_resolver._resolve_to_spec_and_sql") as mock_r,
    ):
        mock_r.return_value = (fake_spec, render_sql_from_spec(fake_spec, real_catalog))

        agent = P1NL2SQLAgent(metric_router=router)
        result = agent.run(question_id="smoke1", question="存款余额是多少")

    assert result.route == "metric"
    assert result.metric_id == "deposit_balance"
    assert result.execution_error is None
    assert result.rows is not None
    assert result.metric_spec["metric_id"] == "deposit_balance"
    # 命中路径不进 Reflect Loop
    assert result.attempts == 1
    assert result.reflect_history == []
