#!/usr/bin/env python3
"""Run BIRD-financial NL2SQL evaluation and dump results to JSON.

Usage:
    python scripts/run_bird_financial.py                  # all 106 questions
    python scripts/run_bird_financial.py --limit 3        # smoke test
    python scripts/run_bird_financial.py --difficulty challenging
    python scripts/run_bird_financial.py --output results/bird_financial_2026-07-01.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from chat_bi_agent.config import CHAT_MODEL  # noqa: E402
from chat_bi_agent.eval.bird_financial.loader import (  # noqa: E402
    load_financial_questions,
    load_tied_append,
)
from chat_bi_agent.eval.bird_financial.runner import BirdRunner  # noqa: E402
from chat_bi_agent.eval.bird_financial.schema_prompt import (  # noqa: E402
    build_financial_schema_block,
)
from chat_bi_agent.eval.bird_financial.sqlite_executor import BirdSQLiteExecutor  # noqa: E402

BIRD_DIR = REPO_ROOT / "benchmarks" / "bird"


def _default_output() -> Path:
    date = dt.date.today().isoformat()
    return REPO_ROOT / "results" / f"bird_financial_{date}.json"


def _summarize_dicts(outcomes: list[dict]) -> dict:
    """Recompute summary over combined (prior + new) outcome dicts."""
    from collections import Counter

    n = len(outcomes)
    if n == 0:
        return {
            "n_questions": 0,
            "ex_overall": 0.0,
            "ex_by_difficulty": {},
            "n_by_difficulty": {},
            "error_counts": {},
            "avg_latency_ms": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
        }

    ex_overall = sum(int(o["ex"]) for o in outcomes) / n
    by_diff: dict[str, list[int]] = {}
    for o in outcomes:
        by_diff.setdefault(o["difficulty"], []).append(int(o["ex"]))
    ex_by_difficulty = {k: round(sum(v) / len(v), 4) for k, v in by_diff.items()}
    n_by_difficulty = {k: len(v) for k, v in by_diff.items()}

    err_counter: Counter[str] = Counter()
    for o in outcomes:
        err = o.get("error")
        if err:
            err_counter[err.split(":", 1)[0]] += 1

    return {
        "n_questions": n,
        "ex_overall": round(ex_overall, 4),
        "ex_by_difficulty": ex_by_difficulty,
        "n_by_difficulty": n_by_difficulty,
        "error_counts": dict(err_counter),
        "avg_latency_ms": int(sum(int(o["latency_ms"]) for o in outcomes) / n),
        "total_prompt_tokens": sum(int(o["prompt_tokens"]) for o in outcomes),
        "total_completion_tokens": sum(int(o["completion_tokens"]) for o in outcomes),
    }


def _md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=BIRD_DIR / "dev_databases/financial/financial.sqlite")
    p.add_argument("--questions", type=Path, default=BIRD_DIR / "dev.json")
    p.add_argument("--tables", type=Path, default=BIRD_DIR / "dev_tables.json")
    p.add_argument("--descriptions", type=Path, default=BIRD_DIR / "dev_databases/financial/database_description")
    p.add_argument("--tied", type=Path, default=BIRD_DIR / "dev_tied_append.json")
    p.add_argument("--output", type=Path, default=None, help="results JSON (default: results/bird_financial_<today>.json)")
    p.add_argument("--limit", type=int, default=None, help="only run first N questions (order preserved)")
    p.add_argument(
        "--difficulty",
        choices=["simple", "moderate", "challenging"],
        default=None,
        help="restrict to one difficulty bucket",
    )
    p.add_argument("--sql-timeout", type=float, default=30.0, help="per-query SQLite timeout in seconds")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument(
        "--resume-from",
        type=Path,
        default=None,
        help="path to a prior results JSON; question_ids already present will be skipped, "
        "and their outcomes merged into the new output",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output or _default_output()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[bird] loading questions from {args.questions}", flush=True)
    all_qs = load_financial_questions(args.questions)
    if args.difficulty:
        all_qs = [q for q in all_qs if q.difficulty == args.difficulty]
    if args.limit is not None:
        all_qs = all_qs[: args.limit]
    print(f"[bird] {len(all_qs)} questions selected", flush=True)

    prior_outcomes: list[dict] = []
    prior_model: str | None = None
    if args.resume_from:
        prior = json.loads(args.resume_from.read_text(encoding="utf-8"))
        prior_outcomes = prior.get("per_question", [])
        prior_model = prior.get("model")
        done_ids = {int(o["question_id"]) for o in prior_outcomes}
        before = len(all_qs)
        all_qs = [q for q in all_qs if q.question_id not in done_ids]
        print(
            f"[bird] resume: {len(prior_outcomes)} prior outcomes loaded from {args.resume_from} "
            f"(model={prior_model}); skipping {before - len(all_qs)} completed → "
            f"{len(all_qs)} left to run",
            flush=True,
        )

    tied = load_tied_append(args.tied)
    print(f"[bird] {len(tied)} tied-append entries loaded", flush=True)

    print(f"[bird] building schema block from {args.tables} + {args.descriptions}", flush=True)
    schema_block = build_financial_schema_block(args.tables, args.descriptions)
    print(f"[bird] schema block: {len(schema_block)} chars", flush=True)

    executor = BirdSQLiteExecutor(args.db, timeout_s=args.sql_timeout)
    runner = BirdRunner(
        executor=executor,
        schema_block=schema_block,
        tied_gold_sqls={qid: sqls for qid, sqls in tied.items() if any(q.question_id == qid for q in all_qs)},
        temperature=args.temperature,
    )

    outcomes = []
    started_at = time.time()
    for i, q in enumerate(all_qs, 1):
        t0 = time.perf_counter()
        try:
            outcome = runner.evaluate(q)
        except Exception as e:  # last-resort catch: don't lose the batch
            print(f"[bird] q{q.question_id} ({q.difficulty}) UNCAUGHT: {type(e).__name__}: {e}", flush=True)
            continue
        outcomes.append(outcome)
        elapsed = time.perf_counter() - t0
        mark = "✓" if outcome.ex else "✗"
        err_tag = f" err={outcome.error.split(':',1)[0]}" if outcome.error else ""
        print(
            f"[bird] {i:3d}/{len(all_qs)} q{q.question_id:<4d} ({q.difficulty:<11s}) {mark}{err_tag}  {elapsed:5.1f}s",
            flush=True,
        )

    new_outcome_dicts = [o.to_dict() for o in outcomes]
    combined_dicts = sorted(prior_outcomes + new_outcome_dicts, key=lambda o: int(o["question_id"]))

    # Recompute summary over combined outcomes so EX numbers reflect all questions.
    combined_summary = _summarize_dicts(combined_dicts)

    result_doc: dict = {
        "benchmark": "bird_financial",
        "run_date_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "model": CHAT_MODEL,
        "dev_json_md5": _md5(args.questions),
        "sqlite_md5": _md5(args.db),
        "filters": {
            "difficulty": args.difficulty,
            "limit": args.limit,
        },
        "wall_clock_seconds": int(time.time() - started_at),
        "summary": combined_summary,
        "per_question": combined_dicts,
    }
    if args.resume_from:
        result_doc["resume"] = {
            "prior_results_file": str(args.resume_from),
            "prior_model": prior_model,
            "prior_n_outcomes": len(prior_outcomes),
            "new_n_outcomes": len(new_outcome_dicts),
        }
    output_path.write_text(json.dumps(result_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print("---")
    print(f"[bird] wrote {output_path}")
    n_total = combined_summary["n_questions"]
    total_hits = sum(int(o["ex"]) for o in combined_dicts)
    print(f"[bird] EX overall: {combined_summary['ex_overall']:.4f}  ({total_hits}/{n_total})")
    for diff, score in combined_summary["ex_by_difficulty"].items():
        n_diff = combined_summary["n_by_difficulty"][diff]
        print(f"[bird]   {diff:<11s}: {score:.4f}  ({int(round(score * n_diff))}/{n_diff})")
    if combined_summary["error_counts"]:
        print(f"[bird] errors: {combined_summary['error_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
