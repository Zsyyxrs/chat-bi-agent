"""P2 Multi-step Analysis Evaluator: assess analytical reasoning of Analysis Agent."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


def _yaml_listdict_to_dict(value) -> dict:
    """YAML 里 analysis_steps / expected_insights 都是 list of single-key dict
    （如 [{"step1": "..."}, {"step2": "..."}]）。把它平铺成 dict 以便迭代。
    若已是 dict 或空，原样/空 dict 返回。
    """
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        merged: dict = {}
        for item in value:
            if isinstance(item, dict):
                merged.update(item)
        return merged
    return {}


@dataclass
class AnalysisScore:
    """单个多步分析问题的评估分数。"""

    question_id: str
    step_completeness: float = 0.0  # 0-1: 完成了多少个必要步骤
    multi_metric_coverage: float = 0.0  # 0-1: 是否覆盖了多个关键指标
    insight_accuracy: float = 0.0  # 0-1: 发现的洞察与期望的相似度
    reasoning_quality: float = 0.0  # 0-1: 推理逻辑的严谨性
    business_relevance: float = 0.0  # 0-1: 结论与业务意义的相关性
    response_time_seconds: float = 0.0

    @property
    def combined_score(self) -> float:
        """综合评分：0-1，权重分配。"""
        weights = {
            "step_completeness": 0.2,  # 是否完成了必要步骤
            "multi_metric": 0.2,  # 是否覆盖了多个指标
            "insight_accuracy": 0.25,  # 最重要：洞察准确度
            "reasoning_quality": 0.2,  # 推理逻辑
            "business_relevance": 0.15,  # 业务相关性
        }

        score = (
            self.step_completeness * weights["step_completeness"]
            + self.multi_metric_coverage * weights["multi_metric"]
            + self.insight_accuracy * weights["insight_accuracy"]
            + self.reasoning_quality * weights["reasoning_quality"]
            + self.business_relevance * weights["business_relevance"]
        )

        return max(0.0, score)


@dataclass
class AnalysisEvaluation:
    """完整评估结果集。"""

    total_questions: int = 0
    scores: list[AnalysisScore] = field(default_factory=list)
    passed_questions: int = 0  # combined_score >= 0.7

    @property
    def pass_rate(self) -> float:
        """通过率（>= 0.7 分为通过）。"""
        if self.total_questions == 0:
            return 0.0
        return self.passed_questions / self.total_questions

    @property
    def avg_score(self) -> float:
        """平均分数。"""
        if not self.scores:
            return 0.0
        return sum(s.combined_score for s in self.scores) / len(self.scores)

    def summary(self) -> str:
        """生成评估摘要。"""
        return f"""
P2 Multi-step Analysis Evaluation Summary
==========================================
Total Questions: {self.total_questions}
Passed (>= 0.7): {self.passed_questions}
Pass Rate: {self.pass_rate:.1%}
Average Score: {self.avg_score:.3f}

Details:
--------
"""


class MultiStepAnalysisEvaluator:
    """
    P2 (Multi-step Analysis) Analysis Agent 评估器。

    工作流程：
    1. 加载 multi_step_analysis_evaluation.yaml 中的问题
    2. 对每个问题运行 Analysis Agent（多步骤推理）
    3. 解析 Agent 的回答，提取：
       - 完成的分析步骤数
       - 提到的关键指标（AUM、续作率、增长率等）
       - 得出的业务洞察
       - 推理的严谨性
    4. 与期望答案对比，计算各子维度分数
    5. 聚合为最终评分
    """

    def __init__(self):
        self.eval_dir = Path(__file__).parent.parent / "data"
        self.questions = self._load_evaluation_questions()

    def _load_evaluation_questions(self) -> list[dict]:
        """从 YAML 加载评估问题。"""
        eval_file = self.eval_dir / "multi_step_analysis_evaluation.yaml"
        if not eval_file.exists():
            return []

        with open(eval_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return data.get("evaluation_questions", [])

    def get_question(self, question_id: str) -> Optional[dict]:
        """按 ID 获取单个问题。"""
        for q in self.questions:
            if q.get("id") == question_id:
                return q
        return None

    def evaluate_response(
        self,
        question_id: str,
        agent_response: str,
        mentioned_steps: list[str] = None,
        mentioned_metrics: list[str] = None,
        extracted_insights: list[str] = None,
    ) -> AnalysisScore:
        """
        评估 Agent 对单个问题的回答。

        Args:
            question_id: 问题 ID
            agent_response: Agent 的原始回答
            mentioned_steps: Agent 在回答中提到的分析步骤
            mentioned_metrics: Agent 提到的关键指标
            extracted_insights: 从回答中提取的业务洞察

        Returns:
            AnalysisScore: 评分对象
        """
        question = self.get_question(question_id)
        if not question:
            raise ValueError(f"Question {question_id} not found")

        mentioned_steps = mentioned_steps or []
        mentioned_metrics = mentioned_metrics or []
        extracted_insights = extracted_insights or []

        score = AnalysisScore(question_id=question_id)

        # 1. 步骤完整性 (step_completeness)
        expected_steps = _yaml_listdict_to_dict(question.get("analysis_steps"))
        if expected_steps:
            step_count = len([s for s in mentioned_steps if s])
            expected_step_count = len(expected_steps)
            score.step_completeness = min(1.0, step_count / max(1, expected_step_count))

        # 2. 多指标覆盖度 (multi_metric_coverage)
        # 简化实现：检查回答中是否提到了多个关键指标
        key_metrics = self._extract_key_metrics(question)
        if key_metrics:
            mentioned_key_metrics = sum(
                1 for metric in key_metrics if any(m in agent_response for m in metric)
            )
            score.multi_metric_coverage = min(
                1.0, mentioned_key_metrics / max(1, len(key_metrics))
            )

        # 3. 洞察准确度 (insight_accuracy)
        expected_insights = _yaml_listdict_to_dict(question.get("expected_insights"))
        if expected_insights:
            matched_insights = 0
            for exp_insight_key, exp_insight_val in expected_insights.items():
                # 检查是否提到了关键洞察的关键词
                if isinstance(exp_insight_val, str):
                    keywords = exp_insight_val.split()[:5]  # 前 5 个关键词
                    if any(kw in agent_response for kw in keywords):
                        matched_insights += 1
                elif isinstance(exp_insight_val, list):
                    if any(item in agent_response for item in exp_insight_val):
                        matched_insights += 1

            score.insight_accuracy = min(
                1.0, matched_insights / max(1, len(expected_insights))
            )

        # 4. 推理质量 (reasoning_quality)
        # 评估是否包含因果关系、对比、条件推理等
        reasoning_patterns = ["因此", "所以", "由于", "导致", "由...引起", "对比", "相比", "相反"]
        reasoning_count = sum(1 for pattern in reasoning_patterns if pattern in agent_response)
        score.reasoning_quality = min(1.0, reasoning_count / max(1, 4))  # 期望 4+ 个推理模式

        # 5. 业务相关性 (business_relevance)
        # 检查是否提到了具体的业务指标和行动建议
        business_terms = [
            "客户",
            "分行",
            "产品",
            "风险",
            "收益",
            "流入",
            "流出",
            "AUM",
            "转化",
            "损失",
        ]
        business_relevance_count = sum(1 for term in business_terms if term in agent_response)
        score.business_relevance = min(1.0, business_relevance_count / max(1, 5))

        return score

    def _extract_key_metrics(self, question: dict) -> list[str]:
        """从问题的期望洞察中提取关键指标。"""
        metrics = []
        expected_insights = _yaml_listdict_to_dict(question.get("expected_insights"))
        for key in expected_insights.keys():
            # 将 key 转换为可搜索的指标关键词
            if "rate" in key or "比例" in key or "率" in key:
                metrics.append("率")
            if "growth" in key or "增长" in key:
                metrics.append("增长")
            if "volume" in key or "金额" in key or "数量" in key:
                metrics.append("金额")
            if "customer" in key or "客户" in key:
                metrics.append("客户")
            if "flow" in key or "流" in key:
                metrics.append("流")

        return list(set(metrics))

    def evaluate_batch(self, results: list[dict]) -> AnalysisEvaluation:
        """
        批量评估多个问题的回答。

        Args:
            results: 包含 question_id, agent_response 等的结果列表

        Returns:
            AnalysisEvaluation: 完整评估结果
        """
        evaluation = AnalysisEvaluation(total_questions=len(results))

        for result in results:
            score = self.evaluate_response(
                question_id=result.get("question_id"),
                agent_response=result.get("agent_response", ""),
                mentioned_steps=result.get("mentioned_steps"),
                mentioned_metrics=result.get("mentioned_metrics"),
                extracted_insights=result.get("extracted_insights"),
            )
            evaluation.scores.append(score)

            if score.combined_score >= 0.7:
                evaluation.passed_questions += 1

        return evaluation
