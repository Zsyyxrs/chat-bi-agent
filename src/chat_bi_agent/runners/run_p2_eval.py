"""P2 baseline eval: run 8 multi-step analysis questions → P2Agent → evaluator.

Run:
    python -m chat_bi_agent.runners.run_p2_eval

Output:
    - Per-question score + summary printed to stdout
    - Langfuse trace per question
    - results/baseline_p2_analysis_<DATE>.json
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

from langfuse import observe  # noqa: E402

from chat_bi_agent.agents.p1_nl2sql_agent import P1NL2SQLAgent  # noqa: E402
from chat_bi_agent.agents.p2 import P2MultiStepAnalysisAgent  # noqa: E402
from chat_bi_agent.eval.multi_step_analysis_evaluator import (  # noqa: E402
    AnalysisEvaluation,
    MultiStepAnalysisEvaluator,
)
from chat_bi_agent.llm.langfuse_setup import flush, get_client  # noqa: E402

YAML_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "multi_step_analysis_evaluation.yaml"
)

OUTPUT_DATE = "2026-06-07"


def load_questions() -> dict[str, dict]:
    with open(YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {q["id"]: q for q in data["evaluation_questions"]}


@observe(name="p2_eval_batch")
def main() -> int:
    get_client()
    questions = load_questions()
    qids = sorted(questions.keys())

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
            per_question.append({
                "question_id": qid,
                "agent_exception": f"{type(e).__name__}: {e}",
            })
            continue

        eval_input = report.to_eval_input()
        score = evaluator.evaluate_response(**eval_input)

        print(f"  Plan: {len(report.plan.steps)} steps, "
              f"replan={report.replan_count}, "
              f"skipped={sum(1 for s in report.step_results if s.skipped)}")
        print(f"  Facts: {len(report.facts)}, Insights: {len(report.insights)}")
        print(f"  Latency: {report.total_latency_ms:.0f}ms")
        print(f"  Score: {score.combined_score:.3f} "
              f"(step={score.step_completeness:.2f} "
              f"metric={score.multi_metric_coverage:.2f} "
              f"insight={score.insight_accuracy:.2f} "
              f"reason={score.reasoning_quality:.2f} "
              f"biz={score.business_relevance:.2f})")

        evaluation.scores.append(score)
        if score.combined_score >= 0.7:
            evaluation.passed_questions += 1

        per_question.append({
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
                "reasoning_quality": round(score.reasoning_quality, 4),
                "business_relevance": round(score.business_relevance, 4),
            },
            "final_answer_preview": report.final_answer[:200],
        })

    print()
    print(evaluation.summary())
    print(f"Pass Rate: {evaluation.pass_rate:.1%}")
    print(f"Avg Score: {evaluation.avg_score:.3f}")

    out_path = (
        Path(__file__).resolve().parents[3]
        / "results" / f"baseline_p2_analysis_{OUTPUT_DATE}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "baseline_id": "p2_analysis_mvp",
        "ran_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "total_questions": evaluation.total_questions,
        "passed_questions": evaluation.passed_questions,
        "pass_rate": round(evaluation.pass_rate, 4),
        "avg_score": round(evaluation.avg_score, 4),
        "per_question": per_question,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote baseline JSON → {out_path}")

    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        flush()
    sys.exit(exit_code)
