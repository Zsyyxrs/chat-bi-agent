"""P2 Multi-step Analysis Evaluator: assess analytical reasoning of Analysis Agent."""

import json as _json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Optional

import yaml

from chat_bi_agent.eval.zh_tokenize import overlap_ratio

# G-Eval rubric 的 4 维 backbone（照 P3 `RCAEvaluator._JUDGE_DIMS` 的结构）。
# 每一维都锚在题目 YAML 里人工写死的字段上，见 `_llm_judge_analysis` 的 docstring。
JUDGE_DIMS = (
    "step_fidelity",  # 锚：analysis_steps
    "quantification",  # 锚：expected_insights 里的量化基准
    "causal_reasoning",  # 锚：本题 evaluation_criteria
    "business_actionability",  # 锚：本题 evaluation_criteria
)

# 计分维度权重。judge 缺席时按剩余维度归一，见 `AnalysisScore.combined_score`。
#
# 2026-08-17 第二次调整：`step_completeness` 移出计分（降为诊断），步骤判定整体交给
# judge 的 `step_fidelity`——两者本来锚在同一份 `analysis_steps` 上，但前者数的是**计划
# 节点数**而非步骤有没有做，详见 `AnalysisScore.combined_score` 与 ADR-016 Update。
# judge 份额因此由 0.25 升到 0.35，是这次搬迁的直接结果，不是更信任 LLM 判读。
SCORED_WEIGHTS = {
    "insight_accuracy": 0.45,  # 唯一有硬 ground truth（expected_insights 文本）
    "analysis_rubric": 0.35,  # LLM judge 4 维均值（含 step_fidelity）
    "multi_metric_coverage": 0.20,
}


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
    step_completeness: float = 0.0  # 0-1: 报了几个计划节点 / YAML 步数（诊断，不计分）
    multi_metric_coverage: float = 0.0  # 0-1: 是否覆盖了多个关键指标
    insight_accuracy: float = 0.0  # 0-1: 发现的洞察与期望的相似度
    reasoning_quality: float = 0.0  # 0-1: 推理逻辑的严谨性（诊断，不计分）
    business_relevance: float = 0.0  # 0-1: 结论与业务意义的相关性（诊断，不计分）
    response_time_seconds: float = 0.0
    # G-Eval rubric 子分（judge 成功时填充；失败/关闭时 None，该维退出计分）
    analysis_rubric: Optional[dict] = None  # {step_fidelity, quantification, ...,  method}

    @property
    def rubric_available(self) -> bool:
        """judge 是否真的判了。False 表示总分口径是「确定性三维归一」而非四维。

        必须可辨：judge 全挂的一轮若与正常轮同形，就会打出一批凭空偏低的分数，
        看起来像 agent 退化——本项目反复栽的那类静默失效。
        """
        return self.rubric_score is not None

    @property
    def rubric_score(self) -> Optional[float]:
        """4 维 rubric 均值；无 rubric 时 None。`method` 是元数据，不参与平均。"""
        if not self.analysis_rubric:
            return None
        vals = [
            float(self.analysis_rubric[d])
            for d in JUDGE_DIMS
            if isinstance(self.analysis_rubric.get(d), (int, float))
        ]
        return sum(vals) / len(vals) if vals else None

    @property
    def combined_score(self) -> float:
        """综合评分：0-1。只计入有可比对象的维度。

        2026-08-17（ADR-015）起 `reasoning_quality` 与 `business_relevance` 移出总分，
        降为诊断字段（与 P1 的 `result_match` 同样处理）。原因不是阈值松，是**它们没有
        可比对的对象**：判据只能是「数中文连接词」「数业务名词」，达到 4~5 个即满分——
        任何通顺的中文分析都自动拿满。实测三题上恒等 1.000，合计 35% 权重是常数而非
        测量，等于给每道题无条件加 0.35 分，把 0.7 及格线的含义架空。

        删除是对的，但留下真实缺口：推理链条与业务可落地性此后完全没被度量。
        ADR-016 补上 `analysis_rubric`——照 P3 的 `_llm_judge_conclusion` 做 LLM judge，
        四维各锚在**每道题**的 analysis_steps / expected_insights / evaluation_criteria
        上（被删两维锚在通用词表上，对任何题目都一样，这是本质差别）。

        权重：确定性三维 0.75 / judge 0.25，对齐 P3 的 0.80/0.20。judge 的锚仍是人写
        rubric + LLM 判读，比 gold SQL / 事件库弱，不该主导总分。

        judge 缺席（未开启或调用失败）时按剩余维度**归一**，而不是记 0：记 0 等于因
        基础设施故障扣 agent 的分。归一会让口径在两种运行间不同，故必须配 `rubric_available`
        显式记账，runner 与报告都要打出来。

        **2026-08-17 第二次调整：`step_completeness` 也移出总分。** 它的实现是
        `len(mentioned_steps) / len(analysis_steps)`——数的是**计划节点数**，不是步骤有没有
        做。实测 q001：agent 只规划 2 步（YAML 有 5 步）→ 0.40，而 judge 拿同一份
        `analysis_steps` 判内容给 1.00。人工核对回答全文，5 步的实质内容全部覆盖（节前基线 /
        假期数据 / 日均与增长率 / 按渠道分别统计 / 对比总结），**judge 是对的**：它罚的是
        「把 5 步并成 2 步做完」这件本身无可指摘的事。

        换成内容词召回同样不行（实测 0.553/0.337/0.350，比数节点还低）：期望步骤是指令式
        文本且带表名（「从 fct_holding 查询…」），agent 用业务语言报结果，不会复述表名——
        那是另一个错的测法。步骤判定因此整体交给 judge 的 `step_fidelity`，本字段保留为
        「计划粒度」诊断。
        """
        parts = {
            "insight_accuracy": self.insight_accuracy,
            "multi_metric_coverage": self.multi_metric_coverage,
        }
        rubric = self.rubric_score
        if rubric is not None:
            parts["analysis_rubric"] = rubric

        total_weight = sum(SCORED_WEIGHTS[k] for k in parts)
        if total_weight <= 0:
            return 0.0
        score = sum(v * SCORED_WEIGHTS[k] for k, v in parts.items()) / total_weight
        return max(0.0, min(1.0, score))


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

    def __init__(self, use_llm_judge: bool = True, llm_client=None, judge_samples: int = 3):
        """
        Args:
            use_llm_judge: True 时 `analysis_rubric` 走 G-Eval 4 维 rubric LLM 判分。
                失败**不回退到启发式**（见 `_llm_judge_analysis`），而是留 None 并让该维
                退出计分。False 时只算确定性三维（单元测试 / 离线快速跑）。
                默认 True 与 P3 `RCAEvaluator` 一致：默认关闭的话，谁忘了开就静默少一维。
            llm_client: 自定义 LLM 客户端（须暴露 chat(system_prompt, user_prompt, temperature)）。
                默认 None → 用 chat_bi_agent.llm.qwen_client。
            judge_samples: 判几次取逐维中位数（self-consistency）。默认 3。

                这一条与 P3 不同（P3 单次判），依据是实测：2026-08-17 用同一份 agent
                回答连判 3 次，**temperature=0 并不给出确定性输出**——
                  q001 rubric avg 0.750 / 0.812 / 0.750
                  q002 rubric avg 0.750 / 0.875 / 0.562   ← 0.31 的摆幅
                  q003 rubric avg 0.375 / 0.312 / 0.312
                q002 那 0.31 摆幅乘 0.25 权重 ≈ 总分 ±0.078，跟真实退化同量级，单次判分
                无法区分「agent 变差了」和「judge 这次心情不同」。
                取中位数的成本可以忽略：judge 每题几秒，agent 每题 300~500s。
        """
        self.eval_dir = Path(__file__).parent.parent / "data"
        self.questions = self._load_evaluation_questions()
        self._use_llm_judge = use_llm_judge
        self._judge_samples = max(1, int(judge_samples))
        if llm_client is not None:
            self._llm_client = llm_client
        elif use_llm_judge:
            from chat_bi_agent.llm import qwen_client as _qwen

            self._llm_client = _qwen
        else:
            self._llm_client = None

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

    _JUDGE_SYSTEM_PROMPT = (
        "你是 BI 多步分析评估专家。给定一道分析题的【参考解法】与 Agent 的【最终回答】，"
        "按下列 4 维 rubric 各打 0-1 分（只允许 0.0 / 0.25 / 0.5 / 0.75 / 1.0）：\n"
        "  1. step_fidelity: agent 是否真的做了参考步骤里的关键动作（看内容，不数条数）\n"
        "  2. quantification: agent 报的数字与参考洞察里的量化基准是否方向一致、幅度接近\n"
        "  3. causal_reasoning: agent 是否说清因果链（事件/条件 → 中间环节 → 指标变化）\n"
        "  4. business_actionability: agent 是否给出可落地的业务含义或建议\n\n"
        "===== step_fidelity 判分规则（机械执行）=====\n"
        "对【参考分析步骤】逐条判「agent 回答里能否找到对应动作或其结果」。\n"
        "  - 命中比例 ≥ 80% → 1.0； ≥ 60% → 0.75； ≥ 40% → 0.5； ≥ 20% → 0.25； 否则 0.0\n"
        "  宽容规则：agent 直接给出该步骤的**结果**（如报出两个时间窗的总额）即算命中，"
        "不要求它复述「我执行了 step2」。步骤合并完成也算命中。\n\n"
        "===== quantification 判分规则（机械执行）=====\n"
        "对【参考洞察】里**带数字**的条目逐条判：\n"
        "  Step 1 - 方向：agent 报的变化方向是否与参考一致？\n"
        "  Step 2 - 幅度：差距比例 = |agent 值 - 参考值| / |参考值|。\n"
        "  打分：方向一致且差距 ≤ 50% → 1.0；方向一致且 50% < 差距 ≤ 100% → 0.5；"
        "方向相反、差距 > 100%、或 agent 完全无数字 → 0.0。多条取平均。\n"
        "  若参考洞察里没有任何数字，本维按 agent 是否给出了具体量化结果判："
        "有具体数字与口径 → 1.0；只有定性描述 → 0.25。\n"
        "  等价规则：英文 metric 名与中文自然表述视为等价"
        "（如 average_daily_balance ≡「日均余额」、AUM ≡「客户总资产」），"
        "不要因措辞差异扣分。\n\n"
        "===== causal_reasoning 判分规则（机械执行）=====\n"
        "检查是否呈现「触发因素 → 中间环节 → 指标变化」三段链条。\n"
        "  三段都点到 → 1.0；两段 → 0.5；一段 → 0.25；只罗列数字无因果 → 0.0\n"
        "  **不要因为文风平实、没用「因此/所以」这类连接词而扣分**——判的是有没有链条，"
        "不是有没有连接词。\n\n"
        "===== business_actionability 判分规则（机械执行）=====\n"
        "  - 给出了针对具体人群/产品/渠道的可执行建议或明确业务含义 → 1.0\n"
        "  - 给出业务含义但不具体（如「需关注客户流失」） → 0.5\n"
        "  - 仅罗列数据结论，无任何业务解读 → 0.0\n"
        "  **不要因为出现「客户」「分行」「风险」等业务名词就给分**——判的是有没有解读，"
        "不是有没有词。\n\n"
        "【本题人工 rubric】里的每条 criterion 请映射到上面 4 维中相关的那一维，作为重点"
        "检查项；它们不另设维度，只用来精化判分。\n\n"
        "严格只输出 JSON，包在 ```json fence 内，键名固定为上述 4 个：\n"
        '```json\n{"step_fidelity": 1.0, "quantification": 0.5, '
        '"causal_reasoning": 0.75, "business_actionability": 1.0}\n```'
    )

    _JUDGE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

    @staticmethod
    def _format_listdict_block(value, prefix: str = "  - ") -> str:
        """把 YAML 的 list-of-single-key-dict 渲染成 prompt 段：`  - key: value`。"""
        items = _yaml_listdict_to_dict(value)
        lines: list[str] = []
        for k, v in items.items():
            if isinstance(v, list):
                lines.extend(f"{prefix}{k}: {one}" for one in v)
            else:
                lines.append(f"{prefix}{k}: {v}")
        return "\n".join(lines)

    def _llm_judge_analysis(
        self,
        question_id: str,
        agent_response: str,
    ) -> tuple[Optional[float], Optional[dict]]:
        """G-Eval 4 维 rubric LLM judge。返回 (avg, rubric) 或失败时 (None, None)。

        与 P3 `_llm_judge_conclusion` 的唯一实质差别是**失败不回退**。P3 回退到 Jaccard，
        因为 Jaccard 对「结论是否相似」至少是个弱信号。P2 这四维没有这样的替代品：
        唯一能想到的廉价近似，正是 ADR-015 刚删掉的关键词计数（数「因此/所以」「客户/分行」），
        任何通顺中文都能拿满分。把它请回 fallback 分支只会让缺陷更隐蔽——平时看不见，
        judge 一挂就悄悄接管。所以宁可让该维退出计分并显式记账。

        每一维都锚在题目 YAML 的人工字段上，这是它区别于被删两维的关键：
          step_fidelity          ← analysis_steps
          quantification         ← expected_insights 里的量化基准
          causal_reasoning       ← evaluation_criteria
          business_actionability ← evaluation_criteria
        """
        if self._llm_client is None:
            return None, None

        samples = [
            s
            for s in (
                self._judge_once(question_id, agent_response) for _ in range(self._judge_samples)
            )
            if s
        ]
        if not samples:
            return None, None

        # 逐维取中位数：某一次判飞了不会拖走整题。全失败才算 judge 未判。
        rubric = {d: median(s[d] for s in samples) for d in JUDGE_DIMS}
        rubric["method"] = "llm_judge"
        rubric["samples"] = len(samples)
        return sum(rubric[d] for d in JUDGE_DIMS) / len(JUDGE_DIMS), rubric

    def _judge_once(self, question_id: str, agent_response: str) -> Optional[dict]:
        """单次判分。失败返回 None（由 `_llm_judge_analysis` 汇总）。"""
        question = self.get_question(question_id) or {}
        parts = [f"【Agent 最终回答】\n{(agent_response or '').strip()}"]

        steps_block = self._format_listdict_block(question.get("analysis_steps"))
        if steps_block:
            parts.append("【参考分析步骤（step_fidelity 维以此为准）】\n" + steps_block)
        insights_block = self._format_listdict_block(question.get("expected_insights"))
        if insights_block:
            parts.append("【参考洞察（quantification 维以此为量化基准）】\n" + insights_block)
        criteria_block = self._format_listdict_block(question.get("evaluation_criteria"))
        if criteria_block:
            parts.append("【本题人工 rubric（映射到 4 维上重点检查）】\n" + criteria_block)

        user_prompt = "\n\n".join(parts) + "\n"
        try:
            result = self._llm_client.chat(
                system_prompt=self._JUDGE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.0,
            )
            raw = (result.content or "").strip()
            m = self._JUDGE_FENCE_RE.search(raw)
            payload = _json.loads(m.group(1) if m else raw)
            return {d: max(0.0, min(1.0, float(payload.get(d, 0.0)))) for d in JUDGE_DIMS}
        except Exception as exc:  # parse error / API error / 任何意外
            # 不回退到启发式：见 `_llm_judge_analysis` docstring。
            logging.warning("P2 rubric judge 单次失败（%s）: %s", question_id, exc)
            return None

    def evaluate_response(
        self,
        question_id: str,
        agent_response: str,
        mentioned_steps: list[str] = None,
        mentioned_metrics: list[str] = None,
        extracted_insights: list[str] = None,
        precomputed_rubric: Optional[dict] = None,
    ) -> AnalysisScore:
        """
        评估 Agent 对单个问题的回答。

        Args:
            question_id: 问题 ID
            agent_response: Agent 的原始回答
            mentioned_steps: Agent 在回答中提到的分析步骤
            mentioned_metrics: Agent 提到的关键指标
            extracted_insights: 从回答中提取的业务洞察
            precomputed_rubric: 已有的 rubric 子分（离线重放用）。给了就直接采用，
                不再调 LLM——产物里存了 rubric 后，改确定性维度可以零成本精确重放。

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
        # 整词匹配。此前是 `any(m in agent_response for m in metric)`——metric 是字符串，
        # `for m in metric` 迭代的是**单个字**，判据退化成「指标名里任意一个字出现过吗」。
        # '长'（长期/董事长）、'户'（账户）这类字在银行叙述里几乎必然出现，于是本维在
        # 每道题上恒等 1.000，占着 20% 权重却零区分度。与 ADR-015 修的 insight 维同类。
        key_metrics = self._extract_key_metrics(question)
        if key_metrics:
            mentioned_key_metrics = sum(1 for metric in key_metrics if metric in agent_response)
            score.multi_metric_coverage = min(1.0, mentioned_key_metrics / max(1, len(key_metrics)))

        # 3. 洞察准确度 (insight_accuracy)
        # 逐条 expected insight 算「内容词召回率」，取均值。
        #
        # 2026-08-15 之前这里是 `exp_insight_val.split()[:5]` + 任一 token 命中即算数，
        # 对中文等于没分词：纯中文洞察 split 后只剩整句一个 token（要求逐字出现，
        # 评测集里 10 条永远拿 0），中英混合的首 token 是 '2'/'ATM'/'和' 这类
        # （几乎必然误命中，21 条白送分）。两个方向同时错，而本维占 25% 权重。
        expected_insights = _yaml_listdict_to_dict(question.get("expected_insights"))
        if expected_insights:
            ratios = []
            for _key, exp_insight_val in expected_insights.items():
                # list 型逐项算，取最好的一项——任一项被说到就算讲到了这条洞察
                if isinstance(exp_insight_val, list):
                    ratios.append(
                        max(
                            (overlap_ratio(str(v), agent_response) for v in exp_insight_val),
                            default=0.0,
                        )
                    )
                else:
                    ratios.append(overlap_ratio(str(exp_insight_val), agent_response))

            score.insight_accuracy = sum(ratios) / len(ratios) if ratios else 0.0

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

        # 6. rubric LLM judge (analysis_rubric) —— 唯一计分的非确定性维度
        if precomputed_rubric is not None:
            score.analysis_rubric = precomputed_rubric
        elif self._use_llm_judge:
            _avg, rubric = self._llm_judge_analysis(question_id, agent_response)
            score.analysis_rubric = rubric  # None 表示 judge 失败，该维退出计分

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
                precomputed_rubric=result.get("precomputed_rubric"),
            )
            evaluation.scores.append(score)

            if score.combined_score >= 0.7:
                evaluation.passed_questions += 1

        return evaluation
