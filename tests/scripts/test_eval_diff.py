"""单测 scripts/eval_diff.py 的纯函数 compute_diff / _question_score。"""

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "eval_diff.py"
_spec = importlib.util.spec_from_file_location("eval_diff", _SCRIPT_PATH)
eval_diff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_diff)


def test_question_score_handles_flat_float():
    assert eval_diff._question_score({"score": 0.8}) == 0.8


def test_question_score_handles_nested_overall():
    assert eval_diff._question_score({"score": {"overall_score": 0.75, "other": 0.1}}) == 0.75


def test_question_score_missing_returns_zero():
    assert eval_diff._question_score({}) == 0.0
    assert eval_diff._question_score({"score": "not-a-number"}) == 0.0


def test_compute_diff_basic_delta():
    prev = {"per_question": [{"question_id": "q1", "score": 0.8}]}
    curr = {"per_question": [{"question_id": "q1", "score": 0.6}]}
    [row] = eval_diff.compute_diff(prev, curr)
    assert row["question_id"] == "q1"
    assert row["delta"] == -0.2
    assert row["status"] == "same"


def test_compute_diff_marks_new_and_removed():
    prev = {"per_question": [{"question_id": "q_old", "score": 0.5}]}
    curr = {"per_question": [{"question_id": "q_new", "score": 0.9}]}
    rows = {r["question_id"]: r for r in eval_diff.compute_diff(prev, curr)}
    assert rows["q_old"]["status"] == "removed"
    assert rows["q_new"]["status"] == "new"
    assert rows["q_old"]["delta"] is None
    assert rows["q_new"]["delta"] is None


def test_compute_diff_handles_nested_score_schema():
    """P2/P3 baseline 把分数嵌在 dict 里。"""
    prev = {"per_question": [{"question_id": "q1", "score": {"overall_score": 0.7}}]}
    curr = {"per_question": [{"question_id": "q1", "score": {"overall_score": 0.85}}]}
    [row] = eval_diff.compute_diff(prev, curr)
    assert row["delta"] == 0.15
