"""P1 生产接线的**唯一**定义：few-shot 池、语义层路由、身份名单。

为什么单独一个模块——2026-09-08 加 MCP server 时，这些常量与构造函数原本长在
`streamlit_app/tabs/p1_nl2sql.py` 里。MCP 若自己抄一份，就成了第二份真值，而本项目
在「双写必然漂移」上已经吃过两次亏（`run_all_evals.py` 的 pattern 没跟着 runner 改名、
`metrics.yaml` 在容器里静默关闭）。两次的共同点都是**不报错**，只是两边不一致。
`tests/p1/test_p1_wiring_shared.py` 用同一性断言把这条钉住。

注意这里刻意**不含任何 UI / transport 代码**：Streamlit 拿它去填 session_state，
MCP server 拿它去建进程内的单例，两边各自管各自的生命周期。
"""

from pathlib import Path

from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog, MetricRouter
from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent
from chat_bi_agent.agents.shared.example_retriever import ExamplePool, ExampleRetriever
from chat_bi_agent.agents.shared.sql_executor import SQLExecutor
from chat_bi_agent.llm import qwen_client

# 仓库根：<root>/src/chat_bi_agent/agents/p1/wiring.py → parents[4]
# 容器内同构（Dockerfile 的 WORKDIR /app 下有 src/ 与 config/）。
REPO_ROOT = Path(__file__).resolve().parents[4]

# 生产 pool 路径。bootstrap_prod_pool.py 落这个文件；反馈闭环夜间任务往它追加。
PROD_POOL_PATH = REPO_ROOT / "data" / "example_pool_prod.jsonl"

# 生产用同域池：min_sim 0.7 比 BIRD 跨库的 0.55 严格（宁缺毋滥），
# k=3 上限；池子小的时候大部分调用会返回空 list，行为等价 few-shot off。
PROD_MIN_SIM = 0.7
PROD_TOP_K = 3

# 语义层指标目录。命中即走 governed 模板 SQL（结果可复现、口径可审计），
# 未命中回退原 NL2SQL——2026-08-13 A/B 判定绿灯，见 DESIGN_DECISIONS.md#adr-013。
METRICS_CATALOG_PATH = REPO_ROOT / "config" / "metrics.yaml"


# ---- 身份（RLAC，ADR-017）----
# 语义层的 row_policies 在渲染期绑这几个属性；LLM 全程看不到它们。
# 演示用固定名单：真实系统里 session 属性来自 SSO/会话，不该让用户自己挑。
# 总行用 branch_scope=ALL 显式越界，而不是靠「不传属性」绕过——不传属性是
# fail-closed 拒绝，不是放行。
HQ_IDENTITY = "总行（可见全行）"
IDENTITIES: dict[str, dict[str, object]] = {
    HQ_IDENTITY: {"branch_scope": "ALL", "branch_id": "__unused_by_hq__"},
    "杭州分行客户经理（BR_CITY_0000）": {
        "branch_scope": "BRANCH",
        "branch_id": "BR_CITY_0000",
    },
    "南京分行客户经理（BR_CITY_0002）": {
        "branch_scope": "BRANCH",
        "branch_id": "BR_CITY_0002",
    },
    "苏州分行客户经理（BR_CITY_0003）": {
        "branch_scope": "BRANCH",
        "branch_id": "BR_CITY_0003",
    },
}


def session_props_for(label: str) -> dict[str, object]:
    """身份 → session 属性。认不出的身份返回空——空即 fail-closed 拒绝，不是放行。"""
    return dict(IDENTITIES.get(label, {}))


def build_retriever_if_available() -> ExampleRetriever | None:
    """池子文件存在且非空才挂 retriever；否则完全跳过（不影响 P1 原有行为）。"""
    # is_file 而非 exists：目录也满足 exists()，随后 ExamplePool.load 会抛
    # IsADirectoryError，把「少一个增强项」升级成「P1 整个不可用」。
    if not PROD_POOL_PATH.is_file():
        return None
    pool = ExamplePool.load(PROD_POOL_PATH)
    if len(pool) == 0:
        return None
    return ExampleRetriever(
        pool=pool,
        dialect="postgres",
        embed_fn=qwen_client.embed,
        min_similarity=PROD_MIN_SIM,
        max_k=PROD_TOP_K,
    )


def build_metric_router_if_available() -> MetricRouter | None:
    """目录存在才挂路由；任何失败都降级成 None。

    语义层是增强项，不能把主路径带崩：构造要 embed 全部 alias（当前 86 条），
    DashScope 抖动或目录写坏时，宁可少一个增强项也不能让整个 P1 入口不可用。
    阈值用 MetricRouter 的默认值（0.63，34 题标尺实测选出），不在这里硬编码。
    """
    if not METRICS_CATALOG_PATH.exists():
        return None
    try:
        catalog = MetricCatalog.from_yaml(METRICS_CATALOG_PATH)
        if not catalog.metrics:
            return None
        return MetricRouter(
            catalog=catalog,
            embed_fn=qwen_client.embed,
            # probe_fn 必须给：string filter 塞错值时 SQL 依然合法，
            # 只会静默返回空结果——这是唯一能拦住它的闸
            probe_fn=SQLExecutor().execute,
        )
    except Exception:
        return None


def build_p1_agent() -> tuple[P1NL2SQLAgent, ExampleRetriever | None, MetricRouter | None]:
    """按生产接线造 P1 agent，并把两个增强件一并交回给调用方。

    调用方需要 retriever/router 本身：Streamlit 要拿池子大小和 catalog 做展示，
    MCP server 要拿 catalog 列受治理指标。返回而不是让调用方再造一遍——再造一遍
    就是把 embed 全部 alias 的开销和漂移风险又付一次。
    """
    retriever = build_retriever_if_available()
    router = build_metric_router_if_available()
    agent = P1NL2SQLAgent(
        top_k=4,
        example_retriever=retriever,
        metric_router=router,
        # 每次 run 自成一条 root trace（不像评测批次那样嵌套在 p1_eval_batch 下），
        # 打 route tag 是安全的。必须打：Langfuse 的 metrics 聚合层不认 metadata
        # （按 metadata.route 分组返回 400），不打 tag 就画不出 metric_hit_rate。
        tag_route_on_trace=True,
    )
    return agent, retriever, router
