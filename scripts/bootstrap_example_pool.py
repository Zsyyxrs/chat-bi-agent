#!/usr/bin/env python3
"""Bootstrap a Q-SQL few-shot pool from BIRD non-financial dev questions.

Why non-financial only: our target eval set IS BIRD financial dev (106 questions).
Putting financial (question, gold_sql) pairs into the retriever pool would let the
retriever return the exact gold as a "similar example" — pure leakage. We use the
other 10 dev DBs (1428 questions total) as a source of SQLite-idiomatic examples
that share the dialect but not the schema, so few-shot teaches style and syntax
(STRFTIME, JOIN patterns, LIMIT/ORDER BY) without cheating.

Usage:
    python scripts/bootstrap_example_pool.py                       # all 1428 non-financial
    python scripts/bootstrap_example_pool.py --sample-per-db 30    # ~300 balanced
    python scripts/bootstrap_example_pool.py --limit 50            # smoke test
    python scripts/bootstrap_example_pool.py --dry-run             # no embed, no write

Output default: data/example_pool_bird.jsonl (gitignored).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from chat_bi_agent.agents.shared.example_retriever import (
    ExamplePool,
    QAExample,
    compute_example_id,
)

BIRD_DIR = REPO_ROOT / "benchmarks" / "bird"
FINANCIAL_DB_ID = "financial"
EMBED_BATCH_SIZE = 10  # DashScope text-embedding-v4 accepts up to 25 per call; use 10 for safety


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--dev-json",
        type=Path,
        default=BIRD_DIR / "dev.json",
        help="BIRD dev.json path (all 1534 questions)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "data" / "example_pool_bird.jsonl",
    )
    p.add_argument(
        "--sample-per-db",
        type=int,
        default=None,
        help="Cap examples per non-financial db_id (balanced sampling). Default: no cap.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Overall cap after per-db sampling. For smoke tests.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for per-db sampling",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip embedding + writing; just print counts",
    )
    return p.parse_args()


def _select_questions(
    all_questions: list[dict], sample_per_db: int | None, limit: int | None, seed: int
) -> list[dict]:
    rng = random.Random(seed)
    non_financial = [q for q in all_questions if q["db_id"] != FINANCIAL_DB_ID]
    print(
        f"[bootstrap] total={len(all_questions)}, non-financial={len(non_financial)}",
        flush=True,
    )

    if sample_per_db is None:
        selected = non_financial
    else:
        by_db: dict[str, list[dict]] = defaultdict(list)
        for q in non_financial:
            by_db[q["db_id"]].append(q)
        selected = []
        for db_id, items in sorted(by_db.items()):
            if len(items) > sample_per_db:
                rng.shuffle(items)
                items = items[:sample_per_db]
            selected.extend(items)
            print(f"[bootstrap]   {db_id}: {len(items)} selected", flush=True)

    if limit is not None and len(selected) > limit:
        rng.shuffle(selected)
        selected = selected[:limit]
        print(f"[bootstrap] hard limit applied: {len(selected)}", flush=True)

    return selected


def _to_qa_example(item: dict, ts: str) -> QAExample:
    question = item["question"]
    sql = item["SQL"]
    db_id = item["db_id"]
    return QAExample(
        example_id=compute_example_id(question, sql),
        question=question,
        sql=sql,
        dialect="sqlite",
        source=f"bird_dev_{db_id}",
        tags=["bird_non_financial", f"bird_db_{db_id}"],
        ts=ts,
        embedding=None,
    )


def _batched_embed(questions: list[str], batch_size: int) -> list[list[float]]:
    """Batch-embed questions using DashScope. Only imported here so --dry-run works without API key."""
    from chat_bi_agent.llm import qwen_client  # noqa: PLC0415

    all_vecs: list[list[float]] = []
    for i in range(0, len(questions), batch_size):
        batch = questions[i : i + batch_size]
        vecs = qwen_client.embed(batch)
        all_vecs.extend(vecs)
        print(
            f"[bootstrap] embedded {min(i + batch_size, len(questions))}/{len(questions)}",
            flush=True,
        )
    return all_vecs


def main() -> int:
    args = parse_args()

    if not args.dev_json.exists():
        print(f"[bootstrap] ERROR: dev.json not found at {args.dev_json}", file=sys.stderr)
        return 1

    all_questions = json.loads(args.dev_json.read_text(encoding="utf-8"))
    selected = _select_questions(
        all_questions,
        sample_per_db=args.sample_per_db,
        limit=args.limit,
        seed=args.seed,
    )

    ts = dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    examples = [_to_qa_example(q, ts) for q in selected]

    # Dedup by example_id (some datasets have identical (question, sql) pairs)
    seen: set[str] = set()
    unique_examples: list[QAExample] = []
    for ex in examples:
        if ex.example_id in seen:
            continue
        seen.add(ex.example_id)
        unique_examples.append(ex)

    print(
        f"[bootstrap] {len(unique_examples)} unique examples to embed"
        f" ({len(examples) - len(unique_examples)} deduped)",
        flush=True,
    )

    if args.dry_run:
        print("[bootstrap] --dry-run: skipping embed + write", flush=True)
        return 0

    # Embed
    print(f"[bootstrap] embedding via DashScope (batch={EMBED_BATCH_SIZE}) ...", flush=True)
    vecs = _batched_embed([ex.question for ex in unique_examples], EMBED_BATCH_SIZE)
    for ex, v in zip(unique_examples, vecs):
        ex.embedding = v

    # Save
    pool = ExamplePool(unique_examples)
    pool.save(args.output)
    print(f"[bootstrap] wrote {len(pool)} examples to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
