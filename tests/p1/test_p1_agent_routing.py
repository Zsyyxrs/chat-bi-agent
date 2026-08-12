"""P1NL2SQLAgent 的语义层前置路由与 fallback 行为。"""

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from chat_bi_agent.agents.p1.metric_resolver import MetricSpec, RouteResult
from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.shared.sql_executor import SQLErrorClass


def _make_agent(metric_router=None):
    """构造 agent，patch 掉 schema loader 与 embedding 索引构建。"""
    with patch("chat_bi_agent.agents.p1.nl2sql_agent.SchemaLoader") as mock_loader_cls, \
         patch("chat_bi_agent.agents.p1.nl2sql_agent.SchemaLinker") as mock_linker_cls, \
         patch("chat_bi_agent.agents.p1.nl2sql_agent.SQLGenerator"), \
         patch("chat_bi_agent.agents.p1.nl2sql_agent.SQLValidator") as mock_val_cls, \
         patch("chat_bi_agent.agents.p1.nl2sql_agent.SQLExecutor") as mock_exec_cls, \
         patch("chat_bi_agent.agents.p1.nl2sql_agent.Reflector"):
        mock_loader = MagicMock()
        mock_loader.get_ddl_text = MagicMock(return_value="DDL")
        mock_loader_cls.return_value = mock_loader

        mock_linker = MagicMock()
        mock_linker.link = MagicMock(return_value=[MagicMock(name="t1")])
        mock_linker_cls.return_value = mock_linker

        mock_validator = MagicMock()
        mock_validator.validate = MagicMock(return_value=MagicMock(ok=True, error=None))
        mock_val_cls.return_value = mock_validator

        mock_exec = MagicMock()
        mock_exec.execute = MagicMock(return_value=([{"n": 1}], None))
        mock_exec_cls.return_value = mock_exec

        agent = P1NL2SQLAgent(metric_router=metric_router)
        agent._mocks = {  # 便于测试断言
            "linker": mock_linker,
            "validator": mock_validator,
            "executor": mock_exec,
        }
        return agent


def test_run_without_router_is_bytewise_equivalent():
    """metric_router=None 时不走路由分支，SchemaLinker 一定被调，route='nl2sql'。"""
    agent = _make_agent(metric_router=None)
    with patch.object(agent.sql_generator, "generate") as mock_gen:
        mock_gen.return_value = MagicMock(sql="SELECT 1", thought="t")
        result = agent.run(question_id="q1", question="随便")
    assert result.route == "nl2sql"
    assert result.metric_id is None
    assert result.prefilter_cosine is None
    assert result.metric_spec is None
    assert result.metric_fail_reason is None
    agent._mocks["linker"].link.assert_called_once()


def test_run_prefilter_miss_falls_through_to_nl2sql():
    """prefilter miss → route='nl2sql', 但 prefilter_cosine 记录 max cosine。"""
    router = MagicMock()
    router.try_route.return_value = RouteResult(
        prefilter_hit=False, metric_id=None, cosine=0.42,
        sql=None, spec=None, fail_reason=None,
    )
    agent = _make_agent(metric_router=router)
    with patch.object(agent.sql_generator, "generate") as mock_gen:
        mock_gen.return_value = MagicMock(sql="SELECT 1", thought="t")
        result = agent.run(question_id="q1", question="列出支行 ID")
    assert result.route == "nl2sql"
    assert result.prefilter_cosine == pytest.approx(0.42)
    agent._mocks["linker"].link.assert_called_once()  # fallback 路径仍走 linker


def test_run_route_metric_success_skips_reflect_loop():
    """prefilter hit + resolve/validator/executor 全 OK → route='metric'，不进 SchemaLinker。"""
    spec = MetricSpec(metric_id="deposit_balance")
    router = MagicMock()
    router.try_route.return_value = RouteResult(
        prefilter_hit=True, metric_id="deposit_balance", cosine=0.85,
        sql="SELECT AVG(balance) FROM fct_balance_daily", spec=spec, fail_reason=None,
    )
    agent = _make_agent(metric_router=router)
    result = agent.run(question_id="q1", question="存款余额")
    assert result.route == "metric"
    assert result.metric_id == "deposit_balance"
    assert result.prefilter_cosine == pytest.approx(0.85)
    assert result.metric_spec == asdict(spec)
    assert result.metric_fail_reason is None
    assert result.rows == [{"n": 1}]
    assert result.attempts == 1
    agent._mocks["linker"].link.assert_not_called()  # 关键：命中路径不 linker


def test_run_route_metric_then_nl2sql_on_resolve_no_metric():
    router = MagicMock()
    router.try_route.return_value = RouteResult(
        prefilter_hit=True, metric_id="deposit_balance", cosine=0.85,
        sql=None, spec=None, fail_reason="no_metric",
    )
    agent = _make_agent(metric_router=router)
    with patch.object(agent.sql_generator, "generate") as mock_gen:
        mock_gen.return_value = MagicMock(sql="SELECT 1", thought="t")
        result = agent.run(question_id="q1", question="XXX")
    assert result.route == "metric_then_nl2sql"
    assert result.metric_id == "deposit_balance"
    assert result.metric_fail_reason == "no_metric"
    agent._mocks["linker"].link.assert_called_once()


def test_run_route_metric_then_nl2sql_on_validator_fail():
    spec = MetricSpec(metric_id="deposit_balance")
    router = MagicMock()
    router.try_route.return_value = RouteResult(
        prefilter_hit=True, metric_id="deposit_balance", cosine=0.85,
        sql="BAD SQL", spec=spec, fail_reason=None,
    )
    agent = _make_agent(metric_router=router)
    agent._mocks["validator"].validate.return_value = MagicMock(ok=False, error="syntax")
    with patch.object(agent.sql_generator, "generate") as mock_gen:
        mock_gen.return_value = MagicMock(sql="SELECT 1", thought="t")
        result = agent.run(question_id="q1", question="XX")
    assert result.route == "metric_then_nl2sql"
    assert result.metric_fail_reason == "validator_fail"


def test_run_route_metric_then_nl2sql_on_executor_fail():
    spec = MetricSpec(metric_id="deposit_balance")
    router = MagicMock()
    router.try_route.return_value = RouteResult(
        prefilter_hit=True, metric_id="deposit_balance", cosine=0.85,
        sql="SELECT * FROM t", spec=spec, fail_reason=None,
    )
    agent = _make_agent(metric_router=router)
    agent._mocks["executor"].execute.return_value = (None, "relation does not exist")
    agent._mocks["executor"].classify_error.return_value = SQLErrorClass.OTHER
    with patch.object(agent.sql_generator, "generate") as mock_gen:
        mock_gen.return_value = MagicMock(sql="SELECT 1", thought="t")
        # 让 executor 在 fallback 后成功
        agent._mocks["executor"].execute.side_effect = [
            (None, "relation does not exist"),
            ([{"n": 1}], None),
        ]
        result = agent.run(question_id="q1", question="XX")
    assert result.route == "metric_then_nl2sql"
    assert result.metric_fail_reason == "executor_fail"


def test_run_route_metric_success_spec_serialized_to_dict():
    spec = MetricSpec(
        metric_id="deposit_balance",
        dims=["branch_name"],
        filters=[{"col": "customer_tier", "op": "=", "val": "HIGH_NET_WORTH"}],
        time_window={"start": "2026-05-01", "end": "2026-05-31"},
    )
    router = MagicMock()
    router.try_route.return_value = RouteResult(
        prefilter_hit=True, metric_id="deposit_balance", cosine=0.9,
        sql="SELECT 1", spec=spec, fail_reason=None,
    )
    agent = _make_agent(metric_router=router)
    result = agent.run(question_id="q1", question="X")
    assert result.metric_spec == asdict(spec)
    assert result.metric_spec["metric_id"] == "deposit_balance"
    assert result.metric_spec["filters"][0]["val"] == "HIGH_NET_WORTH"


def test_tag_trace_includes_route_metadata_when_metric_hit():
    from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
    with patch("chat_bi_agent.agents.p1.nl2sql_agent.get_client") as mock_gc:
        mock_client = MagicMock()
        mock_gc.return_value = mock_client
        P1NL2SQLAgent._tag_trace(
            reflect_history=[],
            error_class=None,
            retrieved_example_ids=[],
            route="metric",
            metric_id="deposit_balance",
            prefilter_cosine=0.85,
            metric_fail_reason=None,
        )
        mock_client.update_current_trace.assert_called_once()
        md = mock_client.update_current_trace.call_args.kwargs["metadata"]
        assert md["route"] == "metric"
        assert md["metric_id"] == "deposit_balance"
        assert md["prefilter_cosine"] == pytest.approx(0.85)
        assert md["metric_fail_reason"] is None
