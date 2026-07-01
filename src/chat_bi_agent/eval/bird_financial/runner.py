"""Orchestrate NL→SQL → SQLite exec → EX scoring for one BIRD question, or a batch.

Result JSON schema (per question) matches the design doc:

    {
      "question_id": int,
      "difficulty": str,
      "question": str,
      "evidence": str,
      "gold_sql": str,
      "predicted_sql": str | null,
      "gold_rows_hash": str,
      "predicted_rows_hash": str | null,
      "ex": 0 | 1,
      "error": null | "parse" | "unsafe" | "syntax" | "timeout" | "runtime:<msg>",
      "latency_ms": int,      # total wall-clock (LLM + SQL exec)
      "llm_latency_ms": int,
      "sql_latency_ms": int,
      "prompt_tokens": int,
      "completion_tokens": int
    }
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from chat_bi_agent.eval.bird_financial.ex_scorer import rows_hash, score_ex
from chat_bi_agent.eval.bird_financial.loader import BirdQuestion
from chat_bi_agent.eval.bird_financial.nl2sql import NL2SQLParseError, generate_sql
from chat_bi_agent.eval.bird_financial.sqlite_executor import (
    BirdSQLiteExecutor,
    ExecutorRuntimeError,
    ExecutorSyntaxError,
    ExecutorTimeout,
    ExecutorUnsafeSQL,
)


@dataclass
class QuestionOutcome:
    question_id: int
    difficulty: str
    question: str
    evidence: str
    gold_sql: str
    predicted_sql: str | None
    gold_rows_hash: str
    predicted_rows_hash: str | None
    ex: int
    error: str | None
    latency_ms: int
    llm_latency_ms: int
    sql_latency_ms: int
    prompt_tokens: int
    completion_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "difficulty": self.difficulty,
            "question": self.question,
            "evidence": self.evidence,
            "gold_sql": self.gold_sql,
            "predicted_sql": self.predicted_sql,
            "gold_rows_hash": self.gold_rows_hash,
            "predicted_rows_hash": self.predicted_rows_hash,
            "ex": self.ex,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "llm_latency_ms": self.llm_latency_ms,
            "sql_latency_ms": self.sql_latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class BatchSummary:
    n_questions: int
    ex_overall: float
    ex_by_difficulty: dict[str, float]
    n_by_difficulty: dict[str, int]
    error_counts: dict[str, int]
    avg_latency_ms: int
    total_prompt_tokens: int
    total_completion_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_questions": self.n_questions,
            "ex_overall": round(self.ex_overall, 4),
            "ex_by_difficulty": {k: round(v, 4) for k, v in self.ex_by_difficulty.items()},
            "n_by_difficulty": self.n_by_difficulty,
            "error_counts": self.error_counts,
            "avg_latency_ms": self.avg_latency_ms,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
        }


@dataclass
class BirdRunner:
    executor: BirdSQLiteExecutor
    schema_block: str
    tied_gold_sqls: dict[int, list[str]] = field(default_factory=dict)
    temperature: float = 0.0

    def _run_gold(self, gold_sql: str) -> list[tuple]:
        return self.executor.execute(gold_sql).rows

    def _run_tied_alternates(self, question_id: int) -> list[list[tuple]]:
        out: list[list[tuple]] = []
        for alt_sql in self.tied_gold_sqls.get(question_id, []):
            try:
                out.append(self.executor.execute(alt_sql).rows)
            except Exception:
                # Skip alternates that don't execute cleanly — they can't help EX.
                continue
        return out

    def evaluate(self, question: BirdQuestion) -> QuestionOutcome:
        start = time.perf_counter()

        # 1. Run gold SQL — always needed for hashing + comparison.
        gold_rows = self._run_gold(question.gold_sql)
        gold_h = rows_hash(gold_rows)
        tied_alts = self._run_tied_alternates(question.question_id)

        # 2. LLM: NL → SQL
        llm_start = time.perf_counter()
        predicted_sql: str | None = None
        prompt_tokens = 0
        completion_tokens = 0
        try:
            gen = generate_sql(
                schema_block=self.schema_block,
                question=question.question,
                evidence=question.evidence,
                temperature=self.temperature,
            )
        except NL2SQLParseError as e:
            llm_ms = max(1, int((time.perf_counter() - llm_start) * 1000))
            total_ms = max(1, int((time.perf_counter() - start) * 1000))
            return QuestionOutcome(
                question_id=question.question_id,
                difficulty=question.difficulty,
                question=question.question,
                evidence=question.evidence,
                gold_sql=question.gold_sql,
                predicted_sql=None,
                gold_rows_hash=gold_h,
                predicted_rows_hash=None,
                ex=0,
                error=f"parse:{str(e)[:200]}",
                latency_ms=total_ms,
                llm_latency_ms=llm_ms,
                sql_latency_ms=0,
                prompt_tokens=0,
                completion_tokens=0,
            )

        llm_ms = max(1, int((time.perf_counter() - llm_start) * 1000))
        predicted_sql = gen.sql
        prompt_tokens = gen.prompt_tokens
        completion_tokens = gen.completion_tokens

        # 3. Execute predicted SQL — classify errors.
        sql_start = time.perf_counter()
        error: str | None = None
        predicted_rows: list[tuple] = []
        try:
            predicted_rows = self.executor.execute(predicted_sql).rows
        except ExecutorUnsafeSQL as e:
            error = f"unsafe:{str(e)[:200]}"
        except ExecutorSyntaxError as e:
            error = f"syntax:{str(e)[:200]}"
        except ExecutorTimeout as e:
            error = f"timeout:{str(e)[:200]}"
        except ExecutorRuntimeError as e:
            error = f"runtime:{str(e)[:200]}"
        sql_ms = max(1, int((time.perf_counter() - sql_start) * 1000))

        ex = 0
        predicted_h: str | None = None
        if error is None:
            ex = score_ex(predicted_rows, gold_rows, tied_alternates=tied_alts)
            predicted_h = rows_hash(predicted_rows)

        total_ms = max(1, int((time.perf_counter() - start) * 1000))
        return QuestionOutcome(
            question_id=question.question_id,
            difficulty=question.difficulty,
            question=question.question,
            evidence=question.evidence,
            gold_sql=question.gold_sql,
            predicted_sql=predicted_sql,
            gold_rows_hash=gold_h,
            predicted_rows_hash=predicted_h,
            ex=ex,
            error=error,
            latency_ms=total_ms,
            llm_latency_ms=llm_ms,
            sql_latency_ms=sql_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def summarize(outcomes: list[QuestionOutcome]) -> BatchSummary:
    n = len(outcomes)
    ex_overall = sum(o.ex for o in outcomes) / n if n else 0.0

    by_diff: dict[str, list[int]] = {}
    for o in outcomes:
        by_diff.setdefault(o.difficulty, []).append(o.ex)
    ex_by_difficulty = {k: sum(v) / len(v) for k, v in by_diff.items()}
    n_by_difficulty = {k: len(v) for k, v in by_diff.items()}

    error_counts: Counter[str] = Counter()
    for o in outcomes:
        if o.error is None:
            continue
        # Bucket by prefix before ":"
        error_counts[o.error.split(":", 1)[0]] += 1

    avg_latency = int(sum(o.latency_ms for o in outcomes) / n) if n else 0
    return BatchSummary(
        n_questions=n,
        ex_overall=ex_overall,
        ex_by_difficulty=ex_by_difficulty,
        n_by_difficulty=n_by_difficulty,
        error_counts=dict(error_counts),
        avg_latency_ms=avg_latency,
        total_prompt_tokens=sum(o.prompt_tokens for o in outcomes),
        total_completion_tokens=sum(o.completion_tokens for o in outcomes),
    )
