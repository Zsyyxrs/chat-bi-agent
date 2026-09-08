"""P1 tab 的身份选择 → session_props → 语义层行级权限（RLAC）。

RLAC 在 2026-09-02 上线时是「实现完整、零使用」：没有调用方传 session_props。
这个文件锁住 UI 这一侧的接线——身份选出来了，就必须真的传下去。
"""

from tests.ast_probe import call_keywords


def test_hq_identity_sees_every_branch():
    """总行身份用 branch_scope=ALL 越过分行边界，而不是靠不传属性绕过。"""
    from chat_bi_agent.agents.p1 import wiring
    from streamlit_app.tabs import p1_nl2sql

    props = p1_nl2sql._session_props_for(wiring.HQ_IDENTITY)
    assert props["branch_scope"] == "ALL"


def test_branch_identity_carries_its_own_branch_id():
    from chat_bi_agent.agents.p1 import wiring
    from streamlit_app.tabs import p1_nl2sql

    label = next(k for k in p1_nl2sql.IDENTITIES if k != wiring.HQ_IDENTITY)
    props = p1_nl2sql._session_props_for(label)
    assert props["branch_scope"] == "BRANCH"
    assert props["branch_id"].startswith("BR_")


def test_every_identity_supplies_all_props_the_catalog_requires():
    """少给一个属性就是 fail-closed 拒绝——渲染期才炸，UI 上表现为静默降级。"""
    from chat_bi_agent.agents.p1 import wiring
    from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog
    from streamlit_app.tabs import p1_nl2sql

    cat = MetricCatalog.from_yaml(wiring.METRICS_CATALOG_PATH)
    required = {name for m in cat.metrics for pol in m.row_policies for name in pol.requires}
    assert required, "catalog 里没有 row_policies，这条用例失去意义"
    for label in p1_nl2sql.IDENTITIES:
        missing = required - set(p1_nl2sql._session_props_for(label))
        assert not missing, f"身份 {label!r} 缺少 session 属性 {missing}"


def test_agent_run_call_site_passes_session_props():
    """在 AST 上断言构造点，注释里写了不算。"""
    kws = call_keywords("streamlit_app/tabs/p1_nl2sql.py")
    assert any(name == "session_props" for name, _ in kws), (
        "P1 tab 调 agent.run 时没传 session_props——身份选了也白选"
    )
