"""守门：崩掉的题必须在产物与报告里显式记账。

2026-08-17 实际发生：P2 跑 3 题，其中 2 题撞 embedding 端点 ConnectionError 从未执行
（runner 捕获异常后 `continue`）。但汇总打印的是：

    Total Questions: 3    Passed: 0    Avg Score: 0.631

那个 0.631 其实是唯一跑成的 q001 的分数——`total_questions` 在开跑前就设成 3，而
`avg_score` 只对成功评分的题求平均，**两个数字来自不同的分母**。`0/3` 又把「没通过」
和「压根没跑」混为一谈。差一点就把这个数字当成 B 档的结论写进 README。

失效是静默的：一次三分之二没跑成的运行，看起来跟一次完整运行完全一样，只是分数低些。
而"分数低些"恰恰是改评分器时预期会看到的现象，所以极易被当成预期结果接受。

产物里本有 `partial` 字段，但此前没有任何代码设置它——2026-06-07 那份的
`partial: true` 是人手写的。
"""

import json

import pytest


def _payload(total: int, errored: list[str], scored_avg: float) -> dict:
    scored = total - len(errored)
    return {
        "baseline_id": "p2_analysis_mvp",
        "total_questions": total,
        "scored_questions": scored,
        "errored_questions": errored,
        "partial": bool(errored),
        "passed_questions": 0,
        "pass_rate": 0.0,
        "avg_score": scored_avg,
        "per_question": [
            {"question_id": q, "agent_exception": "ConnectionError: ..."} for q in errored
        ],
    }


class TestPayloadAccounting:
    def test_partial_flag_set_when_questions_error(self):
        p = _payload(3, ["q002", "q003"], 0.631)
        assert p["partial"] is True
        assert p["scored_questions"] == 1
        assert p["scored_questions"] != p["total_questions"], "分母不同必须可辨"

    def test_clean_run_is_not_partial(self):
        p = _payload(3, [], 0.798)
        assert p["partial"] is False
        assert p["scored_questions"] == p["total_questions"]


class TestRunnerEmitsAccounting:
    """runner 源码层面的契约——避免字段被后来的改动悄悄去掉。"""

    @pytest.mark.parametrize("field", ["scored_questions", "errored_questions", "partial"])
    def test_p2_runner_emits_field(self, field):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "chat_bi_agent"
            / "runners"
            / "run_p2_eval.py"
        ).read_text(encoding="utf-8")
        assert f'"{field}"' in src, (
            f"run_p2_eval 的 payload 缺 {field}——崩掉的题将无法与「跑了但没通过」区分，"
            f"一次大半没跑成的运行会看起来像完整运行。"
        )

    def test_report_surfaces_partial_runs(self):
        """产物里记了账，报告上也必须看得见，否则等于没记。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2] / "scripts" / "run_all_evals.py").read_text(
            encoding="utf-8"
        )
        assert "errored_questions" in src, "run_all_evals 未读取 errored_questions，报告看不出残缺"


def test_current_p2_artifact_is_honest_about_itself():
    """回归：2026-08-17 那份产物必须自带 partial 标记（它确实是残缺的）。"""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "results" / "baseline_p2_analysis_2026-08-17.json"
    if not path.exists():
        pytest.skip("该产物不在仓库中")
    d = json.loads(path.read_text(encoding="utf-8"))
    errored = [q["question_id"] for q in d["per_question"] if "agent_exception" in q]
    if errored:
        assert d.get("partial") is True, (
            f"产物有 {len(errored)} 题 agent_exception 却未标 partial，"
            f"读者会把 avg_score 当成整批结果"
        )
