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
