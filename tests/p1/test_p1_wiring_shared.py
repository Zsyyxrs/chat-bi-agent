"""P1 生产接线只有一份定义，Streamlit 与 MCP server 共用。

为什么需要这组守门——本项目已经在「双写必然漂移」上吃过两次亏：
`run_all_evals.py` 的 pattern 没跟着 runner 改名（「P1 6 题 1.000」在一键报告里
活了两个月）、`metrics.yaml` 在容器里静默关闭。P1 的生产接线（few-shot 池路径、
指标目录路径、min_similarity、top_k、身份名单）如果 Streamlit 一份、MCP 一份，
下一次漂移只是时间问题，且同样不会报错——只会让两个入口对同一个问题给出不同的数。

因此这里断言的是**同一性**（is），不是「值相等」：值相等挡不住有人复制一份再各改各的。
"""

from chat_bi_agent.agents.p1 import wiring
from streamlit_app.tabs import p1_nl2sql


def test_streamlit_metric_router_builder_is_the_shared_one():
    assert p1_nl2sql._build_metric_router_if_available is wiring.build_metric_router_if_available


def test_streamlit_retriever_builder_is_the_shared_one():
    assert p1_nl2sql._build_retriever_if_available is wiring.build_retriever_if_available


def test_streamlit_identity_roster_is_the_shared_one():
    """身份名单必须同源：MCP 用环境变量选身份，选的得是同一张表里的取值。"""
    assert p1_nl2sql.IDENTITIES is wiring.IDENTITIES
    # HQ_IDENTITY 只是 IDENTITIES 的一个键，UI 从不单独引用它，
    # 所以不要求 tab 再导出一遍——它的家在 wiring。
    assert wiring.HQ_IDENTITY in wiring.IDENTITIES


def test_session_props_helper_is_the_shared_one():
    assert p1_nl2sql._session_props_for is wiring.session_props_for
