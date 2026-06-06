"""Phase 5: 跑 6 题 happy path → P1Agent → PrecisionRetrievalEvaluator → 汇总。

运行：
    python -m chat_bi_agent.runners.run_p1_eval

输出：
    控制台打印每题 score + pass_rate + avg_score
    Langfuse 中每题一条 trace
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
from chat_bi_agent.eval.precision_retrieval_evaluator import (  # noqa: E402
    PrecisionEvaluation,
    PrecisionRetrievalEvaluator,
)
from chat_bi_agent.llm.langfuse_setup import flush, get_client  # noqa: E402

HAPPY_PATH_IDS = ["precision_q001", "precision_q002", "precision_q003",
                  "precision_q004", "precision_q006", "precision_q007"]

YAML_PATH = Path(__file__).resolve().parents[1] / "data" / "precision_retrieval_evaluation.yaml"


def load_questions() -> dict[str, dict]:
    with open(YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {q["id"]: q for q in data["evaluation_questions"]}


@observe(name="p2_eval_batch")
def main() -> int:
    get_client()
    questions = load_questions()
    agent = P1NL2SQLAgent(top_k=4)
    evaluator = PrecisionRetrievalEvaluator()

    evaluation = PrecisionEvaluation()
    evaluation.total_questions = len(HAPPY_PATH_IDS)

    print("=" * 64)
    print("Baseline Eval (Validator + Reflector)")
    print("=" * 64)

    per_question: list[dict] = []

    for qid in HAPPY_PATH_IDS:
        q = questions[qid]
        question_text = q["question"].strip()
        print(f"\n--- {qid} ---")
        print(f"Q: {question_text[:80]}...")

        agent_result = agent.run(question_id=qid, question=question_text)
        print(f"  SQL: {(agent_result.sql or '<NONE>')[:120]}")
        print(f"  Rows: {len(agent_result.rows) if agent_result.rows else 0}")
        print(f"  Attempts: {agent_result.attempts}, Latency: {agent_result.total_latency_ms}ms")
        if agent_result.error_class:
            print(f"  ErrorClass: {agent_result.error_class.value}")
        if agent_result.reflect_history:
            print(f"  ReflectHistory: {agent_result.reflect_history}")

        score = evaluator.evaluate_response(
            question_id=qid,
            generated_sql=agent_result.sql or "",
            actual_results=agent_result.rows or [],
            execution_error=agent_result.execution_error,
        )
        print(f"  Score: {score.combined_score:.3f}")

        evaluation.scores.append(score)
        if score.combined_score >= 0.7:
            evaluation.passed_questions += 1

        per_question.append({
            "question_id": qid,
            "rows": len(agent_result.rows) if agent_result.rows else 0,
            "attempts": agent_result.attempts,
            "latency_ms": agent_result.total_latency_ms,
            "score": round(score.combined_score, 4),
            "error_class": agent_result.error_class.value if agent_result.error_class else None,
            "reflect_history": agent_result.reflect_history,
            "sql": agent_result.sql,
        })

    print()
    print(evaluation.summary())
    print(f"Pass Rate: {evaluation.pass_rate:.1%}")
    print(f"Avg Score: {evaluation.avg_score:.3f}")

    out_path = Path(__file__).resolve().parents[3] / "results" / \
        "baseline_p2_validator_reflector_2026-06-03.json"
    payload = {
        "baseline_id": "p2_validator_reflector",
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
