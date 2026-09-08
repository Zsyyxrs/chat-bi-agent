"""MCP server 暴露 P1 取数能力时的权限边界与返回契约。

设计前提（2026-09-08 定）：
- **只暴露 P1**。P2/P3 委托 P1 时不传 session_props（p2_analysis_agent.py:85、
  drill_executor.py:164），子问题一旦走 NL2SQL 兜底就没有行级管控。MCP 不把这个
  已知缺口摆进新接口。
- **身份来自服务端配置，模型不可见**。tool schema 里没有身份字段——让被管的人
  自己声明自己是谁，权限边界就是假的。
- **fail-closed**。环境变量没配 = 空 session_props = 受管控指标被拒，不是放行。
  这与 ADR-017 和 Streamlit 侧「不传属性是拒绝、总行要用 branch_scope=ALL 显式越界」
  的约定完全一致。

本文件全部用假 agent 打桩：不打真 LLM、不连库。
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from chat_bi_agent import mcp_server
from chat_bi_agent.agents.p1 import wiring

# ---------- 身份：从服务端环境变量来，且 fail-closed ----------


def test_no_env_yields_empty_props_which_means_refusal():
    """没配身份不是"总行视角"，是没有身份——受管控指标会被渲染期拒绝。"""
    assert mcp_server.session_props_from_env({}) == {}


def test_branch_scope_carries_the_configured_branch():
    props = mcp_server.session_props_from_env(
        {"CHATBI_MCP_BRANCH_SCOPE": "BRANCH", "CHATBI_MCP_BRANCH_ID": "BR_CITY_0000"}
    )
    assert props == {"branch_scope": "BRANCH", "branch_id": "BR_CITY_0000"}


def test_branch_scope_without_branch_id_is_refused_not_widened():
    """配了 BRANCH 却没给 branch_id，属于配错。此时必须拒绝，
    绝不能因为"没指定分行"就悄悄放成全行可见。"""
    assert mcp_server.session_props_from_env({"CHATBI_MCP_BRANCH_SCOPE": "BRANCH"}) == {}


def test_hq_scope_reuses_the_shared_roster_definition():
    """总行的属性取自 wiring.IDENTITIES，不在 MCP 里另写一份字面量——
    否则 HQ 的语义会在两个入口之间漂移。"""
    props = mcp_server.session_props_from_env({"CHATBI_MCP_BRANCH_SCOPE": "ALL"})
    assert props == wiring.IDENTITIES[wiring.HQ_IDENTITY]


def test_unknown_scope_is_refused():
    assert mcp_server.session_props_from_env({"CHATBI_MCP_BRANCH_SCOPE": "SUPERUSER"}) == {}


def test_env_derived_props_satisfy_every_row_policy_requirement():
    """与 tests/streamlit/test_p1_rlac_identity.py 同一条守门：
    少给一个属性就是渲染期 fail-closed，表现为静默降级。"""
    from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog

    cat = MetricCatalog.from_yaml(wiring.METRICS_CATALOG_PATH)
    required = {name for m in cat.metrics for pol in m.row_policies for name in pol.requires}
    assert required, "catalog 里没有 row_policies，这条用例失去意义"

    for env in (
        {"CHATBI_MCP_BRANCH_SCOPE": "ALL"},
        {"CHATBI_MCP_BRANCH_SCOPE": "BRANCH", "CHATBI_MCP_BRANCH_ID": "BR_CITY_0000"},
    ):
        props = mcp_server.session_props_from_env(env)
        assert not (required - set(props)), f"{env} 派生的属性缺 {required - set(props)}"


# ---------- 返回契约 ----------


@dataclass
class _FakeResult:
    sql: str | None = "SELECT 1"
    rows: list[dict] | None = field(default_factory=lambda: [{"n": 1}])
    execution_error: str | None = None
    route: str = "nl2sql"
    metric_id: str | None = None
    error_class: object | None = None


def test_denied_route_is_reported_as_refusal_with_no_data():
    """行级权限拒绝必须原样透传成拒绝：不给 SQL、不给数、也不换条路重答。
    2026-09-04 实测过回退的后果——无身份走 NL2SQL 拿到 13618 这个裸查出来的数。"""
    out = mcp_server.shape_result(
        _FakeResult(sql=None, rows=None, route="metric_denied", execution_error="当前身份缺少…")
    )
    assert out["denied"] is True
    assert out["sql"] is None
    assert out["rows"] is None
    assert "权限" in out["error"] or "身份" in out["error"]


def test_rows_are_truncated_but_row_count_stays_truthful():
    """截断是为了不撑爆 Claude Desktop 的上下文，但行数必须报真值——
    报截断后的行数等于让模型基于一个错的总量做判断。"""
    rows = [{"i": i} for i in range(mcp_server.MAX_ROWS + 1)]
    out = mcp_server.shape_result(_FakeResult(rows=rows))
    assert len(out["rows"]) == mcp_server.MAX_ROWS
    assert out["row_count"] == mcp_server.MAX_ROWS + 1
    assert out["truncated"] is True


def test_small_result_is_not_marked_truncated():
    out = mcp_server.shape_result(_FakeResult(rows=[{"i": 1}, {"i": 2}]))
    assert out["truncated"] is False
    assert out["row_count"] == 2


def test_execution_error_is_returned_not_raised():
    out = mcp_server.shape_result(
        _FakeResult(rows=None, execution_error="relation does not exist")
    )
    assert out["denied"] is False
    assert "relation does not exist" in out["error"]


def test_metric_route_is_surfaced_so_the_caller_knows_it_was_governed():
    out = mcp_server.shape_result(_FakeResult(route="metric", metric_id="deposit_balance"))
    assert out["route"] == "metric"
    assert out["metric_id"] == "deposit_balance"


# ---------- tool 表面：身份不可由模型声明 ----------


def test_query_tool_exposes_only_the_question_argument():
    """整个设计的重心：schema 里出现任何身份字段，权限边界就是假的。"""
    server = mcp_server.create_server(agent=object(), catalog=None, session_props={})
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    props = tools["query_bank_data"].inputSchema["properties"]
    assert set(props) == {"question"}


def test_server_exposes_exactly_the_two_designed_tools():
    """P2/P3 不在暴露面内——它们没有身份通道。"""
    server = mcp_server.create_server(agent=object(), catalog=None, session_props={})
    assert {t.name for t in asyncio.run(server.list_tools())} == {
        "query_bank_data",
        "list_governed_metrics",
    }


def test_governed_metrics_listing_flags_row_policy_protection():
    """列指标时要标出哪些受行级管控——这是语义层在 MCP 上的可见化。"""
    from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog

    cat = MetricCatalog.from_yaml(wiring.METRICS_CATALOG_PATH)
    listing = mcp_server.describe_metrics(cat)
    assert listing, "catalog 非空时不该列出空表"
    by_id = {m["metric_id"]: m for m in listing}
    for m in cat.metrics:
        assert by_id[m.id]["display_name"] == m.display_name
        assert by_id[m.id]["row_policy_protected"] is bool(m.row_policies)


def test_describe_metrics_on_missing_catalog_is_empty_not_an_error():
    """语义层是增强项：目录没加载成功时 MCP 仍要能取数，只是列不出指标。"""
    assert mcp_server.describe_metrics(None) == []


# ---------- 只读保证 ----------


def test_module_never_reads_the_read_write_db_user():
    """MCP 不新增写路径：连接凭据只经 SQLExecutor 的只读用户。"""
    src = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert "PG_USER" not in src
    assert "PG_PASSWORD" not in src


# ---------- 启动环境：Claude Desktop 以裸子进程拉起，cwd 不可控 ----------


def test_dotenv_path_is_anchored_to_repo_root_not_cwd():
    """裸 load_dotenv() 依赖调用处/cwd 去往上找 .env，而 Claude Desktop 启动本进程时
    cwd 是它自己定的。必须锚到仓库根的绝对路径，否则 PG_PORT（本项目是 5433 而非
    默认 5432）和 DASHSCOPE_API_KEY 都读不到——表现为"连不上库"，而不是"没配环境"。"""
    assert mcp_server.DOTENV_PATH == wiring.REPO_ROOT / ".env"
    assert mcp_server.DOTENV_PATH.is_absolute()


def test_main_loads_env_from_that_path_regardless_of_cwd(tmp_path, monkeypatch):
    env_file = tmp_path / "dotenv" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("CHATBI_MCP_DOTENV_PROBE=loaded\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.delenv("CHATBI_MCP_DOTENV_PROBE", raising=False)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(mcp_server, "DOTENV_PATH", env_file)
    monkeypatch.setattr(mcp_server.wiring, "build_p1_agent", lambda: (object(), None, None))

    class _Server:
        def run(self, transport):  # noqa: ARG002
            pass

    monkeypatch.setattr(mcp_server, "create_server", lambda **kwargs: _Server())

    mcp_server.main()

    import os as _os

    assert _os.environ["CHATBI_MCP_DOTENV_PROBE"] == "loaded"
