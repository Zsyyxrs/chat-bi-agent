"""P2 每一步必须有独立 span，元数据不能互相覆写。

2026-09-02：`_tag_step_span` 最初直接调在 `run` 的循环体里，而 `run` 才是被
@observe 装饰的那个 span——没有 per-step span，`update_current_span` 每轮都在
覆写 `p2_analysis_run` 自身的 metadata，只有最后一步的值留得下来。
P3 的 `_execute_single_drill` 有自己的 @observe，所以那边没这个问题。

这里断言编排契约：每步走一次独立的 `_run_step`，step_index 各不相同。
真实 span 嵌套由 Langfuse e2e 另行验证（同 P3 的做法）。
"""

from unittest.mock import patch

from chat_bi_agent.agents.p2.p2_analysis_agent import P2MultiStepAnalysisAgent
from tests.p2.test_p2_analysis_agent import (
    _QWEN_CHAT,
    FACTS_RESP,
    INSIGHTS_RESP,
    PLAN_2_STEPS,
    REPORT_RESP,
    _make_agent_with_mocks,
    _mk_p1_result,
    _mock_chat,
)


def test_每步走独立的_run_step_且_step_index_不重复(monkeypatch):
    agent, mock_p1 = _make_agent_with_mocks()
    mock_p1.run.side_effect = [
        _mk_p1_result(ok=True, sql="SQL1", rows=[{"v": 100}]),
        _mk_p1_result(ok=True, sql="SQL2", rows=[{"v": 125}]),
    ]

    seen: list[dict] = []
    orig = P2MultiStepAnalysisAgent._run_step

    def spy(self, sub_qid, enriched, **kw):
        seen.append(kw)
        return orig(self, sub_qid, enriched, **kw)

    monkeypatch.setattr(P2MultiStepAnalysisAgent, "_run_step", spy)

    llm = [_mock_chat(PLAN_2_STEPS), _mock_chat(FACTS_RESP),
           _mock_chat(INSIGHTS_RESP), _mock_chat(REPORT_RESP)]
    with patch(_QWEN_CHAT, side_effect=llm):
        agent.run(question_id="q", question="春节对比")

    assert len(seen) == 2, "两步计划应触发两次独立的 step span"
    # 覆写缺陷的判据：step_index 会塌成同一个值 / 只剩最后一条
    assert [k["step_index"] for k in seen] == [0, 1]
    assert {k["total_steps"] for k in seen} == {2}
    assert [k["step_id"] for k in seen] == ["step1", "step2"]
