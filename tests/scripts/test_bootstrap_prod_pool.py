"""bootstrap_prod_pool.py 单测：p1_eval 装载 / dedup / langfuse 失败降级。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "bootstrap_prod_pool", REPO_ROOT / "scripts" / "bootstrap_prod_pool.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

load_p1_eval = _mod.load_p1_eval
load_langfuse = _mod.load_langfuse
dedup_by_id = _mod.dedup_by_id


def _write_p1_yaml(path: Path, entries: list[dict]):
    path.write_text(
        yaml.safe_dump({"evaluation_questions": entries}, allow_unicode=True),
        encoding="utf-8",
    )


def _write_baseline(path: Path, per_question: list[dict]):
    path.write_text(
        json.dumps(
            {
                "baseline_id": "p2_validator_reflector",
                "total_questions": len(per_question),
                "per_question": per_question,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ---------------------- load_p1_eval ----------------------


def test_load_p1_eval_picks_up_pass_only(tmp_path: Path):
    yaml_path = tmp_path / "q.yaml"
    baseline_path = tmp_path / "b.json"
    _write_p1_yaml(
        yaml_path,
        [
            {"id": "q001", "question": "有多少客户？"},
            {"id": "q002", "question": "存款余额？"},
        ],
    )
    _write_baseline(
        baseline_path,
        [
            {"question_id": "q001", "score": 1.0, "sql": "SELECT COUNT(*) FROM dim_customer"},
            {
                "question_id": "q002",
                "score": 0.5,
                "sql": "SELECT SUM(balance) FROM fct_balance_daily",
            },
        ],
    )
    got = load_p1_eval(yaml_path, baseline_path, score_threshold=1.0)
    assert len(got) == 1
    assert got[0].question == "有多少客户？"
    assert got[0].sql == "SELECT COUNT(*) FROM dim_customer"
    assert got[0].dialect == "postgres"
    assert "prod" in got[0].tags
    assert "p1_eval_gold" in got[0].tags
    assert got[0].source.startswith("p1_eval_baseline:q001")


def test_load_p1_eval_missing_yaml_row_is_skipped(tmp_path: Path):
    yaml_path = tmp_path / "q.yaml"
    baseline_path = tmp_path / "b.json"
    _write_p1_yaml(yaml_path, [{"id": "q001", "question": "Q1"}])
    _write_baseline(
        baseline_path,
        [
            {"question_id": "q001", "score": 1.0, "sql": "SELECT 1"},
            {"question_id": "q999_orphan", "score": 1.0, "sql": "SELECT 2"},
        ],
    )
    got = load_p1_eval(yaml_path, baseline_path, score_threshold=1.0)
    assert len(got) == 1
    assert got[0].sql == "SELECT 1"


def test_load_p1_eval_no_sql_is_skipped(tmp_path: Path):
    yaml_path = tmp_path / "q.yaml"
    baseline_path = tmp_path / "b.json"
    _write_p1_yaml(yaml_path, [{"id": "q001", "question": "Q1"}])
    _write_baseline(
        baseline_path,
        [{"question_id": "q001", "score": 1.0, "sql": ""}],
    )
    assert load_p1_eval(yaml_path, baseline_path, 1.0) == []


def test_load_p1_eval_missing_file_returns_empty(tmp_path: Path):
    assert load_p1_eval(tmp_path / "no.yaml", tmp_path / "no.json", 1.0) == []


def test_example_id_deterministic_across_calls(tmp_path: Path):
    """两次 load 同一 (Q, SQL) 得同一 example_id → dedup 才有意义"""
    yaml_path = tmp_path / "q.yaml"
    baseline_path = tmp_path / "b.json"
    _write_p1_yaml(yaml_path, [{"id": "q1", "question": "Q?"}])
    _write_baseline(
        baseline_path,
        [{"question_id": "q1", "score": 1.0, "sql": "SELECT 1"}],
    )
    r1 = load_p1_eval(yaml_path, baseline_path, 1.0)
    r2 = load_p1_eval(yaml_path, baseline_path, 1.0)
    assert r1[0].example_id == r2[0].example_id


# ---------------------- dedup ----------------------


def test_dedup_by_id_keeps_first(tmp_path: Path):
    yaml_path = tmp_path / "q.yaml"
    baseline_path = tmp_path / "b.json"
    _write_p1_yaml(yaml_path, [{"id": "q1", "question": "Q?"}])
    _write_baseline(
        baseline_path,
        [{"question_id": "q1", "score": 1.0, "sql": "SELECT 1"}],
    )
    ex_list = load_p1_eval(yaml_path, baseline_path, 1.0)
    combined = ex_list + ex_list + ex_list  # 3x
    got = dedup_by_id(combined)
    assert len(got) == 1


# ---------------------- load_langfuse graceful degradation ----------------------


def test_load_langfuse_missing_env_returns_empty(monkeypatch):
    """没有 Langfuse key 环境变量 → get_client 抛错 → 返回空 list 且不炸。"""
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    # get_client 里如果 _client 已经初始化过（其他测试污染），我们需要 reset
    from chat_bi_agent.llm import langfuse_setup

    monkeypatch.setattr(langfuse_setup, "_client", None)
    got = load_langfuse(days_back=30, score_threshold=1.0)
    assert got == []


# ---------------------- resolve_pass_score：谁说了算 ----------------------
#
# 原实现是 `max(user_feedback, judge_pass)`，两个毛病：
#   1. judge_pass 全仓没有任何写入方，这个 or 提前给一个还不存在的 LLM judge
#      授了权——接上那天机器判的分能盖过人点的 👎
#   2. max 表达不了「改主意」：点完 👍 刷新页面再点 👎，纠正无效
# 现在只认 user_feedback，且**取最新的一条**。


class _Score:
    """Langfuse score 明细的最小替身（只用到 name/value/timestamp）。"""

    def __init__(self, name, value, timestamp=None):
        self.name = name
        self.value = value
        if timestamp is not None:
            self.timestamp = timestamp


resolve_pass_score = _mod.resolve_pass_score


def test_judge_pass_alone_no_longer_promotes():
    """judge_pass 不再算数——它现在没有写入方，将来也不该替人做主。"""
    assert resolve_pass_score([_Score("judge_pass", 1.0)]) == 0.0


def test_judge_pass_cannot_override_a_human_thumbs_down():
    scores = [
        _Score("user_feedback", 0.0, "2026-09-01T10:00:00Z"),
        _Score("judge_pass", 1.0, "2026-09-01T11:00:00Z"),
    ]
    assert resolve_pass_score(scores) == 0.0


def test_latest_feedback_wins_when_user_changes_mind_to_down():
    """👍 在先、👎 在后 → 0.0。max 在这里会返回 1.0，纠正被吞掉。"""
    scores = [
        _Score("user_feedback", 1.0, "2026-09-01T10:00:00Z"),
        _Score("user_feedback", 0.0, "2026-09-01T10:05:00Z"),
    ]
    assert resolve_pass_score(scores) == 0.0


def test_latest_feedback_wins_when_user_changes_mind_to_up():
    """反向也要成立——这是「取最新」而不是「一票否决」。"""
    scores = [
        _Score("user_feedback", 0.0, "2026-09-01T10:00:00Z"),
        _Score("user_feedback", 1.0, "2026-09-01T10:05:00Z"),
    ]
    assert resolve_pass_score(scores) == 1.0


def test_order_of_arrival_does_not_matter_only_timestamps_do():
    """API 不保证返回顺序，判定必须只看时间戳。"""
    scores = [
        _Score("user_feedback", 0.0, "2026-09-01T10:05:00Z"),
        _Score("user_feedback", 1.0, "2026-09-01T10:00:00Z"),
    ]
    assert resolve_pass_score(scores) == 0.0


def test_datetime_timestamps_work_alongside_iso_strings():
    """SDK 版本不同，timestamp 可能是 datetime 也可能是字符串，还可能不带时区。"""
    import datetime as _dt

    scores = [
        _Score("user_feedback", 1.0, _dt.datetime(2026, 9, 1, 10, 0)),  # naive
        _Score("user_feedback", 0.0, "2026-09-01T10:05:00+00:00"),
    ]
    assert resolve_pass_score(scores) == 0.0


def test_falls_back_to_arrival_order_when_no_timestamps():
    scores = [_Score("user_feedback", 1.0), _Score("user_feedback", 0.0)]
    assert resolve_pass_score(scores) == 0.0


def test_timestamped_scores_beat_untimestamped_ones():
    """有时间戳的才判得了先后，没有的只能垫底，不该靠位置赢过它们。"""
    scores = [
        _Score("user_feedback", 1.0, "2026-09-01T10:00:00Z"),
        _Score("user_feedback", 0.0),
    ]
    assert resolve_pass_score(scores) == 1.0


def test_no_feedback_at_all_is_not_a_pass():
    assert resolve_pass_score([]) == 0.0
    assert resolve_pass_score([_Score("other_metric", 1.0)]) == 0.0


def test_none_valued_scores_are_ignored():
    scores = [
        _Score("user_feedback", 1.0, "2026-09-01T10:00:00Z"),
        _Score("user_feedback", None, "2026-09-01T10:05:00Z"),
    ]
    assert resolve_pass_score(scores) == 1.0
