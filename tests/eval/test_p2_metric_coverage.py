"""P2 evaluator 的 multi_metric_coverage 维度：关键指标是否被提到。

2026-08-17 排查发现的缺陷——原实现：

    sum(1 for metric in key_metrics if any(m in agent_response for m in metric))

`metric` 是字符串（"增长" / "客户" / "金额"），`for m in metric` **迭代的是单个字符**，
判据退化成「该指标名里任意一个字出现过吗」。'长' 在中文里几乎无处不在（长期、
董事长、长度…），'户' 同理，于是这一维在每道题上恒等于 1.000，占着 20% 权重却不
提供任何区分度。作者本意几乎肯定是 `metric in agent_response`。

与 insight_accuracy 那处（ADR-015）是同一类：静默失效——分数照常产出，看不出它
没在测东西。
"""

import pytest

from chat_bi_agent.eval.multi_step_analysis_evaluator import MultiStepAnalysisEvaluator


@pytest.fixture
def ev():
    return MultiStepAnalysisEvaluator(use_llm_judge=False)


def test_key_metrics_are_whole_terms_not_characters(ev):
    """区分用例（旧实现必红）。

    q001 的 key_metrics 含「增长」「客户」等。这段回答刻意只包含这些词的**单个字**
    （"长期"含'长'、"账户"含'户'），但一个完整指标词都没出现。
    旧实现按字符匹配 → 满分；新实现应当接近 0。
    """
    response = "本次分析着眼于长期表现，并核对了账户层面的明细，未见异常。"
    score = ev.evaluate_response("multi_step_q001", response, [], [])
    assert score.multi_metric_coverage < 0.5


def test_stating_the_metrics_still_scores(ev):
    """健全性：真正提到指标词时应当得分。"""
    response = "现金支取金额显著增长，涉及的客户数量同步上升，资金流向以柜面为主。"
    score = ev.evaluate_response("multi_step_q001", response, [], [])
    assert score.multi_metric_coverage > 0.5


def test_empty_response_scores_zero(ev):
    score = ev.evaluate_response("multi_step_q001", "", [], [])
    assert score.multi_metric_coverage == 0.0
