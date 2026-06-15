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
    """Construct RCAEvaluator with stubbed questions list (avoids dependence on attribution_evaluation.yaml)."""
    ev = RCAEvaluator(events_dir=events_dir)
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
    ev = RCAEvaluator(events_dir=events_dir)
    assert "安鑫 90 天到期" in ev._dim_aliases["PROD_WEA_0030"]
    # Also the no-space variant
    assert "安鑫90天到期" in ev._dim_aliases["PROD_WEA_0030"]


def test_alias_map_covers_related_products_from_propagation(events_dir: Path):
    ev = RCAEvaluator(events_dir=events_dir)
    assert "PROD_WEA_0032" in ev._dim_aliases
    assert "安鑫 90 天到期" in ev._dim_aliases["PROD_WEA_0032"]


def test_alias_map_missing_events_dir_returns_empty(tmp_path: Path):
    ev = RCAEvaluator(events_dir=tmp_path / "nope")
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

def test_combined_score_with_alias_match_clears_event_plus_dim(evaluator_with_questions: RCAEvaluator):
    """event_hit ✓ + dim_recall=1.0 + concl_sim>0 → 应当 ≥ 0.7 通过线。"""
    score = evaluator_with_questions.evaluate_response(
        question_id="Q_PRODUCT_ID",
        agent_response="本周「安鑫 90 天到期」触发赎回。",
        agent_conclusion="安鑫 90 天理财到期，活期存款下降。",
    )
    assert score.event_hit is True
    assert score.dimension_recall == 1.0
    assert score.combined_score >= 0.7
