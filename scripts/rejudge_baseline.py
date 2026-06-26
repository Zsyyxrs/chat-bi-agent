"""Re-run LLM judge on an existing baseline's narratives (no P3 generation).

Usage:
    python scripts/rejudge_baseline.py [--baseline results/baseline_p3_rca_2026-06-25.json]

Reads full narrative from baseline JSON, re-runs RCAEvaluator._llm_judge_conclusion
with the *current* prompt in src code. Prints per-question diff vs original rubric.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "src"))

from chat_bi_agent.eval.rca_evaluator import RCAEvaluator  # noqa: E402

DATA_YAML = ROOT / "src/chat_bi_agent/data/attribution_evaluation.yaml"
DEFAULT_BASELINE = ROOT / "results/baseline_p3_rca_2026-06-25.json"


def load_questions() -> dict[str, dict]:
    with open(DATA_YAML, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {q["id"]: q for q in data.get("evaluation_questions", [])}


def main(baseline_path: Path) -> int:
    qs = load_questions()
    with open(baseline_path, encoding="utf-8") as f:
        baseline = json.load(f)

    evaluator = RCAEvaluator()
    rows = []
    sum_old = sum_new = 0.0
    n = 0

    print(f"Rejudging {baseline_path.name}  (ran_at={baseline.get('ran_at', '?')})")
    print(f"Original avg: {baseline.get('avg_score', 0):.3f}\n")

    print(f"{'qid':<20} {'old quant':>10} {'new quant':>10} {'Δ':>8}   old→new rubric")
    print("-" * 95)

    for q in baseline["per_question"]:
        qid = q["question_id"]
        narrative = q.get("narrative") or q.get("narrative_preview") or ""
        if not narrative.strip():
            print(f"{qid:<20}  (no narrative — skipped)")
            continue
        question = qs.get(qid)
        if not question:
            print(f"{qid:<20}  (yaml miss — skipped)")
            continue
        expected = question.get("expected_root_cause", "").strip()
        old_rubric = q.get("sub_scores", {}).get("conclusion_rubric") or {}

        try:
            new_avg, new_rubric = evaluator._llm_judge_conclusion(
                expected=expected, agent=narrative, question=question
            )
        except Exception as exc:
            print(f"{qid:<20}  judge exception: {exc!r}")
            continue

        old_q = old_rubric.get("quantification", float("nan"))
        new_q = (new_rubric or {}).get("quantification", float("nan"))
        sum_old += (
            sum(
                old_rubric.get(d, 0.0)
                for d in ("event_identification", "quantification", "mechanism", "scope")
            )
            / 4
            if old_rubric
            else 0
        )
        sum_new += new_avg
        n += 1

        def fmt_rubric(r):
            if not r:
                return "—"
            return (
                f"E{r.get('event_identification', 0):.2f}"
                f" Q{r.get('quantification', 0):.2f}"
                f" M{r.get('mechanism', 0):.2f}"
                f" S{r.get('scope', 0):.2f}"
            )

        print(
            f"{qid:<20} {old_q:>10.2f} {new_q:>10.2f} {(new_q - old_q):>+8.2f}   "
            f"{fmt_rubric(old_rubric)} → {fmt_rubric(new_rubric)}"
        )
        rows.append({"qid": qid, "old": old_rubric, "new": new_rubric})

    print()
    if n:
        print(
            f"avg conclusion_rubric: {sum_old / n:.3f} → {sum_new / n:.3f}  Δ {(sum_new - sum_old) / n:+.3f}"
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()
    sys.exit(main(args.baseline))
