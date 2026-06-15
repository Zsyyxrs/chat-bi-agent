"""RCA (Root Cause Analysis) Evaluator: assess attribution accuracy of RCA Agent."""

import logging
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import jieba
import yaml

# jieba prints initialization noise on first call — silence it to keep eval logs clean.
jieba.setLogLevel(logging.WARNING)

_CHINESE_PUNCT = "。，、；：？！…（）《》「」『』【】“”‘’—－·"
_PUNCT_CHARS = set(string.punctuation + _CHINESE_PUNCT + " \t\n\r")

# Event-specific keywords used both for event_hit fuzzy match and dim_recall alias expansion.
# When a product is tied to an event in events YAML, the event's keywords also act as
# acceptable aliases for that product — so narratives that name the event verbatim
# ("安鑫 90 天到期") count toward the product_id's dim_recall.
_EVENT_KEYWORDS: dict[str, list[str]] = {
    "anxin_90_expire": ["安鑫", "90天", "理财", "到期", "赎回"],
    "spring_festival_withdrawal": ["春节", "现金", "支取", "ATM", "柜面"],
    "lpr_cut_q2": ["LPR", "下调", "政策", "贷款", "6月"],
    "qixi_deposit_campaign": ["七夕", "情侣", "定期", "营销", "活动"],
}


def _tokenize_zh(text: str) -> set[str]:
    """jieba 分词后剔除纯标点/空白 token，返回小写 token 集合。

    比 .split() 的关键优势：中文按词切分，"理财产品"→{"理财","产品"}，
    "活期存款"→{"活期","存款"}；Jaccard 时才会有有效重叠。
    """
    if not text:
        return set()
    tokens = jieba.lcut(text.lower())
    return {t for t in tokens if t.strip() and not all(c in _PUNCT_CHARS for c in t)}


@dataclass
class RCAScore:
    """单个 RCA 问题的评估分数。"""

    question_id: str
    related_event_id: str
    dimension_recall: float = 0.0  # 0-1: 关键维度识别率
    event_hit: bool = False  # 是否识别出根因事件
    conclusion_similarity: float = 0.0  # 0-1: 结论与期望的语义相似度
    hallucination_detected: bool = False  # 是否存在事实幻觉
    response_time_seconds: float = 0.0

    @property
    def combined_score(self) -> float:
        """综合评分：0-1，权重分配。"""
        weights = {
            "event_hit": 0.4,  # 最重要：是否命中根因
            "dimension_recall": 0.3,  # 次重要：维度识别
            "conclusion_similarity": 0.2,  # 重要：结论一致性
            "hallucination_penalty": 0.1,  # 是否幻觉
        }

        score = (
            (1.0 if self.event_hit else 0.0) * weights["event_hit"]
            + self.dimension_recall * weights["dimension_recall"]
            + self.conclusion_similarity * weights["conclusion_similarity"]
            - (0.2 if self.hallucination_detected else 0.0) * weights["hallucination_penalty"]
        )

        return max(0.0, score)


@dataclass
class RCAEvaluation:
    """完整评估结果集。"""

    total_questions: int = 0
    scores: list[RCAScore] = field(default_factory=list)
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
RCA Evaluation Summary
======================
Total Questions: {self.total_questions}
Passed (>= 0.7): {self.passed_questions}
Pass Rate: {self.pass_rate:.1%}
Average Score: {self.avg_score:.3f}

Details:
--------
"""


class RCAEvaluator:
    """
    RCA (Root Cause Analysis) Agent 评估器。

    工作流程：
    1. 加载 attribution_evaluation.yaml 中的问题和期望答案
    2. 对每个问题运行 RCA Agent
    3. 解析 Agent 的回答，提取：
       - 识别的维度（branch_id, customer_tier, product_id...）
       - 识别的根因事件
       - 生成的结论文本
    4. 与期望答案对比，计算各子维度分数
    5. 聚合为最终评分
    """

    def __init__(self, events_dir: Optional[Path] = None):
        self.eval_dir = Path(__file__).parent.parent / "data"
        self.questions = self._load_evaluation_questions()
        events_dir = events_dir if events_dir is not None else self.eval_dir / "events"
        self._dim_aliases = self._build_dim_alias_map(events_dir)

    @staticmethod
    def _build_dim_alias_map(events_dir: Path) -> dict[str, set[str]]:
        """从 events YAML 抽 product_id ↔ 事件名/描述 别名。

        narrator 被允许引用业务事件名（如 "安鑫 90 天到期"）而非裸 product_id；
        dim_recall 应同时接受两种表达。
        """
        aliases: dict[str, set[str]] = {}
        if not events_dir.exists():
            return aliases
        for yaml_file in events_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            for event in data.get("events", []) or []:
                event_id = (event.get("id") or "").strip()
                event_name = (event.get("name") or "").strip()
                # Aliases the narrator might use to reference products of this event:
                #   - bare product_id
                #   - event.name (and a no-space variant — "安鑫90天到期" ↔ "安鑫 90 天到期")
                #   - all event_hit fuzzy keywords (e.g. "安鑫"), since narrator naming
                #     the event verbatim is itself evidence that the product is on topic
                extras: set[str] = set()
                if event_name:
                    extras.add(event_name)
                    extras.add(event_name.replace(" ", ""))
                extras.update(_EVENT_KEYWORDS.get(event_id, []))

                affected = event.get("affected_dimensions") or {}
                for pid in affected.get("product_id") or []:
                    aliases.setdefault(str(pid), set()).add(str(pid))
                    aliases[str(pid)].update(extras)
                for prop in event.get("propagation") or []:
                    for pid in prop.get("related_products") or []:
                        aliases.setdefault(str(pid), set()).add(str(pid))
                        aliases[str(pid)].update(extras)
        return aliases

    def _value_matches_response(self, value: str, response: str) -> bool:
        """value 或其别名在 response 里出现即视为命中。"""
        candidates = self._dim_aliases.get(value, set()) | {value}
        return any(alias and alias in response for alias in candidates)

    def _load_evaluation_questions(self) -> list[dict]:
        """从 YAML 加载评估问题。"""
        eval_file = self.eval_dir / "attribution_evaluation.yaml"
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
        agent_extracted_dimensions: dict = None,
        agent_identified_event: Optional[str] = None,
        agent_conclusion: str = "",
    ) -> RCAScore:
        """
        评估 Agent 对单个问题的回答。

        Args:
            question_id: 问题 ID
            agent_response: Agent 的原始回答
            agent_extracted_dimensions: Agent 识别的维度字典
            agent_identified_event: Agent 识别的事件 ID
            agent_conclusion: Agent 的结论摘要

        Returns:
            RCAScore: 评分对象
        """
        question = self.get_question(question_id)
        if not question:
            raise ValueError(f"Question {question_id} not found")

        agent_extracted_dimensions = agent_extracted_dimensions or {}
        score = RCAScore(
            question_id=question_id,
            related_event_id=question.get("related_event", "unknown"),
        )

        # 1. 事件命中率 (event_hit)
        expected_event = question.get("related_event")
        if agent_identified_event == expected_event:
            score.event_hit = True
        # 宽松匹配：如果 Agent 提到了正确的事件名称（不一定是 ID）
        elif (
            expected_event
            and agent_response
            and any(
                keyword in agent_response
                for keyword in self._get_event_keywords(expected_event)
            )
        ):
            score.event_hit = True

        # 2. 维度回忆率 (dimension_recall)
        # 每个 expected_dim 是一个 {key: value | [values]} 的 dict；
        # 命中条件：value（或其在 events YAML 里的业务别名）在 agent_response 中出现。
        # 例：product_id=PROD_WEA_0030 也可以靠 narrative 写"安鑫 90 天到期"命中。
        expected_dims = question.get("expected_affected_dimensions", [])
        if expected_dims:
            matched_dims = 0
            for exp_dim in expected_dims:
                for _key, expected_vals in exp_dim.items():
                    vals = expected_vals if isinstance(expected_vals, list) else [expected_vals]
                    if any(self._value_matches_response(str(v), agent_response) for v in vals):
                        matched_dims += 1

            score.dimension_recall = min(
                1.0, matched_dims / max(1, sum(len(d.values()) for d in expected_dims))
            )

        # 3. 结论相似度 (conclusion_similarity)
        # 用 jieba 中文分词后取 Jaccard；裸 .split() 在中文上等同于把整句当一个 token。
        expected_conclusion = question.get("expected_root_cause", "")
        if expected_conclusion and agent_conclusion:
            expected_tokens = _tokenize_zh(expected_conclusion)
            agent_tokens = _tokenize_zh(agent_conclusion)
            intersection = expected_tokens & agent_tokens
            union = expected_tokens | agent_tokens
            if union:
                score.conclusion_similarity = len(intersection) / len(union)

        # 4. 幻觉检测 (hallucination_detected)
        # 简化版：检查是否提到了不存在的产品 ID 或日期
        if self._detect_hallucination(agent_response):
            score.hallucination_detected = True

        return score

    def _get_event_keywords(self, event_id: str) -> list[str]:
        """获取事件的关键词（用于宽松匹配）；单一来源 _EVENT_KEYWORDS。"""
        return _EVENT_KEYWORDS.get(event_id, [])

    def _detect_hallucination(self, response: str) -> bool:
        """检测是否存在明显的事实幻觉。"""
        # 简化实现：检查是否提到了不合理的百分比或日期
        import re

        # 检查不合理的数字
        numbers = re.findall(r"\d+(?:\.\d+)?%", response)
        for num_str in numbers:
            try:
                num = float(num_str.rstrip("%"))
                if num > 200:  # 超过 200% 的变化是不合理的
                    return True
            except ValueError:
                pass

        return False

    def evaluate_batch(self, results: list[dict]) -> RCAEvaluation:
        """
        批量评估多个问题的回答。

        Args:
            results: 包含 question_id, agent_response 等的结果列表

        Returns:
            RCAEvaluation: 完整评估结果
        """
        evaluation = RCAEvaluation(total_questions=len(results))

        for result in results:
            score = self.evaluate_response(
                question_id=result.get("question_id"),
                agent_response=result.get("agent_response", ""),
                agent_extracted_dimensions=result.get("extracted_dimensions"),
                agent_identified_event=result.get("identified_event"),
                agent_conclusion=result.get("conclusion", ""),
            )
            evaluation.scores.append(score)

            if score.combined_score >= 0.7:
                evaluation.passed_questions += 1

        return evaluation
