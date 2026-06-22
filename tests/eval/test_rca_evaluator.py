"""Tests for RCAEvaluator scoring (alias-aware dim_recall, jieba-based concl_sim)."""

from pathlib import Path

import pytest
import yaml

from chat_bi_agent.eval.rca_evaluator import (
    RCAEvaluator,
    _tokenize_zh,
)

# ----------------------------- helpers -----------------------------


@pytest.fixture
def events_dir(tmp_path: Path) -> Path:
    """Minimal events YAML with one event covering anxin scenario."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (events_dir / "product_expiry.yaml").write_text(
        yaml.safe_dump(
            {
                "events": [
                    {
                        "id": "anxin_90_expire",
                        "name": "安鑫 90 天到期",
                        "description": "短期理财集中到期",
                        "affected_dimensions": {
                            "product_id": ["PROD_WEA_0030", "PROD_WEA_0031"],
                        },
                        "propagation": [
                            {
                                "related_products": ["PROD_WEA_0032", "PROD_WEA_0033"],
                            }
                        ],
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return events_dir


@pytest.fixture
def evaluator_with_questions(events_dir: Path, monkeypatch: pytest.MonkeyPatch) -> RCAEvaluator:
    """Construct RCAEvaluator with stubbed questions list.

    Avoids dependence on the real attribution_evaluation.yaml.
    """
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=False)
    ev.questions = [
        {
            "id": "Q_PRODUCT_ID",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [
                {"product_id": "PROD_WEA_0030"},
            ],
            "expected_root_cause": "安鑫 90 天理财产品到期，导致活期存款下降。",
        },
        {
            "id": "Q_RELATED_LIST",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [
                {"related_products": ["PROD_WEA_0032", "PROD_WEA_0033"]},
            ],
            "expected_root_cause": "客户续作到其他理财产品。",
        },
        {
            "id": "Q_NON_PRODUCT",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [
                {"branch_id": "BR_CITY_0006"},
                {"customer_tier": "HIGH_NET_WORTH"},
            ],
            "expected_root_cause": "上海分行高净值客户活期存款下降。",
        },
    ]
    return ev


# ------------------------- _tokenize_zh ----------------------------


def test_tokenize_zh_segments_chinese_into_meaningful_words():
    """关键点：相比 .split() 把整句当 1 个 token，jieba 应切出多个有效词。"""
    raw = "活期存款大幅下降"
    assert len(raw.split()) == 1  # baseline: .split() 给 1 token
    tokens = _tokenize_zh(raw)
    assert len(tokens) >= 2  # jieba 至少多切几刀
    assert "活期存款" in tokens  # 复合词在 dict 里整体保留
    assert "下降" in tokens
    assert "" not in tokens


def test_tokenize_zh_drops_pure_punctuation():
    tokens = _tokenize_zh("到期，赎回。续作！")
    assert all(t not in tokens for t in {"，", "。", "！", " "})
    assert {"到期", "赎回", "续作"} <= tokens


def test_tokenize_zh_handles_mixed_zh_en():
    """jieba 把 underscore/space 当分隔符，PROD_WEA_0030 会被切成多段；
    我们不在意切法，只要保证两边一致就行（Jaccard 仍可比）。"""
    tokens = _tokenize_zh("PROD_WEA_0030 到期")
    assert "到期" in tokens
    # 切片各段应保留至少 id 的数字尾巴和字母段
    assert "0030" in tokens
    assert "prod" in tokens


def test_tokenize_zh_empty_input_returns_empty_set():
    assert _tokenize_zh("") == set()
    assert _tokenize_zh(None) == set()  # type: ignore[arg-type]


# ----------------------- alias map -------------------------------


def test_alias_map_includes_event_name_for_affected_product(events_dir: Path):
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=False)
    assert "安鑫 90 天到期" in ev._dim_aliases["PROD_WEA_0030"]
    # Also the no-space variant
    assert "安鑫90天到期" in ev._dim_aliases["PROD_WEA_0030"]


def test_alias_map_covers_related_products_from_propagation(events_dir: Path):
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=False)
    assert "PROD_WEA_0032" in ev._dim_aliases
    assert "安鑫 90 天到期" in ev._dim_aliases["PROD_WEA_0032"]


def test_alias_map_missing_events_dir_returns_empty(tmp_path: Path):
    ev = RCAEvaluator(events_dir=tmp_path / "nope", use_llm_judge=False)
    assert ev._dim_aliases == {}


# ------------------- dim_recall: alias matching ------------------


def test_dim_recall_matches_via_event_name_when_id_absent(evaluator_with_questions: RCAEvaluator):
    """narrative 写"安鑫 90 天到期"，没有裸 PROD_WEA_0030 — 修前会 0，修后应命中。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="本周「安鑫 90 天到期」事件导致活期存款下降。",
        agent_conclusion="安鑫 90 天到期。",
    )
    assert score.dimension_recall == 1.0


def test_dim_recall_still_matches_when_id_present(evaluator_with_questions: RCAEvaluator):
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="PROD_WEA_0030 到期触发赎回。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0


def test_dim_recall_list_value_any_alias_counts(evaluator_with_questions: RCAEvaluator):
    """related_products 是 list；其中任意一个的别名命中即算。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_RELATED_LIST",
        agent_response="资金续作转向「安鑫 90 天到期」相关后续产品。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0


def test_dim_recall_partial_hit_proportional(evaluator_with_questions: RCAEvaluator):
    """两个 expected dim，narrative 只提一个 → 0.5。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_NON_PRODUCT",
        agent_response="上海分行（BR_CITY_0006）数据异常。",  # branch hit, tier miss
        agent_conclusion="",
    )
    assert score.dimension_recall == pytest.approx(0.5)


def test_dim_recall_zero_when_no_match(evaluator_with_questions: RCAEvaluator):
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="完全无关的叙述。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 0.0


# -------------------- concl_sim: jieba Jaccard --------------------


def test_concl_sim_chinese_matches_better_than_legacy_split(evaluator_with_questions: RCAEvaluator):
    """语义贴合的中文叙述应给出明显非零分；旧 .split() 几乎为 0。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="安鑫 90 天理财到期。",
        agent_conclusion="安鑫 90 天理财产品到期，导致活期存款下降。",
    )
    # 两句话有"安鑫"、"理财"、"到期"等多个公共 token
    assert score.conclusion_similarity > 0.3


def test_concl_sim_unrelated_text_near_zero(evaluator_with_questions: RCAEvaluator):
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="x",
        agent_conclusion="今天天气不错。",  # no overlap with expected_root_cause
    )
    assert score.conclusion_similarity <= 0.05


def test_concl_sim_empty_agent_conclusion_is_zero(evaluator_with_questions: RCAEvaluator):
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="",
        agent_conclusion="",
    )
    assert score.conclusion_similarity == 0.0


# ----------------- combined_score sanity --------------------------


def test_combined_score_with_alias_match_clears_event_plus_dim(
    evaluator_with_questions: RCAEvaluator,
):
    """event_hit ✓ + dim_recall=1.0 + concl_sim>0 → 应当 ≥ 0.7 通过线。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="本周「安鑫 90 天到期」触发赎回。",
        agent_conclusion="安鑫 90 天理财到期，活期存款下降。",
    )
    assert score.event_hit is True
    assert score.dimension_recall == 1.0
    assert score.combined_score >= 0.7


# ----------- enum aliases (Option A: static business-code ↔ Chinese map) -----------


def _make_eval_with_dim(evaluator_with_questions: RCAEvaluator, qid: str, dims: list[dict]):
    """Helper: patch evaluator_with_questions with a custom expected_dims for a question."""
    for q in evaluator_with_questions.questions:
        if q["id"] == qid:
            q["expected_affected_dimensions"] = dims
            return
    evaluator_with_questions.questions.append(
        {
            "id": qid,
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": dims,
            "expected_root_cause": "",
        }
    )


def test_enum_alias_customer_tier_chinese_hits(evaluator_with_questions: RCAEvaluator):
    _make_eval_with_dim(
        evaluator_with_questions, "Q_ENUM_TIER", [{"customer_tier": ["BASIC", "MASS"]}]
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_ENUM_TIER",
        agent_response="本次涉及大众客户群体的资金流动。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0  # "大众" matches MASS alias


def test_enum_alias_withdraw_zh_translation_hits(evaluator_with_questions: RCAEvaluator):
    _make_eval_with_dim(evaluator_with_questions, "Q_ENUM_TXN", [{"transaction_type": "WITHDRAW"}])
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_ENUM_TXN",
        agent_response="春节期间客户集中取现。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0  # "取现" matches WITHDRAW alias


def test_enum_alias_counter_zh_translation_hits(evaluator_with_questions: RCAEvaluator):
    _make_eval_with_dim(
        evaluator_with_questions, "Q_ENUM_CH", [{"transaction_channel": ["ATM", "COUNTER"]}]
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_ENUM_CH",
        agent_response="柜面渠道交易量显著上升。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0  # "柜面" matches COUNTER alias


def test_enum_alias_branch_id_city_name_hits(evaluator_with_questions: RCAEvaluator):
    _make_eval_with_dim(evaluator_with_questions, "Q_ENUM_BR", [{"branch_id": "BR_CITY_0006"}])
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_ENUM_BR",
        agent_response="上海地区客群响应明显。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0  # "上海" matches BR_CITY_0006 alias


def test_enum_alias_unknown_value_falls_back_to_literal(evaluator_with_questions: RCAEvaluator):
    """value 不在 enum 表里时，仍走裸字符串子串匹配（兜底）。"""
    _make_eval_with_dim(
        evaluator_with_questions, "Q_ENUM_UNK", [{"campaign_id": "CAMP_UNKNOWN_999"}]
    )
    score_hit = evaluator_with_questions.evaluate_response(
        question_id="Q_ENUM_UNK",
        agent_response="活动 CAMP_UNKNOWN_999 上线。",
        agent_conclusion="",
    )
    assert score_hit.dimension_recall == 1.0
    score_miss = evaluator_with_questions.evaluate_response(
        question_id="Q_ENUM_UNK",
        agent_response="活动上线。",
        agent_conclusion="",
    )
    assert score_miss.dimension_recall == 0.0


def test_enum_alias_combined_partial_hit(evaluator_with_questions: RCAEvaluator):
    """期望 3 个 dim，narrative 各种翻译命中 2/3 → dim_recall=0.667。"""
    _make_eval_with_dim(
        evaluator_with_questions,
        "Q_ENUM_MIX",
        [
            {"customer_tier": ["BASIC", "MASS"]},  # narrative 用"大众" → hit
            {"transaction_type": "WITHDRAW"},  # narrative 用"取现" → hit
            {"transaction_channel": ["ATM", "COUNTER"]},  # narrative 不提 → miss
        ],
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_ENUM_MIX",
        agent_response="大众客户在春节期间集中取现，对资金面形成扰动。",
        agent_conclusion="",
    )
    assert score.dimension_recall == pytest.approx(2 / 3, abs=0.01)


# ----------- R1: related_events (plural) support -----------


def test_related_events_plural_strict_match(evaluator_with_questions: RCAEvaluator):
    """YAML 用 related_events (plural list); agent_identified_event 是其中之一 → event_hit。"""
    evaluator_with_questions.questions.append(
        {
            "id": "Q_PLURAL",
            "related_events": ["anxin_90_expire"],  # plural form, no singular
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PLURAL",
        agent_response="",
        agent_identified_event="anxin_90_expire",
        agent_conclusion="",
    )
    assert score.event_hit is True


def test_related_events_plural_fuzzy_keyword_match(evaluator_with_questions: RCAEvaluator):
    """plural 形式下 fuzzy keyword 回退也要工作。"""
    evaluator_with_questions.questions.append(
        {
            "id": "Q_PLURAL_FUZZY",
            "related_events": ["anxin_90_expire"],
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PLURAL_FUZZY",
        agent_response="叙述里只提到了「安鑫」关键词。",
        agent_identified_event=None,  # narrator 没拿到 event_id
        agent_conclusion="",
    )
    assert score.event_hit is True


def test_related_event_singular_still_works(evaluator_with_questions: RCAEvaluator):
    """旧的单数写法保留兼容。"""
    evaluator_with_questions.questions.append(
        {
            "id": "Q_SINGULAR",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_SINGULAR",
        agent_response="",
        agent_identified_event="anxin_90_expire",
        agent_conclusion="",
    )
    assert score.event_hit is True


def test_no_related_event_at_all_gives_event_hit_false(evaluator_with_questions: RCAEvaluator):
    """两种 key 都没有 → event_hit 必须 False（不要意外通过）。"""
    evaluator_with_questions.questions.append(
        {
            "id": "Q_NONE",
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_NONE",
        agent_response="叙述里提到「安鑫」关键词。",
        agent_identified_event="anxin_90_expire",
        agent_conclusion="",
    )
    assert score.event_hit is False


# ----------- R3: date / period alias expansion -----------


def test_date_alias_iso_date_chinese_form_hits(evaluator_with_questions: RCAEvaluator):
    _make_eval_with_dim(evaluator_with_questions, "Q_DATE_ISO", [{"policy_date": "2026-06-20"}])
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_DATE_ISO",
        agent_response="6月20日发布的政策推动了贷款需求。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0  # "6月20日" matches via _date_aliases


def test_date_alias_quarter_hits(evaluator_with_questions: RCAEvaluator):
    _make_eval_with_dim(evaluator_with_questions, "Q_DATE_Q", [{"policy_date": "2026-06-20"}])
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_DATE_Q",
        agent_response="二季度 LPR 下调显著刺激了贷款。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0  # "二季度" matches via quarter alias


def test_period_alias_chinese_range_hits(evaluator_with_questions: RCAEvaluator):
    _make_eval_with_dim(
        evaluator_with_questions, "Q_PERIOD", [{"transaction_period": "2026-02-15 to 2026-02-23"}]
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PERIOD",
        agent_response="2月15-23日期间交易量显著上升。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0


def test_period_alias_chunyi_keyword_hits(evaluator_with_questions: RCAEvaluator):
    """春节时间窗口（2 月中下旬）应允许 narrative 用'春节'命中。"""
    _make_eval_with_dim(
        evaluator_with_questions, "Q_CHUNJIE", [{"transaction_period": "2026-02-15 to 2026-02-23"}]
    )
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_CHUNJIE",
        agent_response="春节假期客户集中提现。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 1.0


def test_date_alias_does_not_overmatch_non_date_values(evaluator_with_questions: RCAEvaluator):
    """非日期格式 value 不应被 _date_aliases 误展开。"""
    _make_eval_with_dim(evaluator_with_questions, "Q_PLAIN", [{"customer_tier": "随便一个值"}])
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PLAIN",
        agent_response="6月20日 二季度 春节 都提到了。",
        agent_conclusion="",
    )
    assert score.dimension_recall == 0.0  # 不该因 narrative 含日期就误判


# ----------- R4: concl_sim improvements (cut_for_search + stopwords) -----------


def test_tokenize_for_similarity_drops_stopwords():
    from chat_bi_agent.eval.rca_evaluator import _tokenize_for_similarity

    tokens = _tokenize_for_similarity("这是一个很重要的产品到期事件")
    # 停用词应被剔除
    assert "的" not in tokens
    assert "是" not in tokens
    # 关键业务词保留
    assert "产品" in tokens
    assert "到期" in tokens


def test_tokenize_for_similarity_finer_granularity_than_lcut():
    """cut_for_search 切复合词更细，给 Jaccard 更多命中机会。"""
    from chat_bi_agent.eval.rca_evaluator import _tokenize_for_similarity, _tokenize_zh

    text = "理财产品到期赎回数据分析"
    lcut_tokens = _tokenize_zh(text)
    search_tokens = _tokenize_for_similarity(text)
    # cut_for_search 应该至少切出和 lcut 一样多、或更多的 token
    # 关键：长复合词被进一步细分
    assert len(search_tokens) >= len(lcut_tokens)
    assert "理财" in search_tokens  # 复合词 "理财产品" 被进一步切出"理财"


def test_concl_sim_improvement_over_old_pipeline(evaluator_with_questions: RCAEvaluator):
    """半语义贴合的中文 narrative，新公式给的分应当 > 0.05（旧 baseline 上限）。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="安鑫 90 天到期事件。",
        agent_conclusion="安鑫 90 天理财到期，赎回触发活期下降。",
    )
    # expected_root_cause in Q_PRODUCT_ID fixture: "安鑫 90 天理财产品到期，导致活期存款下降。"
    # 公共 token: 安鑫/90/天/理财/到期/活期/下降 → 应当 ≥ 0.30
    assert score.conclusion_similarity >= 0.25


def test_concl_sim_unrelated_still_low(evaluator_with_questions: RCAEvaluator):
    """无关文本仍应给低分。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="",
        agent_conclusion="今天天气真好，适合出去散步。",
    )
    assert score.conclusion_similarity <= 0.1


# ----------- R5: hallucination — phantom product_id detection -----------


def test_hallucination_no_valid_set_skips_product_check(events_dir: Path):
    """未传 valid_product_ids 时，仅保留 >200% 的旧检测；编造产品 ID 不算幻觉。"""
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=False)  # 不传 valid_product_ids
    ev.questions = [
        {
            "id": "Q_HALLU_OFF",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_HALLU_OFF",
        agent_response="narrative 提到 PROD_FAKE_9999 这种不存在的产品。",
        agent_conclusion="",
    )
    assert score.hallucination_detected is False


def test_hallucination_phantom_product_id_flagged(events_dir: Path):
    """传 valid_product_ids 后，narrative 含未知 PROD_XXX_NNNN → 幻觉。"""
    ev = RCAEvaluator(
        events_dir=events_dir,
        valid_product_ids={"PROD_WEA_0030", "PROD_WEA_0031", "PROD_DEP_0005"},
        use_llm_judge=False,
    )
    ev.questions = [
        {
            "id": "Q_HALLU_ON",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_HALLU_ON",
        agent_response="narrative 引用了 PROD_FAKE_9999 这个不存在的产品。",
        agent_conclusion="",
    )
    assert score.hallucination_detected is True


def test_hallucination_only_valid_ids_no_flag(events_dir: Path):
    """narrative 全用合法 product_id → 无幻觉。"""
    ev = RCAEvaluator(
        events_dir=events_dir,
        valid_product_ids={"PROD_WEA_0030", "PROD_WEA_0031"},
        use_llm_judge=False,
    )
    ev.questions = [
        {
            "id": "Q_HALLU_OK",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_HALLU_OK",
        agent_response="narrative 引用 PROD_WEA_0030 和 PROD_WEA_0031 这两款。",
        agent_conclusion="",
    )
    assert score.hallucination_detected is False


def test_hallucination_number_check_still_works(events_dir: Path):
    """旧的 >200% 数字检测仍然生效，独立于 product_id 检测。"""
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=False)
    ev.questions = [
        {
            "id": "Q_HALLU_NUM",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_HALLU_NUM",
        agent_response="余额变化高达 350% — 明显失实。",
        agent_conclusion="",
    )
    assert score.hallucination_detected is True


# --- G-Eval LLM judge tests --------------------------------------------------


class _FakeChatResult:
    def __init__(self, content: str):
        self.content = content
        self.prompt_tokens = 0
        self.completion_tokens = 0


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content
        self.calls = 0
        self.last_user_prompt = ""

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.0):
        self.calls += 1
        self.last_user_prompt = user_prompt
        return _FakeChatResult(self._content)


def test_conclusion_similarity_uses_llm_judge_when_enabled(events_dir: Path):
    """LLM judge 返回有效 JSON → conclusion_similarity = 4 维平均，rubric 落到 score."""
    fake = _FakeLLM(
        "```json\n"
        '{"event_identification": 1.0, "quantification": 0.5, '
        '"mechanism": 0.75, "scope": 1.0}\n```'
    )
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=True, llm_client=fake)
    ev.questions = [
        {
            "id": "Q_JUDGE",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "安鑫 90 天理财到期导致高净值客户赎回，续作率 42%",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_JUDGE",
        agent_response="narrative ...",
        agent_conclusion="上海分行高净值客户活期存款下降 8.5%",
    )
    assert fake.calls == 1
    # avg = (1.0 + 0.5 + 0.75 + 1.0) / 4 = 0.8125
    assert abs(score.conclusion_similarity - 0.8125) < 1e-6
    assert score.conclusion_rubric is not None
    assert score.conclusion_rubric["event_identification"] == 1.0
    assert score.conclusion_rubric["method"] == "llm_judge"


def test_conclusion_similarity_falls_back_to_jaccard_on_judge_failure(events_dir: Path):
    """LLM judge 抛异常或返回无法解析 JSON → 回退 Jaccard，rubric=None."""

    class _BrokenLLM:
        def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.0):
            raise RuntimeError("simulated API failure")

    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=True, llm_client=_BrokenLLM())
    ev.questions = [
        {
            "id": "Q_FALLBACK",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "安鑫 90 天理财到期",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_FALLBACK",
        agent_response="...",
        agent_conclusion="安鑫 90 天理财到期触发赎回",
    )
    # 完全 fallback 到 Jaccard，rubric=None 表示走了 fallback 路径
    assert score.conclusion_rubric is None
    assert score.conclusion_similarity > 0.0  # Jaccard 命中 "安鑫"/"90"/"理财"/"到期"


def test_conclusion_similarity_use_llm_judge_false_uses_jaccard(events_dir: Path):
    """use_llm_judge=False 时不调 LLM，rubric=None."""
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=False)
    ev.questions = [
        {
            "id": "Q_NOJUDGE",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "安鑫 90 天到期",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_NOJUDGE",
        agent_response="...",
        agent_conclusion="安鑫到期",
    )
    assert score.conclusion_rubric is None


def test_llm_judge_injects_evaluation_criteria_and_key_metrics(events_dir: Path):
    """B 方案：question.evaluation_criteria + expected_key_metrics 注入 judge prompt。"""
    fake = _FakeLLM(
        '```json\n{"event_identification": 1.0, "quantification": 1.0, '
        '"mechanism": 1.0, "scope": 1.0}\n```'
    )
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=True, llm_client=fake)
    ev.questions = [
        {
            "id": "Q_RICH",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "安鑫 90 天到期",
            "evaluation_criteria": [
                {"product_identification": "Agent 是否精准定位到源产品和目标产品"},
                {"behavior_pattern": "Agent 是否解释了赎回→续作的行为链条"},
            ],
            "expected_key_metrics": [
                {
                    "metric": "retail_deposit_balance",
                    "change_direction": "down",
                    "magnitude": "~8.5%",
                }
            ],
        }
    ]
    ev.evaluate_response(
        question_id="Q_RICH",
        agent_response="...",
        agent_conclusion="安鑫 90 天理财到期触发赎回",
    )
    # criterion 键名 + 描述都在 prompt 里
    assert "product_identification" in fake.last_user_prompt
    assert "Agent 是否精准定位到源产品和目标产品" in fake.last_user_prompt
    # key_metrics 三元组在 prompt 里
    assert "retail_deposit_balance" in fake.last_user_prompt
    assert "down" in fake.last_user_prompt
    assert "~8.5%" in fake.last_user_prompt


def test_llm_judge_handles_question_without_optional_blocks(events_dir: Path):
    """没有 evaluation_criteria / expected_key_metrics 时 prompt 仍正常构造。"""
    fake = _FakeLLM(
        '```json\n{"event_identification": 1.0, "quantification": 0.5, '
        '"mechanism": 0.5, "scope": 0.5}\n```'
    )
    ev = RCAEvaluator(events_dir=events_dir, use_llm_judge=True, llm_client=fake)
    ev.questions = [
        {
            "id": "Q_MIN",
            "related_event": "anxin_90_expire",
            "expected_affected_dimensions": [],
            "expected_root_cause": "安鑫到期",
        }
    ]
    score = ev.evaluate_response(
        question_id="Q_MIN",
        agent_response="...",
        agent_conclusion="安鑫到期",
    )
    assert score.conclusion_rubric is not None
    assert "本题人工 rubric" not in fake.last_user_prompt
    assert "期望关键指标" not in fake.last_user_prompt
