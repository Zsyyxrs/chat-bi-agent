"""P2 evaluator 的 insight_accuracy 维度：中文洞察匹配。

2026-08-15 排查发现的缺陷——原实现是 `exp_insight_val.split()[:5]`，对中文按空格切：

- **纯中文洞察永不命中**：「识别出春节是季节性高峰而非异常」split 后只有 1 个 token
  （整句），要求它逐字出现在回答里才算命中。评测集 31 条 insight 里有 10 条是这样。
- **中英混合洞察白送分**：「2 月 15-23 日现金支取量增加约 25%」split 后首个 token 是
  `'2'`，`'2' in response` 对任何含数字 2 的回答都为真。这类有 21 条。

两个方向同时错，而 insight_accuracy 占 P2 combined_score 的 25%（权重最高的一维）。
失效是静默的：分数照常产出，看不出它没在测洞察。

修法是复用 P3 早就有的 jieba 分词（见 eval/zh_tokenize.py），改成内容词召回率。
"""

import pytest

from chat_bi_agent.eval.multi_step_analysis_evaluator import MultiStepAnalysisEvaluator
from chat_bi_agent.eval.zh_tokenize import overlap_ratio, tokenize_zh


class TestTokenizer:
    def test_chinese_is_actually_segmented(self):
        """核心：中文必须切成多个词，而不是整句一个 token。"""
        tokens = tokenize_zh("识别出春节是季节性高峰而非异常")
        assert len(tokens) > 1
        assert "春节" in tokens

    def test_stopwords_removed(self):
        assert "的" not in tokenize_zh("春节的现金支取")

    def test_empty_is_safe(self):
        assert tokenize_zh("") == set()
        assert overlap_ratio("", "任意回答") == 0.0


class TestOverlapRatio:
    def test_pure_chinese_insight_can_now_match(self):
        """回归用例：这正是旧实现永远给 0 的那一类。"""
        expected = "识别出春节是季节性高峰而非异常"
        response = "从数据看，春节期间的高峰属于季节性波动，并非异常事件。"
        assert overlap_ratio(expected, response) > 0.5

    def test_unrelated_response_scores_low(self):
        expected = "识别出春节是季节性高峰而非异常"
        response = "贷款余额在七月底出现小幅上升，主要由对公业务拉动。"
        assert overlap_ratio(expected, response) < 0.3

    def test_single_digit_no_longer_grants_full_credit(self):
        """回归用例：旧实现下首 token '2' 会让任何含 2 的回答满命中。"""
        expected = "2 月 15-23 日现金支取量增加约 25%"
        response = "本次分析共涉及 2 张表，未发现明显变化。"
        assert overlap_ratio(expected, response) < 0.5

    def test_ratio_is_recall_not_jaccard(self):
        """长叙述不应稀释得分——expected 的词都出现了就该接近满分。"""
        expected = "现金支取量增加"
        response = "（前略很长的分析）……现金支取量增加明显……（后略很长的分析）" + "补充说明。" * 50
        assert overlap_ratio(expected, response) > 0.9


class TestEvaluatorIntegration:
    @pytest.fixture
    def ev(self):
        return MultiStepAnalysisEvaluator(use_llm_judge=False)

    def test_insight_accuracy_rewards_stating_the_insight(self, ev):
        """健全性检查：原文照抄应当高分（旧实现也过，不用于区分新旧）。"""
        q = ev.get_question("multi_step_q001")
        assert q is not None, "评测集缺 multi_step_q001"
        from chat_bi_agent.eval.multi_step_analysis_evaluator import _yaml_listdict_to_dict

        insights = _yaml_listdict_to_dict(q.get("expected_insights"))
        stated = "。".join(str(v) for v in insights.values())
        score = ev.evaluate_response("multi_step_q001", stated, [], [])
        assert score.insight_accuracy > 0.8

    def test_paraphrased_chinese_insight_gets_credit(self, ev):
        """区分用例（旧实现必红）。

        q001 的 seasonality_detection 是纯中文，旧实现要求整句
        「识别出春节是季节性高峰而非异常」逐字出现才算命中，改述一律 0 分。
        本回答刻意避开旧实现会误命中的 token（'2' / '月' / 'ATM' / '和'）,
        因此旧实现得 0.0，新实现应当认出这是同一个洞察。
        """
        response = "春节期间的支取高峰属于季节性波动，并非异常事件。"
        score = ev.evaluate_response("multi_step_q001", response, [], [])
        assert score.insight_accuracy > 0.15

    def test_irrelevant_answer_does_not_get_free_credit(self, ev):
        """区分用例（旧实现必红）。

        旧实现把 channel_shift 切成 ['ATM', '和', 'COUNTER', ...]，只要回答里
        出现一个 '和' 就算命中该洞察（1/4 = 0.25）。这段回答与题目无关却含 '和'。
        """
        response = "本次分析涉及存款和贷款两张表，未发现值得注意的变化。"
        score = ev.evaluate_response("multi_step_q001", response, [], [])
        assert score.insight_accuracy < 0.1

    def test_insight_accuracy_punishes_empty_answer(self, ev):
        score = ev.evaluate_response("multi_step_q001", "无法回答。", [], [])
        assert score.insight_accuracy < 0.3
