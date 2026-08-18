"""P2 baseline eval: run 8 multi-step analysis questions → P2Agent → evaluator.

Run:
    python -m chat_bi_agent.runners.run_p2_eval

Output:
    - Per-question score + summary printed to stdout
    - Langfuse trace per question
    - results/baseline_p2_analysis_<DATE>.json
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

from langfuse import observe  # noqa: E402

from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent  # noqa: E402
from chat_bi_agent.agents.p2 import P2MultiStepAnalysisAgent  # noqa: E402
from chat_bi_agent.eval.multi_step_analysis_evaluator import (  # noqa: E402
    JUDGE_DIMS,
    AnalysisEvaluation,
    MultiStepAnalysisEvaluator,
)
from chat_bi_agent.llm.langfuse_setup import flush, get_client  # noqa: E402

YAML_PATH = Path(__file__).resolve().parents[1] / "data" / "multi_step_analysis_evaluation.yaml"

# 与 run_p1_eval / run_p3_eval 一致取当天日期。此处曾长期硬编码为 "2026-06-07"，
# 后果是**每一次 P2 跑批都覆盖那份历史 baseline**，而且提示信息里印的还是旧日期，
# 不会有任何人察觉自己刚销毁了对照组。2026-08-17 复跑时实际触发，靠 git 才救回来。
OUTPUT_DATE = datetime.now(UTC).strftime("%Y-%m-%d")


def build_question_row(qid: str, report, score, eval_input: dict) -> dict:
    """组装单题的产物行。与 `build_payload` 同理，单独成函数是为了让守门能直接调它。

    `report` / `score` 只按属性取值，测试可传轻量替身，不必跑 agent（单题 300~500s）。
    """
    return {
        "question_id": qid,
        "plan_type": report.plan.plan_type,
        "step_count": len(report.plan.steps),
        "skipped_steps": sum(1 for s in report.step_results if s.skipped),
        "fact_count": len(report.facts),
        "insight_count": len(report.insights),
        "replan_count": report.replan_count,
        "latency_ms": round(report.total_latency_ms, 0),
        "score": round(score.combined_score, 4),
        "sub_scores": {
            "step_completeness": round(score.step_completeness, 4),
            "multi_metric_coverage": round(score.multi_metric_coverage, 4),
            "insight_accuracy": round(score.insight_accuracy, 4),
            # 诊断字段，不计分（ADR-015/016）
            "reasoning_quality": round(score.reasoning_quality, 4),
            "business_relevance": round(score.business_relevance, 4),
            # rubric 子分原样落盘（照 P3 的 conclusion_rubric）：既让判分事后可复核，
            # 也让 replay 能复用它精确重放总分而不必再花钱重判。
            # None 表示 judge 未判，该题总分口径是确定性维度归一。
            "analysis_rubric": score.analysis_rubric,
        },
        "rubric_unavailable": not score.rubric_available,
        "final_answer_preview": report.final_answer[:200],
        # 评分器的完整入参原样落盘，喂回 evaluate_response(**eval_input) 即可精确重放。
        # 此前只存 200 字预览（评分用的却是完整回答），既无法事后复核「这分打得对不对」，
        # 也让每次改评分器都得重跑一轮。P3 早就同时存完整 narrative 与 preview。
        "eval_input": eval_input,
    }


def build_payload(
    per_question: list[dict],
    total_questions: int,
    passed_questions: int,
    avg_score: float,
) -> dict:
    """组装落盘 payload。

    **单独成函数是为了让守门测试能直接调它**。此前守门写的是
    `assert '"errored_questions"' in 源码文本`——2026-08-18 实测：把真实字段行删掉、
    只留上方注释，3 个守门测试**全绿**，因为字段名在注释里也出现。那是「声称在守门、
    实际没守」，比原缺陷更隐蔽：有测试、还是绿的，反而让人以为已经防住了。

    崩掉的题必须显式记账。此前 total_questions 在开跑前就设成 len(qids)，异常时
    `continue` 跳过、不进 scores，而 avg_score 只对成功评分的题求平均——两个数字
    来自不同的分母。2026-08-17 实测：3 题里 2 题撞 embedding 端点 ConnectionError
    从未执行，汇总却打印「Total 3 / Passed 0 / Avg 0.631」，那个 0.631 其实是
    q001 一题的分数，而 0/3 把「没通过」和「压根没跑」混为一谈。
    产物里的 partial 字段此前无代码设置，2026-06-07 那份的 partial=true 是人手写的。

    judge 降级同样必须记账：这些题的总分是「确定性维度归一」，与有 rubric 的题不同
    口径。不标出来的话，judge 大面积失败的一轮与正常一轮产物同形，只是分数分布悄悄变了。
    """
    from chat_bi_agent.eval.run_metadata import build_run_metadata

    errored = [q["question_id"] for q in per_question if "agent_exception" in q]
    scored = total_questions - len(errored)
    rubric_missing = [q["question_id"] for q in per_question if q.get("rubric_unavailable")]
    return {
        "baseline_id": "p2_analysis_mvp",
        "ran_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_metadata": build_run_metadata(),
        "total_questions": total_questions,
        "scored_questions": scored,
        "errored_questions": errored,
        "partial": bool(errored),
        "rubric_unavailable_questions": rubric_missing,
        "passed_questions": passed_questions,
        # 保留原字段名：run_all_evals.py 与 eval_diff.py 直接按 d['avg_score'] 取值
        # 且无 .get 兜底，改名会让报告生成 KeyError。口径由上面的 scored/partial 说明。
        # 注意分母是成功评分的题数，不是 total_questions——partial=true 时二者不同。
        "pass_rate": round(passed_questions / scored, 4) if scored else 0.0,
        "avg_score": round(avg_score, 4),
        "per_question": per_question,
    }


def load_questions() -> dict[str, dict]:
    with open(YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {q["id"]: q for q in data["evaluation_questions"]}


@observe(name="p2_eval_batch")
def main(limit: int | None = None, only_qid: str | None = None) -> int:
    get_client()
    questions = load_questions()
    qids = sorted(questions.keys())
    # 与 run_p3_eval 的 --limit/--qid 对齐。P2 单题 300~500s，跑全 8 题要一小时，
    # 没有子集开关时无法只复跑与历史 baseline 可比的那几题。
    if only_qid is not None:
        qids = [only_qid] if only_qid in questions else []
    elif limit is not None:
        qids = qids[:limit]

    p1 = P1NL2SQLAgent(top_k=4)
    p2 = P2MultiStepAnalysisAgent(
        p1_agent=p1,
        schema_linker=p1.schema_linker,
        loader=p1.loader,
        top_k=8,
    )
    evaluator = MultiStepAnalysisEvaluator()

    evaluation = AnalysisEvaluation()
    evaluation.total_questions = len(qids)

    print("=" * 64)
    print("P2 Multi-step Analysis Eval (Plan-and-Execute MVP)")
    print("=" * 64)

    per_question: list[dict] = []

    for qid in qids:
        q = questions[qid]
        question_text = q["question"].strip()
        print(f"\n--- {qid} ---")
        print(f"Q: {question_text[:100]}...")

        try:
            report = p2.run(question_id=qid, question=question_text)
        except Exception as e:
            print(f"  AGENT EXCEPTION: {type(e).__name__}: {e}")
            per_question.append(
                {
                    "question_id": qid,
                    "agent_exception": f"{type(e).__name__}: {e}",
                }
            )
            continue

        eval_input = report.to_eval_input()
        score = evaluator.evaluate_response(**eval_input)

        print(
            f"  Plan: {len(report.plan.steps)} steps, "
            f"replan={report.replan_count}, "
            f"skipped={sum(1 for s in report.step_results if s.skipped)}"
        )
        print(f"  Facts: {len(report.facts)}, Insights: {len(report.insights)}")
        print(f"  Latency: {report.total_latency_ms:.0f}ms")
        rubric_str = (
            " ".join(f"{d.split('_')[0]}={score.analysis_rubric[d]:.2f}" for d in JUDGE_DIMS)
            if score.rubric_available
            else "judge 未判（该维退出计分）"
        )
        rubric_avg = "—" if score.rubric_score is None else f"{score.rubric_score:.2f}"
        print(
            f"  Score: {score.combined_score:.3f} "
            f"(step={score.step_completeness:.2f} "
            f"metric={score.multi_metric_coverage:.2f} "
            f"insight={score.insight_accuracy:.2f} "
            f"rubric={rubric_avg})"
        )
        print(f"    rubric: {rubric_str}")
        print(
            f"    诊断（不计分）: reason={score.reasoning_quality:.2f} "
            f"biz={score.business_relevance:.2f}"
        )

        evaluation.scores.append(score)
        if score.combined_score >= 0.7:
            evaluation.passed_questions += 1

        per_question.append(build_question_row(qid, report, score, eval_input))

    print()
    print(evaluation.summary())
    print(f"Pass Rate: {evaluation.pass_rate:.1%}")
    print(f"Avg Score: {evaluation.avg_score:.3f}")

    out_path = (
        Path(__file__).resolve().parents[3] / "results" / f"baseline_p2_analysis_{OUTPUT_DATE}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = build_payload(
        per_question=per_question,
        total_questions=evaluation.total_questions,
        passed_questions=evaluation.passed_questions,
        avg_score=evaluation.avg_score,
    )
    if payload["errored_questions"]:
        print(
            f"\n⚠️  {len(payload['errored_questions'])}/{payload['total_questions']} "
            f"题未执行（agent 异常）：{', '.join(payload['errored_questions'])}\n"
            f"    avg/pass_rate 的分母是成功评分的 {payload['scored_questions']} 题，"
            f"不代表整批结果。"
        )
    if payload["rubric_unavailable_questions"]:
        print(
            f"\n⚠️  {len(payload['rubric_unavailable_questions'])}/{payload['scored_questions']} "
            f"题的 rubric judge 未判：{', '.join(payload['rubric_unavailable_questions'])}\n"
            f"    这些题的总分按确定性三维归一，与其余题不同口径，不可直接对比。"
        )
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote baseline JSON → {out_path}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Run first N questions only")
    parser.add_argument(
        "--qid", type=str, default=None, help="Run only this question_id (overrides --limit)"
    )
    args = parser.parse_args()
    try:
        exit_code = main(limit=args.limit, only_qid=args.qid)
    finally:
        flush()
    sys.exit(exit_code)
