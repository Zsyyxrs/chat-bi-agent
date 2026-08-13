"""Phase 5: 跑 6 题 happy path → P1Agent → PrecisionRetrievalEvaluator → 汇总。

运行：
    python -m chat_bi_agent.runners.run_p1_eval                                    # 无 few-shot
    python -m chat_bi_agent.runners.run_p1_eval --example-pool data/example_pool_prod.jsonl

few-shot 用 leave-one-out 语义：retriever 传 exclude_question_texts 排除当前评测题，
防止用池里已有的 (q, sql) 自问自答泄题。
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

from chat_bi_agent.agents.p1.metric_resolver import MetricCatalog, MetricRouter  # noqa: E402
from chat_bi_agent.agents.p1.nl2sql_agent import P1NL2SQLAgent  # noqa: E402
from chat_bi_agent.agents.shared.example_retriever import (  # noqa: E402
    ExamplePool,
    ExampleRetriever,
)
from chat_bi_agent.agents.shared.sql_executor import SQLExecutor  # noqa: E402
from chat_bi_agent.eval.precision_retrieval_evaluator import (  # noqa: E402
    PrecisionEvaluation,
    PrecisionRetrievalEvaluator,
)
from chat_bi_agent.llm import qwen_client  # noqa: E402
from chat_bi_agent.llm.langfuse_setup import flush, get_client  # noqa: E402

HAPPY_PATH_IDS = [
    "precision_q001",
    "precision_q002",
    "precision_q003",
    "precision_q004",
    "precision_q006",
    "precision_q007",
]

YAML_PATH = Path(__file__).resolve().parents[1] / "data" / "precision_retrieval_evaluation.yaml"


def load_questions() -> dict[str, dict]:
    with open(YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {q["id"]: q for q in data["evaluation_questions"]}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--example-pool",
        type=Path,
        default=None,
        help="Q-SQL 池 JSONL 路径；不传就 few-shot off",
    )
    p.add_argument("--few-shot-k", type=int, default=3)
    p.add_argument(
        "--few-shot-min-sim",
        type=float,
        default=0.7,
        help="同域场景默认 0.7（比 BIRD 跨库的 0.55 严格，宁缺毋滥）",
    )
    p.add_argument(
        "--metric-catalog",
        type=Path,
        default=None,
        help="MetricCatalog YAML 路径（config/metrics.yaml）；不传就 metric router off",
    )
    p.add_argument(
        "--metric-prefilter-threshold",
        type=float,
        default=0.7,
        help="Metric router prefilter cosine 阈值，默认 0.7",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="结果 JSON 路径；默认 results/baseline_p1_eval_<date>[_fewshot].json",
    )
    return p.parse_args()


def _build_retriever(args: argparse.Namespace) -> ExampleRetriever | None:
    if args.example_pool is None:
        return None
    pool = ExamplePool.load(args.example_pool)
    print(
        f"[p1-eval] few-shot ON pool={args.example_pool} size={len(pool)} "
        f"k={args.few_shot_k} min_sim={args.few_shot_min_sim}",
        flush=True,
    )
    return ExampleRetriever(
        pool=pool,
        dialect="postgres",
        embed_fn=qwen_client.embed,
        min_similarity=args.few_shot_min_sim,
        max_k=args.few_shot_k,
    )


def _build_metric_router(args: argparse.Namespace) -> MetricRouter | None:
    if args.metric_catalog is None:
        return None
    catalog = MetricCatalog.from_yaml(args.metric_catalog)
    print(
        f"[p1-eval] metric router ON catalog={args.metric_catalog} "
        f"n_metrics={len(catalog.metrics)} threshold={args.metric_prefilter_threshold}",
        flush=True,
    )
    # probe_fn 用只读 executor：给 string filter 做值域存在性探针
    probe_executor = SQLExecutor()
    return MetricRouter(
        catalog=catalog,
        embed_fn=qwen_client.embed,
        threshold=args.metric_prefilter_threshold,
        probe_fn=probe_executor.execute,
    )


def _summarize_metric_router(
    per_question: list[dict],
    enabled: bool,
    catalog_path,
    threshold: float | None,
) -> dict | None:
    if not enabled:
        return None
    n_total = len(per_question)
    n_metric = sum(1 for r in per_question if r["route"] == "metric")
    n_fallback = sum(1 for r in per_question if r["route"] == "metric_then_nl2sql")
    n_bypass = sum(1 for r in per_question if r["route"] == "nl2sql")
    n_prefilter_hit = n_metric + n_fallback

    def _avg(scores: list[float]) -> float | None:
        return round(sum(scores) / len(scores), 4) if scores else None

    hit_scores = [r["score"] for r in per_question if r["route"] == "metric"]
    fb_scores = [r["score"] for r in per_question if r["route"] == "metric_then_nl2sql"]
    bp_scores = [r["score"] for r in per_question if r["route"] == "nl2sql"]

    fallback_rate = round(n_fallback / n_prefilter_hit, 4) if n_prefilter_hit > 0 else None

    fail_reasons = [
        "no_metric",
        "unknown_dim",
        "enum_out_of_range",
        "unsupported_op",
        "value_out_of_domain",
        "validator_fail",
        "executor_fail",
    ]
    breakdown = {fr: 0 for fr in fail_reasons}
    for r in per_question:
        fr = r.get("metric_fail_reason")
        if fr in breakdown:
            breakdown[fr] += 1

    return {
        "enabled": True,
        "catalog_path": str(catalog_path) if catalog_path else None,
        "prefilter_threshold": threshold,
        "n_total": n_total,
        "n_prefilter_hit": n_prefilter_hit,
        "n_route_metric": n_metric,
        "n_route_metric_then_nl2sql": n_fallback,
        "n_route_nl2sql": n_bypass,
        "metric_hit_rate": round(n_metric / n_total, 4) if n_total else 0.0,
        "prefilter_hit_rate": round(n_prefilter_hit / n_total, 4) if n_total else 0.0,
        "precision_when_hit": _avg(hit_scores),
        "precision_when_fallback": _avg(fb_scores),
        "precision_when_bypass": _avg(bp_scores),
        "fallback_rate": fallback_rate,
        "fail_reason_breakdown": breakdown,
    }


@observe(name="p1_eval_batch")
def main(args: argparse.Namespace | None = None) -> int:
    if args is None:
        args = parse_args()
    get_client()
    questions = load_questions()
    retriever = _build_retriever(args)
    metric_router = _build_metric_router(args)
    agent = P1NL2SQLAgent(top_k=4, example_retriever=retriever, metric_router=metric_router)
    # A/B 实验臂打到 batch trace 的 tags 上。agent._tag_trace 只写 metadata，不碰 tags，
    # 所以这里不会被逐题覆盖；Langfuse UI 里可直接按臂筛选。
    try:
        get_client().update_current_trace(
            tags=[
                "arm:metric_router" if metric_router is not None else "arm:baseline",
                "fewshot:on" if retriever is not None else "fewshot:off",
            ],
        )
    except Exception:
        pass
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

        per_question.append(
            {
                "question_id": qid,
                "rows": len(agent_result.rows) if agent_result.rows else 0,
                "attempts": agent_result.attempts,
                "latency_ms": agent_result.total_latency_ms,
                "score": round(score.combined_score, 4),
                "error_class": agent_result.error_class.value if agent_result.error_class else None,
                "reflect_history": agent_result.reflect_history,
                "sql": agent_result.sql,
                "route": agent_result.route,
                "metric_id": agent_result.metric_id,
                "prefilter_cosine": agent_result.prefilter_cosine,
                "metric_spec": agent_result.metric_spec,
                "metric_fail_reason": agent_result.metric_fail_reason,
            }
        )

    print()
    print(evaluation.summary())
    print(f"Pass Rate: {evaluation.pass_rate:.1%}")
    print(f"Avg Score: {evaluation.avg_score:.3f}")

    if args.output is not None:
        out_path = args.output
    else:
        date_str = datetime.now(UTC).date().isoformat()
        suffix = "_fewshot" if retriever is not None else ""
        out_path = (
            Path(__file__).resolve().parents[3]
            / "results"
            / f"baseline_p1_eval_{date_str}{suffix}.json"
        )
    from chat_bi_agent.eval.latency_stats import latency_percentiles
    from chat_bi_agent.eval.run_metadata import build_run_metadata

    lat_stats = latency_percentiles([r["latency_ms"] for r in per_question])
    extra_hashes = {"pool_hash": args.example_pool} if args.example_pool else {}
    payload = {
        "baseline_id": "p1_eval",
        "ran_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_metadata": build_run_metadata(extra_paths=extra_hashes),
        "few_shot": {
            "enabled": retriever is not None,
            "pool_path": str(args.example_pool) if args.example_pool else None,
            "k": args.few_shot_k,
            "min_similarity": args.few_shot_min_sim,
        },
        "metric_router": _summarize_metric_router(
            per_question=per_question,
            enabled=metric_router is not None,
            catalog_path=args.metric_catalog,
            threshold=args.metric_prefilter_threshold if metric_router else None,
        ),
        "total_questions": evaluation.total_questions,
        "passed_questions": evaluation.passed_questions,
        "pass_rate": round(evaluation.pass_rate, 4),
        "avg_score": round(evaluation.avg_score, 4),
        "latency_ms": lat_stats,
        "per_question": per_question,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote baseline JSON → {out_path}")

    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        flush()
    sys.exit(exit_code)
