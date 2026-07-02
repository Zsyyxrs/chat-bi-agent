#!/usr/bin/env python3
"""Run the *production* P1NL2SQLAgent (unchanged) against BIRD-financial.

Companion to ``run_bird_financial.py``:
- ``run_bird_financial.py``   → lean BIRD-specific pipeline (measures LLM ceiling)
- ``run_bird_financial_p1.py`` → wraps live P1 agent (measures pipeline generalization)

Result JSON schema mirrors the lean runner's plus a few P1-only fields:
``attempts`` (Reflector loop count), ``reflect_history`` (per-attempt error class + action),
``schema_link_top_k`` (tables provided to the LLM), ``p1_error_class`` (final P1 classifier verdict).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from chat_bi_agent.config import CHAT_MODEL  # noqa: E402
from chat_bi_agent.eval.bird_financial.ex_scorer import rows_hash, score_ex  # noqa: E402
from chat_bi_agent.eval.bird_financial.loader import (  # noqa: E402
    load_financial_questions,
    load_tied_append,
)
from chat_bi_agent.eval.bird_financial.p1_adapter import build_p1_bird_agent  # noqa: E402
from chat_bi_agent.eval.bird_financial.sqlite_executor import (  # noqa: E402
    BirdSQLiteExecutor,
    ExecutorRuntimeError,
    ExecutorSyntaxError,
    ExecutorTimeout,
    ExecutorUnsafeSQL,
)

BIRD_DIR = REPO_ROOT / "benchmarks" / "bird"


def _default_output() -> Path:
    date = dt.date.today().isoformat()
    return REPO_ROOT / "results" / f"bird_financial_p1_{date}.json"


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
    p.add_argument(
        "--descriptions",
        type=Path,
        default=BIRD_DIR / "dev_databases/financial/database_description",
    )
    p.add_argument("--tied", type=Path, default=BIRD_DIR / "dev_tied_append.json")
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--difficulty",
        choices=["simple", "moderate", "challenging"],
        default=None,
    )
    p.add_argument("--sql-timeout", type=float, default=30.0)
    p.add_argument(
        "--dialect",
        choices=["postgres", "sqlite"],
        default="sqlite",
        help='SQL dialect for P1 SQLGenerator / Validator / Reflector. '
        'Default "sqlite" targets BIRD; use "postgres" to reproduce the '
        "pre-dialect-fix baseline for A/B comparison.",
    )
    p.add_argument(
        "--resume-from",
        type=Path,
        default=None,
        help="path to a prior results JSON; question_ids already present will be skipped",
    )
    return p.parse_args()


def _summarize(outcomes: list[dict]) -> dict[str, Any]:
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
            "avg_attempts": 0.0,
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
        "total_prompt_tokens": sum(int(o.get("prompt_tokens") or 0) for o in outcomes),
        "total_completion_tokens": sum(int(o.get("completion_tokens") or 0) for o in outcomes),
        "avg_attempts": round(sum(int(o.get("attempts") or 1) for o in outcomes) / n, 2),
    }


def _evaluate_one(agent, executor: BirdSQLiteExecutor, question, tied_map: dict) -> dict:
    """Run P1 agent on one question, then re-execute predicted SQL for EX scoring."""
    start = time.perf_counter()

    # Gold rows (for EX comparison)
    gold_rows = executor.execute(question.gold_sql).rows
    gold_h = rows_hash(gold_rows)
    tied_rows_alts: list[list[tuple]] = []
    for alt_sql in tied_map.get(question.question_id, []):
        try:
            tied_rows_alts.append(executor.execute(alt_sql).rows)
        except Exception:
            pass

    # Run the P1 agent — this uses P1's *own* executor (SQLite via adapter) internally
    p1_start = time.perf_counter()
    try:
        p1_result = agent.run(str(question.question_id), question.question)
    except Exception as e:
        total_ms = max(1, int((time.perf_counter() - start) * 1000))
        return {
            "question_id": question.question_id,
            "difficulty": question.difficulty,
            "question": question.question,
            "gold_sql": question.gold_sql,
            "predicted_sql": None,
            "gold_rows_hash": gold_h,
            "predicted_rows_hash": None,
            "ex": 0,
            "error": f"agent_exception:{type(e).__name__}:{str(e)[:200]}",
            "latency_ms": total_ms,
            "p1_latency_ms": max(1, int((time.perf_counter() - p1_start) * 1000)),
            "attempts": 0,
            "reflect_history": [],
            "schema_link_top_k": [],
            "p1_error_class": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    p1_ms = p1_result.total_latency_ms

    predicted_sql = p1_result.sql
    # Re-execute the predicted SQL against a *fresh* executor call for a canonical
    # tuple-shaped row list (P1's adapter returns dicts internally). If P1 already
    # failed, skip re-exec.
    error: str | None = None
    predicted_rows: list[tuple] = []
    if predicted_sql is None:
        error = f"p1_gave_up:{p1_result.error_class.value if p1_result.error_class else 'no_sql'}"
    elif p1_result.execution_error is not None:
        # P1 kept the failing SQL from its last attempt; try one more exec for hash consistency
        try:
            predicted_rows = executor.execute(predicted_sql).rows
        except ExecutorUnsafeSQL as e:
            error = f"unsafe:{str(e)[:200]}"
        except ExecutorSyntaxError as e:
            error = f"syntax:{str(e)[:200]}"
        except ExecutorTimeout as e:
            error = f"timeout:{str(e)[:200]}"
        except ExecutorRuntimeError as e:
            error = f"runtime:{str(e)[:200]}"
    else:
        try:
            predicted_rows = executor.execute(predicted_sql).rows
        except ExecutorUnsafeSQL as e:
            error = f"unsafe:{str(e)[:200]}"
        except ExecutorSyntaxError as e:
            error = f"syntax:{str(e)[:200]}"
        except ExecutorTimeout as e:
            error = f"timeout:{str(e)[:200]}"
        except ExecutorRuntimeError as e:
            error = f"runtime:{str(e)[:200]}"

    ex = 0
    predicted_h: str | None = None
    if error is None and predicted_sql is not None:
        ex = score_ex(predicted_rows, gold_rows, tied_alternates=tied_rows_alts)
        predicted_h = rows_hash(predicted_rows)

    total_ms = max(1, int((time.perf_counter() - start) * 1000))
    return {
        "question_id": question.question_id,
        "difficulty": question.difficulty,
        "question": question.question,
        "gold_sql": question.gold_sql,
        "predicted_sql": predicted_sql,
        "gold_rows_hash": gold_h,
        "predicted_rows_hash": predicted_h,
        "ex": ex,
        "error": error,
        "latency_ms": total_ms,
        "p1_latency_ms": p1_ms,
        "attempts": p1_result.attempts,
        "reflect_history": p1_result.reflect_history,
        "schema_link_top_k": p1_result.schema_link_top_k,
        "p1_error_class": p1_result.error_class.value if p1_result.error_class else None,
        # P1 doesn't expose per-call token usage — leave 0; Langfuse traces have the truth
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def main() -> int:
    args = parse_args()
    output_path = args.output or _default_output()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[bird-p1] loading questions from {args.questions}", flush=True)
    all_qs = load_financial_questions(args.questions)
    if args.difficulty:
        all_qs = [q for q in all_qs if q.difficulty == args.difficulty]
    if args.limit is not None:
        all_qs = all_qs[: args.limit]
    print(f"[bird-p1] {len(all_qs)} questions selected", flush=True)

    prior_outcomes: list[dict] = []
    if args.resume_from:
        prior = json.loads(args.resume_from.read_text(encoding="utf-8"))
        prior_outcomes = prior.get("per_question", [])
        done_ids = {int(o["question_id"]) for o in prior_outcomes}
        before = len(all_qs)
        all_qs = [q for q in all_qs if q.question_id not in done_ids]
        print(
            f"[bird-p1] resume: {len(prior_outcomes)} prior outcomes, "
            f"skipping {before - len(all_qs)} → {len(all_qs)} left to run",
            flush=True,
        )

    tied = load_tied_append(args.tied)
    executor = BirdSQLiteExecutor(args.db, timeout_s=args.sql_timeout)
    print("[bird-p1] building P1 agent with BIRD stubs (skips SchemaLoader index)", flush=True)
    agent = build_p1_bird_agent(
        tables_json=args.tables,
        description_dir=args.descriptions,
        sqlite_db=args.db,
        sql_timeout_s=args.sql_timeout,
        dialect=args.dialect,
    )
    print(f"[bird-p1] dialect: {args.dialect}", flush=True)

    new_outcomes: list[dict] = []
    started_at = time.time()
    for i, q in enumerate(all_qs, 1):
        t0 = time.perf_counter()
        try:
            out = _evaluate_one(agent, executor, q, tied)
        except Exception as e:
            print(f"[bird-p1] q{q.question_id} UNCAUGHT {type(e).__name__}: {e}", flush=True)
            continue
        new_outcomes.append(out)
        elapsed = time.perf_counter() - t0
        mark = "✓" if out["ex"] else "✗"
        err_tag = f" err={out['error'].split(':', 1)[0]}" if out["error"] else ""
        attempts_tag = f" att={out['attempts']}"
        print(
            f"[bird-p1] {i:3d}/{len(all_qs)} q{q.question_id:<4d} "
            f"({q.difficulty:<11s}) {mark}{err_tag}{attempts_tag}  {elapsed:5.1f}s",
            flush=True,
        )

    combined = sorted(prior_outcomes + new_outcomes, key=lambda o: int(o["question_id"]))
    summary = _summarize(combined)

    result_doc: dict = {
        "benchmark": "bird_financial",
        "variant": "p1_agent",
        "dialect": args.dialect,
        "run_date_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "model": CHAT_MODEL,
        "dev_json_md5": _md5(args.questions),
        "sqlite_md5": _md5(args.db),
        "filters": {"difficulty": args.difficulty, "limit": args.limit},
        "wall_clock_seconds": int(time.time() - started_at),
        "summary": summary,
        "per_question": combined,
    }
    output_path.write_text(json.dumps(result_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print("---")
    print(f"[bird-p1] wrote {output_path}")
    total_hits = sum(int(o["ex"]) for o in combined)
    print(f"[bird-p1] EX overall: {summary['ex_overall']:.4f}  ({total_hits}/{summary['n_questions']})")
    for diff, score in summary["ex_by_difficulty"].items():
        n_diff = summary["n_by_difficulty"][diff]
        print(f"[bird-p1]   {diff:<11s}: {score:.4f}  ({int(round(score * n_diff))}/{n_diff})")
    print(f"[bird-p1] avg attempts: {summary['avg_attempts']}")
    if summary["error_counts"]:
        print(f"[bird-p1] errors: {summary['error_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
