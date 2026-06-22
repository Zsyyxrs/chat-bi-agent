"""P3 baseline eval: run attribution questions → P3 RCA Agent → RCAEvaluator.

Run:
    python -m chat_bi_agent.runners.run_p3_eval [--limit N]

Output:
    - Per-question score + summary printed to stdout
    - Langfuse trace per question (via @observe in P3 orchestrator)
    - results/p3_rca_baseline_<DATE>.json
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
from chat_bi_agent.agents.p3 import P3RootCauseAnalysisAgent  # noqa: E402
from chat_bi_agent.eval.rca_evaluator import RCAEvaluator  # noqa: E402
from chat_bi_agent.llm import qwen_client  # noqa: E402
from chat_bi_agent.llm.langfuse_setup import flush, get_client  # noqa: E402

DATA_YAML = Path(__file__).resolve().parents[1] / "data" / "attribution_evaluation.yaml"
EVENTS_DIR = Path(__file__).resolve().parents[1] / "data" / "events"
RESULTS_DIR = Path(__file__).resolve().parents[3] / "results"

OUTPUT_DATE = datetime.now(UTC).strftime("%Y-%m-%d")


def load_questions() -> list[dict]:
    with open(DATA_YAML, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("evaluation_questions", [])


@observe(name="p3_eval_batch")
def main(limit: int | None = None, only_qid: str | None = None) -> int:
    get_client()
    questions = load_questions()
    if only_qid:
        questions = [q for q in questions if q.get("id") == only_qid]
        if not questions:
            raise SystemExit(f"未找到 question_id={only_qid}")
    elif limit is not None:
        questions = questions[:limit]

    p1 = P1NL2SQLAgent(top_k=4)
    agent = P3RootCauseAnalysisAgent(
        p1_agent=p1,
        llm_client=qwen_client,
        events_dir=EVENTS_DIR,
    )
    evaluator = RCAEvaluator()

    print("=" * 64)
    print("P3 RCA Eval (Root Cause Analysis MVP)")
    print("=" * 64)

    per_question: list[dict] = []
    pass_count = 0
    event_hit_count = 0
    score_sum = 0.0

    for q in questions:
        qid = q["id"]
        question_text = q["question"].strip()
        print(f"\n--- {qid} ---")
        print(f"Q: {question_text[:100]}...")

        try:
            report = agent.run(question_id=qid, question=question_text)
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
        try:
            score = evaluator.evaluate_response(**eval_input)
        except Exception as e:
            print(f"  EVAL EXCEPTION: {type(e).__name__}: {e}")
            per_question.append(
                {
                    "question_id": qid,
                    "eval_exception": f"{type(e).__name__}: {e}",
                    "narrative": report.narrative,
                }
            )
            continue

        combined = score.combined_score
        score_sum += combined
        if combined >= 0.7:
            pass_count += 1
        if score.event_hit:
            event_hit_count += 1

        print(
            f"  Drills: {len(report.drill_results)} "
            f"(skipped={sum(1 for d in report.drill_results if d.skipped)})"
        )
        # 每个 drill 落详细诊断：NL、SQL 头、行数、error_class
        for i, dr in enumerate(report.drill_results):
            sql_preview = (dr.sql or "").replace("\n", " ").strip()[:160]
            print(
                f"    drill[{i}] dim={dr.dimension} skipped={dr.skipped} "
                f"rows={len(dr.rows)} err_class={dr.error_class} "
                f"top_k={len(dr.pareto_top_k)} sql={sql_preview!r}"
            )
            print(f"      nl: {dr.nl_question[:200]}")
        print(f"  Events matched: {[ev.event_id for ev in report.matched_events]}")
        print(f"  Latency: {report.latency_ms}ms")
        print(
            f"  Score: {combined:.3f} "
            f"(event_hit={score.event_hit} "
            f"dim_recall={score.dimension_recall:.2f} "
            f"concl_sim={score.conclusion_similarity:.2f} "
            f"hallu={score.hallucination_detected})"
        )

        per_question.append(
            {
                "question_id": qid,
                "drill_dimensions": [dr.dimension for dr in report.drill_results],
                "skipped_drills": sum(1 for d in report.drill_results if d.skipped),
                "drill_details": [
                    {
                        "dimension": dr.dimension,
                        "nl_question": dr.nl_question,
                        "sql": dr.sql,
                        "row_count": len(dr.rows),
                        "first_row": dr.rows[0] if dr.rows else None,
                        "pareto_top_k_count": len(dr.pareto_top_k),
                        "error_class": dr.error_class,
                        "skipped": dr.skipped,
                    }
                    for dr in report.drill_results
                ],
                "matched_event_ids": [ev.event_id for ev in report.matched_events],
                "latency_ms": report.latency_ms,
                "score": round(combined, 4),
                "sub_scores": {
                    "event_hit": bool(score.event_hit),
                    "dimension_recall": round(score.dimension_recall, 4),
                    "conclusion_similarity": round(score.conclusion_similarity, 4),
                    "conclusion_rubric": score.conclusion_rubric,
                    "hallucination_detected": bool(score.hallucination_detected),
                },
                "narrative_preview": report.narrative[:200],
                "error": report.error,
            }
        )

    total = len(questions)
    avg_score = score_sum / total if total else 0.0
    print()
    print("=" * 64)
    print(f"Total: {total}  Passed (>=0.7): {pass_count}  Event-hit: {event_hit_count}")
    print(f"Avg Score: {avg_score:.3f}")
    print("=" * 64)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"baseline_p3_rca_{OUTPUT_DATE}.json"
    payload = {
        "baseline_id": "p3_rca_mvp",
        "ran_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "total_questions": total,
        "passed_questions": pass_count,
        "event_hit_count": event_hit_count,
        "avg_score": round(avg_score, 4),
        "per_question": per_question,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
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
