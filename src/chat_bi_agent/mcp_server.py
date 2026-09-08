"""MCP server：把 P1 精准取数暴露给 Claude Desktop 等 MCP 客户端。

**只暴露 P1，且身份由服务端配置锁定。** 两条都是设计，不是省事：

1. P2/P3 不暴露。两者都委托 P1 执行，但调用时不传 session_props
   （`p2_analysis_agent.py:85`、`p3/drill_executor.py:164`）——子问题一旦没命中语义层、
   走 NL2SQL 兜底，就没有任何行级管控。那是个已知缺口，MCP 不把它摆进新接口。
2. 身份从环境变量读，**tool schema 里没有身份字段**。让被管的人自己声明自己是谁，
   权限边界就是假的。这与项目既有约定一脉相承：session 属性只参与渲染、不进 prompt
   （`metric_resolver.py`），LLM 全程看不到它。

fail-closed 沿用 ADR-017：没配环境变量 = 空 session_props = 声明了 `row_policies`
的指标在渲染期被拒。**空不是"总行视角"**——总行要用 `CHATBI_MCP_BRANCH_SCOPE=ALL`
显式越界，和 Streamlit 侧的身份下拉完全一致。

拒绝之后不回退：P1 返回 `route="metric_denied"` 时本模块原样报拒绝。2026-09-04
实测过回退的后果——同一个问题，总行 31992 / 杭州 808，而无身份走 NL2SQL 拿到
13618，第三个数是绕开行级权限裸查出来的。

跑法（stdio）：
    CHATBI_MCP_BRANCH_SCOPE=BRANCH CHATBI_MCP_BRANCH_ID=BR_CITY_0000 \
        python -m chat_bi_agent.mcp_server
"""

import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from chat_bi_agent.agents.p1 import wiring
from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog

# 单次返回给模型的最大行数。截断只砍 rows，`row_count` 仍报真值——
# 报截断后的行数等于让模型基于一个错的总量下判断。
MAX_ROWS = 200

SCOPE_ENV = "CHATBI_MCP_BRANCH_SCOPE"
BRANCH_ENV = "CHATBI_MCP_BRANCH_ID"

# 锚到仓库根的**绝对**路径，不用裸 load_dotenv()。裸调用靠调用处/cwd 往上找，
# 而 Claude Desktop 拉起本进程时 cwd 由它决定——找不到 .env 的后果是 PG_PORT
# 退回默认 5432（本项目是 5433）、DASHSCOPE_API_KEY 为空，表现成「连不上库」
# 而不是「没配环境」，排查方向会被带偏。
DOTENV_PATH: Path = wiring.REPO_ROOT / ".env"


def session_props_from_env(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """服务端配置 → session 属性。任何配不全/配不对的情况一律返回空 = 拒绝。

    返回空绝不等于放行：下游 `render_sql_from_spec` 对声明了 row_policies 的指标
    会 fail-closed 抛错，P1 据此返回 metric_denied。
    """
    env = os.environ if env is None else env
    scope = (env.get(SCOPE_ENV) or "").strip().upper()

    if scope == "ALL":
        # 总行的属性取自共享名单，不在这里另写一份字面量——否则 HQ 的语义
        # 会在 Streamlit 与 MCP 两个入口之间漂移。
        return dict(wiring.IDENTITIES[wiring.HQ_IDENTITY])

    if scope == "BRANCH":
        branch_id = (env.get(BRANCH_ENV) or "").strip()
        if not branch_id:
            # 配了 BRANCH 却没给分行，属于配错。此时必须拒绝——
            # 「没指定分行」绝不能被解释成「所有分行都能看」。
            return {}
        return {"branch_scope": "BRANCH", "branch_id": branch_id}

    return {}


def shape_result(result: Any) -> dict[str, Any]:
    """P1AgentResult → 给模型的返回体。截断在这里，拒绝语义也在这里。"""
    route = getattr(result, "route", "nl2sql")
    if route == "metric_denied":
        return {
            "denied": True,
            "sql": None,
            "rows": None,
            "row_count": None,
            "truncated": False,
            "route": route,
            "metric_id": getattr(result, "metric_id", None),
            "error": getattr(result, "execution_error", None)
            or "当前身份缺少该指标所需的行级权限属性，已拒绝查询",
        }

    rows = getattr(result, "rows", None)
    if rows is None:
        return {
            "denied": False,
            "sql": getattr(result, "sql", None),
            "rows": None,
            "row_count": None,
            "truncated": False,
            "route": route,
            "metric_id": getattr(result, "metric_id", None),
            "error": getattr(result, "execution_error", None) or "查询未返回结果",
        }

    return {
        "denied": False,
        "sql": getattr(result, "sql", None),
        "rows": rows[:MAX_ROWS],
        "row_count": len(rows),
        "truncated": len(rows) > MAX_ROWS,
        "route": route,
        "metric_id": getattr(result, "metric_id", None),
        "error": None,
    }


def describe_metrics(catalog: MetricCatalog | None) -> list[dict[str, Any]]:
    """列出受治理指标。目录没加载成功时返回空表而不是报错——
    语义层是增强项，它缺席时 MCP 仍应能取数，只是列不出指标。"""
    if catalog is None or not getattr(catalog, "metrics", None):
        return []
    return [
        {
            "metric_id": m.id,
            "display_name": m.display_name,
            "aliases": list(m.aliases),
            "row_policy_protected": bool(m.row_policies),
        }
        for m in catalog.metrics
    ]


def create_server(
    agent: Any,
    catalog: MetricCatalog | None,
    session_props: dict[str, object],
) -> FastMCP:
    """注册两个 tool 并返回 server。身份在闭包里，**不出现在任何 tool 的入参中**。"""
    server = FastMCP(
        name="chat-bi-agent",
        instructions=(
            "银行对话式 BI 的精准取数能力（P1）。用自然语言提问即可，"
            "查询身份由服务端配置锁定、不可在对话中指定；"
            "命中受治理指标时走固定口径的模板 SQL，未命中回退 NL2SQL。"
        ),
    )

    @server.tool()
    def query_bank_data(question: str) -> dict[str, Any]:
        """用自然语言查询银行数据，返回 SQL 与结果行。

        返回体里 route=metric 表示命中受治理指标（口径固定、结果可复现）；
        denied=true 表示当前服务端身份无权查询该指标——这是拒绝，不会换一条
        没有行级管控的路径把同一个数给出来。
        """
        result = agent.run(
            question_id=f"mcp_{uuid.uuid4().hex[:8]}",
            question=question,
            session_props=session_props,
        )
        return shape_result(result)

    @server.tool()
    def list_governed_metrics() -> list[dict[str, Any]]:
        """列出语义层里受治理的指标：id、业务名、别名、是否受行级权限管控。

        先看这张表再提问，命中模板 SQL 的概率更高，口径也更可靠。
        """
        return describe_metrics(catalog)

    return server


def main() -> None:
    """stdio 入口。身份在启动时定死，进程存续期间不变。"""
    load_dotenv(DOTENV_PATH)
    session_props = session_props_from_env()
    agent, _retriever, router = wiring.build_p1_agent()
    catalog = router.catalog if router is not None else None
    create_server(agent=agent, catalog=catalog, session_props=session_props).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
