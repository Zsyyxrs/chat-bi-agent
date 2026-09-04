"""三个 tab 的「能问什么」引导块 + 示例问题预填 + 反馈按钮语义。

为什么放进 app 而不是写一份 docs/用户手册.md：这些问题（该问什么、要等多久、
👍/👎 点了会怎样）全都是**用的当下**才想知道的，切出去翻文档没人会做。

示例问题一律取自 `src/chat_bi_agent/data/*_evaluation.yaml`——那是跑过 ground truth
的题面。手册里给一条答不对的示例，比不给示例更糟。
"""

import ast

import pytest

from tests.ast_probe import REPO_ROOT

TAB_SOURCES = {
    "p1": "streamlit_app/tabs/p1_nl2sql.py",
    "p2": "streamlit_app/tabs/p2_analysis.py",
    "p3": "streamlit_app/tabs/p3_rca.py",
}
_FEEDBACK_SRC = "streamlit_app/components/feedback_block.py"


def _calls_named(source_path: str, name: str) -> list[ast.Call]:
    """名字（属性名或函数名）等于 name 的真实调用节点，注释与字符串不命中。"""
    tree = ast.parse((REPO_ROOT / source_path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        label = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if label == name:
            out.append(node)
    return out


def _kwarg(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name:
            return ast.unparse(kw.value)
    return None


# ---- 引导内容 ----


def test_every_tab_has_a_guide():
    from streamlit_app.components.guide_block import TAB_GUIDES

    assert set(TAB_GUIDES) == set(TAB_SOURCES)


@pytest.mark.parametrize("tab_key", sorted(TAB_SOURCES))
def test_guide_says_both_what_it_is_for_and_what_to_use_instead(tab_key):
    """只说「适合什么」等于没说——用户走错 tab 时需要被指到对的那个。"""
    from streamlit_app.components.guide_block import TAB_GUIDES

    guide = TAB_GUIDES[tab_key]
    assert guide.good_at.strip()
    assert guide.not_for.strip()
    others = {k.upper() for k in TAB_SOURCES if k != tab_key}
    assert any(o in guide.not_for for o in others), (
        f"{tab_key} 的 not_for 没指向任何另一个 tab，用户还是不知道该去哪"
    )


@pytest.mark.parametrize("tab_key", sorted(TAB_SOURCES))
def test_examples_come_from_the_evaluated_question_sets(tab_key):
    """示例必须是评测跑过的题面，不能是现编的——现编的答不对就砸招牌。"""
    import yaml

    from streamlit_app.components.guide_block import TAB_GUIDES

    eval_files = {
        "p1": "precision_retrieval_evaluation.yaml",
        "p2": "multi_step_analysis_evaluation.yaml",
        "p3": "attribution_evaluation.yaml",
    }
    path = REPO_ROOT / "src" / "chat_bi_agent" / "data" / eval_files[tab_key]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else next(v for v in raw.values() if isinstance(v, list))
    # 归一化：题集里是带换行的块标量，UI 上要单行
    known = {" ".join((it.get("question") or "").split()) for it in items}

    examples = TAB_GUIDES[tab_key].examples
    assert len(examples) >= 2, "至少给两条，一条示例撑不起「可以这么问」"
    for ex in examples:
        assert " ".join(ex.split()) in known, f"{tab_key} 示例不在评测题集里：{ex!r}"


def test_examples_are_not_shared_between_tabs():
    from streamlit_app.components.guide_block import TAB_GUIDES

    seen: dict[str, str] = {}
    for tab_key, guide in TAB_GUIDES.items():
        for ex in guide.examples:
            assert ex not in seen, f"{ex!r} 同时出现在 {seen[ex]} 和 {tab_key}"
            seen[ex] = tab_key


# ---- 预填 ----


def test_prefill_overwrites_the_question_box():
    """点示例是明确意图，直接覆盖输入框，不做「已有内容就不动」的猜测。"""
    from streamlit_app.components.guide_block import prefill_question

    state = {"p1_question_input": "用户自己打了一半的问题"}
    prefill_question(state, "p1_question_input", "示例问题")
    assert state["p1_question_input"] == "示例问题"


def test_prefill_works_on_an_empty_state():
    from streamlit_app.components.guide_block import prefill_question

    state: dict = {}
    prefill_question(state, "p2_question_input", "示例问题")
    assert state["p2_question_input"] == "示例问题"


# ---- 接线（AST，注释里写了不算）----


@pytest.mark.parametrize("tab_key,src", sorted(TAB_SOURCES.items()))
def test_guide_block_renders_before_the_question_box(tab_key, src):
    """Streamlit 不允许 widget 实例化后再改它的 session_state。

    引导块要在 text_area **之前**渲染，点示例的赋值才生效；顺序反了会抛
    StreamlitAPIException，而且只在点按钮时才炸——单元测试之外抓不到。
    """
    guides = _calls_named(src, "render_guide_block")
    boxes = _calls_named(src, "text_area")
    assert guides, f"{src} 没渲染引导块"
    assert boxes, f"{src} 没有 text_area，这条用例失去意义"
    assert min(g.lineno for g in guides) < min(b.lineno for b in boxes), (
        f"{src} 的 render_guide_block 在 text_area 之后，示例预填会在运行时抛异常"
    )


@pytest.mark.parametrize("tab_key,src", sorted(TAB_SOURCES.items()))
def test_guide_block_targets_the_question_box_it_sits_above(tab_key, src):
    """预填的 key 必须就是 text_area 的 key——写错了按钮点了没反应，静默失效。"""
    guide_call = _calls_named(src, "render_guide_block")[0]
    box_call = _calls_named(src, "text_area")[0]
    assert _kwarg(guide_call, "input_key") == _kwarg(box_call, "key")


@pytest.mark.parametrize("tab_key,src", sorted(TAB_SOURCES.items()))
def test_guide_and_feedback_agree_on_the_tab_key(tab_key, src):
    """同一个 tab 在两处用同一个 key，改名时不会只改一半。"""
    guide_call = _calls_named(src, "render_guide_block")[0]
    fb_call = _calls_named(src, "render_feedback_block")[0]
    guide_key = _kwarg(guide_call, "tab_key") or ast.unparse(guide_call.args[0])
    assert guide_key == _kwarg(fb_call, "tab_key") == f"'{tab_key}'"


# ---- 反馈按钮语义 ----


def test_feedback_buttons_say_where_the_vote_goes():
    """👍 进 example pool、👎 进回归集——这语义原本只写在 docstring 里，点的人看不到。"""
    buttons = _calls_named(_FEEDBACK_SRC, "button")
    helps = [h for h in (_kwarg(b, "help") for b in buttons) if h]
    assert len(helps) == 2, f"两个反馈按钮都要有 help=，实际 {len(helps)} 个"
    joined = "".join(helps)
    assert "池" in joined or "pool" in joined
    assert "回归" in joined


# ---- 耗时说明 ----


@pytest.mark.parametrize(
    "src,stale",
    [
        ("streamlit_app/tabs/p2_analysis.py", "10-30s"),
        ("streamlit_app/tabs/p3_rca.py", "20-40s"),
    ],
)
def test_spinner_does_not_quote_a_stale_latency(src, stale):
    """实测中位 P2 491s / P3 481s（results/baseline_*_2026-06 系列）。

    原文案差一个数量级，用户会以为卡死了去刷页面。这是反向断言：
    这个字面量不该出现。
    """
    text = (REPO_ROOT / src).read_text(encoding="utf-8")
    assert stale not in text, f"{src} 仍在承诺 {stale}，实测是它的十几倍"


# ---- 示例必须有 baseline 证据，且不能是题集里偏弱的那几条 ----
#
# 2026-09-04 走查发现：原先只校验「题面出自评测题集」，这只保证题目合法，
# 不保证 agent 答得对。当时 P1 的第一条示例是 precision_q005——全集最低分
# （0.8167，均分 0.965），实跑漏掉了 gold 要求的 account_type='SAVING' 过滤，
# 报出的是全部账户类型的合计。另一条 precision_q009 干脆不在任何 baseline 里。
# 把「验证过」从口号变成断言。

_BASELINES = {
    "p1": "results/baseline_p1_eval_2026-08-15.json",
    "p2": "results/baseline_p2_analysis_2026-06-07.json",
    "p3": "results/baseline_p3_rca_2026-06-29.json",
}
_EVAL_FILES = {
    "p1": "precision_retrieval_evaluation.yaml",
    "p2": "multi_step_analysis_evaluation.yaml",
    "p3": "attribution_evaluation.yaml",
}


def _baseline_scores(tab_key: str) -> dict[str, float]:
    import json

    data = json.loads((REPO_ROOT / _BASELINES[tab_key]).read_text(encoding="utf-8"))
    return {r["question_id"]: r["score"] for r in data["per_question"]}


def _question_to_id(tab_key: str) -> dict[str, str]:
    import yaml

    path = REPO_ROOT / "src" / "chat_bi_agent" / "data" / _EVAL_FILES[tab_key]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else next(v for v in raw.values() if isinstance(v, list))
    return {" ".join((it.get("question") or "").split()): it["id"] for it in items}


@pytest.mark.parametrize("tab_key", sorted(TAB_SOURCES))
def test_every_example_has_baseline_evidence(tab_key):
    """题面出自题集还不够——必须有 baseline 真跑过它并留下分数。"""
    from streamlit_app.components.guide_block import TAB_GUIDES

    scores = _baseline_scores(tab_key)
    by_question = _question_to_id(tab_key)
    for ex in TAB_GUIDES[tab_key].examples:
        qid = by_question[" ".join(ex.split())]
        assert qid in scores, (
            f"{tab_key} 示例 {qid} 不在 {_BASELINES[tab_key]} 里——没有任何证据说明它答得对"
        )


@pytest.mark.parametrize("tab_key", sorted(TAB_SOURCES))
def test_examples_are_not_below_the_median_question(tab_key):
    """别把偏弱的题放进橱窗。用中位数而非固定阈值，三条路径共用一把尺。"""
    import statistics

    from streamlit_app.components.guide_block import TAB_GUIDES

    scores = _baseline_scores(tab_key)
    median = statistics.median(scores.values())
    by_question = _question_to_id(tab_key)
    for ex in TAB_GUIDES[tab_key].examples:
        qid = by_question[" ".join(ex.split())]
        assert scores[qid] >= median, (
            f"{tab_key} 示例 {qid} 得分 {scores[qid]} 低于该题集中位数 {median}"
        )


# ---- 真跑 Streamlit：点按钮是否真的填进输入框 ----
#
# 上面的 AST 断言只能保证「渲染顺序对」，保证不了 Streamlit 的 widget/rerun
# 机制真的把值送进 text_area。这条用 AppTest 跑真实脚本，是唯一能证明按钮
# 有用的测试——不跑它就只能靠人手点浏览器。


@pytest.mark.parametrize("tab_key", sorted(TAB_SOURCES))
def test_clicking_an_example_fills_the_question_box(tab_key):
    from streamlit.testing.v1 import AppTest

    from streamlit_app.components.guide_block import TAB_GUIDES

    expected = TAB_GUIDES[tab_key].examples[0]
    at = AppTest.from_file(str(REPO_ROOT / "streamlit_app" / "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception

    at = at.button(key=f"{tab_key}_example_0").click().run()
    assert not at.exception, at.exception
    assert at.session_state[f"{tab_key}_question_input"] == expected
    # 光看 session_state 不够：要确认渲染出来的输入框真带上了这个值
    assert at.text_area(key=f"{tab_key}_question_input").value == expected
