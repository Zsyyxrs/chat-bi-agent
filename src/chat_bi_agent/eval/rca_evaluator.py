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


# Static enum-code aliases for common business identifiers (customer_tier,
# transaction_type, transaction_channel, branch_level, branch_id, campaign_id).
# Narrator is told via synthesizer prompt to use these codes verbatim, but often
# "translates" them into natural Chinese (WITHDRAW → "取现", COUNTER → "柜面",
# BR_CITY_0006 → "上海"). dim_recall should accept either form so a perfectly
# correct Chinese narrative doesn't score 0 just because of code/translation mismatch.
_ENUM_ALIASES: dict[str, set[str]] = {
    # customer_tier
    "BASIC": {"BASIC", "基础", "基础客户"},
    "MASS": {"MASS", "大众", "大众客户", "大众层"},
    "AFFLUENT": {"AFFLUENT", "富裕", "富裕客户", "富裕层"},
    "HIGH_NET_WORTH": {"HIGH_NET_WORTH", "高净值", "高净值客户"},
    # transaction_type
    "WITHDRAW": {"WITHDRAW", "支取", "取现", "提现", "现金支取"},
    "DEPOSIT": {"DEPOSIT", "存款"},
    "TRANSFER": {"TRANSFER", "转账"},
    "LOAN": {"LOAN", "贷款"},
    # transaction_channel
    "ATM": {"ATM"},
    "COUNTER": {"COUNTER", "柜面", "柜台"},
    "ONLINE": {"ONLINE", "网银", "线上"},
    "MOBILE": {"MOBILE", "手机银行", "移动端"},
    # branch_level
    "CITY": {"CITY", "城分行", "城市分行"},
    "SUBBRANCH": {"SUBBRANCH", "支行"},
    "HEADQUARTERS": {"HEADQUARTERS", "总行"},
    # branch_id ↔ city (only the three actually referenced by attribution YAML)
    "BR_CITY_0000": {"BR_CITY_0000", "杭州"},
    "BR_CITY_0002": {"BR_CITY_0002", "南京"},
    "BR_CITY_0006": {"BR_CITY_0006", "上海"},
    # campaign
    "CAMP_QIXI": {"CAMP_QIXI", "七夕"},
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


# 中文停用词：保守列表，只剔除几乎不携带 RCA 业务语义的功能词。
# 避免过度剔除（"月"/"日"/"年" 在日期上下文是有意义的，故保留）。
_ZH_STOPWORDS: frozenset[str] = frozenset(
    {
        "的",
        "了",
        "是",
        "在",
        "与",
        "和",
        "也",
        "都",
        "还",
        "而",
        "但",
        "就",
        "等",
        "这",
        "那",
        "此",
        "其",
        "之",
        "或",
        "或者",
        "及",
        "及其",
        "以",
        "以及",
        "我",
        "你",
        "他",
        "她",
        "它",
        "我们",
        "你们",
        "他们",
        "自己",
        "一",
        "个",
        "些",
        "多",
        "少",
        "上",
        "下",
        "中",
        "内",
        "外",
        "可以",
        "可能",
        "应该",
        "需要",
        "已经",
        "正在",
        "将",
        "已",
        "即",
        "还是",
        "进行",
        "形成",
        "完成",
        "出现",
        "表示",
        "表明",
        "显示",
        "反映",
        "体现",
        "如",
        "如下",
        "如此",
        "对",
        "对于",
        "关于",
        "通过",
        "经过",
        "由",
        "由于",
        "因",
        "因为",
        "所以",
        "因此",
        "导致",
    }
)


def _tokenize_for_similarity(text: str) -> set[str]:
    """concl_sim 专用 tokenizer：jieba.cut_for_search 提高颗粒度 + 去停用词。

    与 `_tokenize_zh` 区别：
    - 用 `cut_for_search`：长复合词会被进一步切（"理财产品"→{"理财","产品","理财产品"}），
      增加 Jaccard 命中面
    - 过滤 `_ZH_STOPWORDS`：剔除"的/了/是/在"这类公词，减少 expected vs narrative 共有
      的噪声，让差异化业务词在分子里占更大比重
    """
    if not text:
        return set()
    tokens = jieba.cut_for_search(text.lower())
    return {
        t
        for t in tokens
        if t.strip() and not all(c in _PUNCT_CHARS for c in t) and t not in _ZH_STOPWORDS
    }


_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+to\s+(\d{4})-(\d{2})-(\d{2})$")
_QUARTER_ZH = ("一", "二", "三", "四")

# Product ID pattern matches naming conventions used in seed data
# (PROD_WEA_0030, PROD_DEP_0005, PROD_LOAN_0012, ...).
_PRODUCT_ID_RE = re.compile(r"\bPROD_[A-Z]+_\d{4}\b")


def _date_aliases(value: str) -> set[str]:
    """ISO 日期 / 期段字符串展开成中文叙述里常见的多种写法。

    narrator 写日期方式多样（"6月20日"、"二季度"、"2月15-23日"、"春节期间"），
    expected_dim 里却只存 ISO 形式。靠这套展开，narrative 用任一中文等价写法都能命中。
    """
    aliases: set[str] = {value}
    s = (value or "").strip()
    if not s:
        return aliases

    m = _ISO_DATE_RE.match(s)
    if m:
        _, mo, day = m.groups()
        mo_n, day_n = int(mo), int(day)
        aliases.add(f"{mo_n}月{day_n}日")
        aliases.add(f"{mo_n}/{day_n}")
        aliases.add(f"{mo_n}-{day_n}")
        q = (mo_n - 1) // 3 + 1
        aliases.add(f"Q{q}")
        aliases.add(f"{_QUARTER_ZH[q - 1]}季度")
        return aliases

    m = _ISO_PERIOD_RE.match(s)
    if m:
        _, mo1, d1, _, mo2, d2 = m.groups()
        mo1_n, d1_n, mo2_n, d2_n = int(mo1), int(d1), int(mo2), int(d2)
        if mo1 == mo2:
            aliases.add(f"{mo1_n}月{d1_n}-{d2_n}日")
            aliases.add(f"{mo1_n}月{d1_n}日-{d2_n}日")
        # 已知节庆窗口（2 月中下旬）→ 加春节别名。
        # 这是数据集级别的固定窗口，不是一般化春节算法（春节日期年年不同）。
        if mo1_n == 2 and mo2_n == 2 and d1_n >= 10 and d2_n <= 28:
            aliases.add("春节")
            aliases.add("春节假期")
            aliases.add("春节期间")
    return aliases


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
    # G-Eval rubric 子分（仅当 use_llm_judge=True 时填充；否则 None）
    conclusion_rubric: Optional[dict] = None  # {event, quantification, mechanism, scope, method}

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

    def __init__(
        self,
        events_dir: Optional[Path] = None,
        valid_product_ids: Optional[set[str]] = None,
        use_llm_judge: bool = True,
        llm_client=None,
    ):
        """
        Args:
            events_dir: 事件 YAML 目录，用于构建 dim_alias_map。默认 data/events。
            valid_product_ids: 真实可用的 product_id 集合（如来自 dim_product 表全集）。
                提供后，narrative 里凡是匹配 PROD_XXX_NNNN 但不在该集合的 ID 都会被标记为
                幻觉。不提供（None）时跳过该检测，仅保留 >200% 数字的旧检测。
            use_llm_judge: True 时 conclusion_similarity 走 G-Eval 4 维 rubric LLM 判分，
                失败回退到 Jaccard。False 时只用 Jaccard（单元测试 / 离线快速跑）。
            llm_client: 自定义 LLM 客户端（须暴露 chat(system_prompt, user_prompt, temperature)）。
                默认 None → 用 chat_bi_agent.llm.qwen_client。
        """
        self.eval_dir = Path(__file__).parent.parent / "data"
        self.questions = self._load_evaluation_questions()
        events_dir = events_dir if events_dir is not None else self.eval_dir / "events"
        self._dim_aliases = self._build_dim_alias_map(events_dir)
        self._valid_product_ids = set(valid_product_ids) if valid_product_ids is not None else None
        self._use_llm_judge = use_llm_judge
        if llm_client is not None:
            self._llm_client = llm_client
        elif use_llm_judge:
            from chat_bi_agent.llm import qwen_client as _qwen

            self._llm_client = _qwen
        else:
            self._llm_client = None

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
        """value 或其别名在 response 里出现即视为命中。

        别名来源（OR 合并）：
        - self._dim_aliases：事件 YAML 派生的产品别名（PROD_WEA_0030 ↔ 安鑫/到期/...）
        - _ENUM_ALIASES：静态业务码 ↔ 中文映射（WITHDRAW ↔ 取现，BR_CITY_0006 ↔ 上海，等）
        - _date_aliases(value)：ISO 日期/期段 → 中文等价写法（2026-06-20 ↔ 6月20日/二季度）
        - value 自身：兜底，保证裸字符串永远在候选里
        """
        candidates = (
            self._dim_aliases.get(value, set())
            | _ENUM_ALIASES.get(value, set())
            | _date_aliases(value)
            | {value}
        )
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

    _JUDGE_SYSTEM_PROMPT = (
        "你是 BI 归因评估专家。给定参考根因（expected）与 Agent 生成的结论（agent），"
        "按下列 4 维 rubric 各打 0-1 分（0.0 / 0.25 / 0.5 / 0.75 / 1.0）：\n"
        "  1. event_identification: agent 是否准确识别并提及参考里的根因事件（事件名/动作）\n"
        "  2. quantification: agent 是否给出与参考一致或语义等价的量化数字（百分比/比率/金额）\n"
        "  3. mechanism: agent 是否说清因果链（事件→中间环节→指标变化）\n"
        "  4. scope: agent 是否准确界定影响范围（分行/客户层级/产品维度等）\n\n"
        "如果输入包含【本题人工 rubric】，把每条 criterion 映射到上面 4 维 backbone 里相关的"
        "那一维并在打分时作为重点检查项；这些 criterion 不另设维度，只用来精化判分。\n"
        "如果输入包含【期望关键指标】，把它作为 quantification 维的结构化 ground truth："
        "agent 量化是否方向一致、幅度落在 ±50% 范围内。\n\n"
        "严格只输出 JSON，包在 ```json fence 内，键名固定为上述 4 个：\n"
        '```json\n{"event_identification": 1.0, "quantification": 0.5, '
        '"mechanism": 0.75, "scope": 1.0}\n```\n'
        "评分校准：\n"
        "- 完全命中（事件名、量化数字、机制链、范围都对）→ 1.0\n"
        "- 方向对但细节缺/数字差异大 → 0.5\n"
        "- 完全没提或方向错 → 0.0\n"
        "- 内部代码（如 BR_CITY_0006、PROD_WEA_0030）若 agent 能映射到业务名（上海分行/安鑫）"
        "视为表达等价，按命中算。"
    )

    _JUDGE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
    _JUDGE_DIMS = ("event_identification", "quantification", "mechanism", "scope")

    @staticmethod
    def _format_evaluation_criteria(criteria: Optional[list]) -> str:
        """把 attribution_evaluation.yaml 的 evaluation_criteria 列表渲染成 prompt 段。

        YAML 形式：列表里每项是单键 dict，键是 criterion 名，值是中文描述。
        如：[{product_identification: "Agent 是否精准定位..."}, ...]
        """
        if not criteria:
            return ""
        lines: list[str] = []
        for item in criteria:
            if isinstance(item, dict):
                for k, v in item.items():
                    lines.append(f"  - {k}: {v}")
        return "\n".join(lines)

    @staticmethod
    def _format_expected_key_metrics(metrics: Optional[list]) -> str:
        """渲染 expected_key_metrics 为人话：metric=X 方向=down 幅度=~8.5%。"""
        if not metrics:
            return ""
        lines: list[str] = []
        for m in metrics:
            if not isinstance(m, dict):
                continue
            parts = []
            if m.get("metric"):
                parts.append(f"metric={m['metric']}")
            if m.get("change_direction"):
                parts.append(f"direction={m['change_direction']}")
            if m.get("magnitude"):
                parts.append(f"magnitude={m['magnitude']}")
            if parts:
                lines.append("  - " + " ".join(parts))
        return "\n".join(lines)

    def _llm_judge_conclusion(
        self,
        expected: str,
        agent: str,
        question: Optional[dict] = None,
    ) -> tuple[float, Optional[dict]]:
        """G-Eval 4 维 rubric LLM judge。返回 (avg_score, rubric_dict_or_None)。

        - 通用 4 维 backbone：event_identification / quantification / mechanism / scope
        - 若 `question` 提供，把每题自定义的 evaluation_criteria 注入 prompt 作为
          per-question 重点检查项（提升判分针对性），expected_key_metrics 注入
          作为 quantification 维的结构化 ground truth。
        - 失败回退到 Jaccard，调用方据 rubric=None 判定走了 fallback。
        """
        import json as _json

        # 防御：客户端缺失（use_llm_judge=False 但意外走到这里）
        if self._llm_client is None:
            return self._jaccard_similarity(expected, agent), None

        user_prompt_parts = [f"【expected】\n{expected.strip()}", f"【agent】\n{agent.strip()}"]
        if question is not None:
            criteria_block = self._format_evaluation_criteria(question.get("evaluation_criteria"))
            if criteria_block:
                user_prompt_parts.append(
                    "【本题人工 rubric（请映射到 4 维 backbone 上重点检查）】\n" + criteria_block
                )
            metrics_block = self._format_expected_key_metrics(question.get("expected_key_metrics"))
            if metrics_block:
                user_prompt_parts.append(
                    "【期望关键指标（quantification 维以此为 ground truth）】\n" + metrics_block
                )
        user_prompt = "\n\n".join(user_prompt_parts) + "\n"
        try:
            result = self._llm_client.chat(
                system_prompt=self._JUDGE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.0,
            )
            raw = (result.content or "").strip()
            m = self._JUDGE_FENCE_RE.search(raw)
            payload_text = m.group(1) if m else raw
            payload = _json.loads(payload_text)
            scores = [float(payload.get(dim, 0.0)) for dim in self._JUDGE_DIMS]
            # clip 到 [0,1]，平均
            scores = [max(0.0, min(1.0, s)) for s in scores]
            avg = sum(scores) / len(scores)
            rubric = dict(zip(self._JUDGE_DIMS, scores))
            rubric["method"] = "llm_judge"
            return avg, rubric
        except Exception as exc:  # parse error / API error / 任何意外
            logging.warning("LLM judge 失败回退到 Jaccard: %s", exc)
            return self._jaccard_similarity(expected, agent), None

    @staticmethod
    def _jaccard_similarity(expected: str, agent: str) -> float:
        expected_tokens = _tokenize_for_similarity(expected)
        agent_tokens = _tokenize_for_similarity(agent)
        union = expected_tokens | agent_tokens
        return (len(expected_tokens & agent_tokens) / len(union)) if union else 0.0

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
        # YAML 两种写法都接受：related_event (单数 str) 和 related_events (复数 list)
        expected_events = self._collect_expected_events(question)
        if expected_events:
            # 严格匹配：agent 识别的事件出现在期望列表里
            if agent_identified_event in expected_events:
                score.event_hit = True
            # 宽松匹配：narrative 含任一期望事件的关键词
            elif agent_response and any(
                keyword in agent_response
                for ev in expected_events
                for keyword in self._get_event_keywords(ev)
            ):
                score.event_hit = True

        # 2. 维度召回率 (dimension_recall)
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
        # 默认走 G-Eval 4 维 rubric LLM judge（事件/量化/机制/范围）；
        # 失败回退到 Jaccard（jieba.cut_for_search + 中文停用词过滤）。
        expected_conclusion = question.get("expected_root_cause", "")
        if expected_conclusion and agent_conclusion:
            if self._use_llm_judge:
                sim, rubric = self._llm_judge_conclusion(
                    expected_conclusion, agent_conclusion, question=question
                )
                score.conclusion_similarity = sim
                score.conclusion_rubric = rubric  # None 表示走了 Jaccard fallback
            else:
                score.conclusion_similarity = self._jaccard_similarity(
                    expected_conclusion, agent_conclusion
                )

        # 4. 幻觉检测 (hallucination_detected)
        # 简化版：检查是否提到了不存在的产品 ID 或日期
        if self._detect_hallucination(agent_response):
            score.hallucination_detected = True

        return score

    def _get_event_keywords(self, event_id: str) -> list[str]:
        """获取事件的关键词（用于宽松匹配）；单一来源 _EVENT_KEYWORDS。"""
        return _EVENT_KEYWORDS.get(event_id, [])

    @staticmethod
    def _collect_expected_events(question: dict) -> list[str]:
        """同时支持 related_event（单数 str）和 related_events（复数 list）。

        旧 YAML schema 是单数；attribution_q007 用了复数 — 历史不一致。
        统一返回去重列表（保留顺序）。
        """
        seen: list[str] = []
        single = question.get("related_event")
        if isinstance(single, str) and single:
            seen.append(single)
        plural = question.get("related_events")
        if isinstance(plural, list):
            for ev in plural:
                if isinstance(ev, str) and ev and ev not in seen:
                    seen.append(ev)
        return seen

    def _detect_hallucination(self, response: str) -> bool:
        """检测是否存在明显的事实幻觉。

        两类检查：
        1. 数字异常：任何 >200% 的百分比变化
        2. 编造 product_id（仅当 self._valid_product_ids 提供时）：narrative 里出现
           符合 PROD_XXX_NNNN 模式但不在合法集合的产品 ID
        """
        if not response:
            return False

        # 1. 检查不合理的数字
        for num_str in re.findall(r"\d+(?:\.\d+)?%", response):
            try:
                if float(num_str.rstrip("%")) > 200:
                    return True
            except ValueError:
                pass

        # 2. 检查编造的 product_id
        if self._valid_product_ids is not None:
            for match in _PRODUCT_ID_RE.finditer(response):
                if match.group(0) not in self._valid_product_ids:
                    return True

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
