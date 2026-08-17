"""用已落盘的 P2 产物离线重放评分，不跑 agent、不调 LLM。

为什么需要它：P2 单题 300~500s，一轮 3 题就是 22 分钟。改一次评分器（调阈值、换
匹配方式）如果都要重跑，调参根本没法迭代——2026-08-15 修 insight 维时就是这么过来的。

前提是产物里存了 `eval_input`（评分器的完整入参）。2026-08-17 之前只存 200 字预览，
评分用的却是完整回答，所以旧产物无法精确重放——用 `--allow-preview` 可以拿预览
凑合看趋势，但结论不可当真。

用法：
    python scripts/replay_p2_scoring.py results/baseline_p2_analysis_2026-08-15.json
    python scripts/replay_p2_scoring.py <产物> --compare   # 与产物里记录的分数对比
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# --rejudge 要调 LLM，需要 DASHSCOPE_API_KEY。默认路径不调 LLM，所以这里缺 .env
# 也不影响；但不加载的话 --rejudge 会三题全降级，只在日志里留 warning。
from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from chat_bi_agent.eval.multi_step_analysis_evaluator import (  # noqa: E402
    JUDGE_DIMS,
    MultiStepAnalysisEvaluator,
)

# 计分维度（诊断维 reasoning_quality / business_relevance 不在此列，见 ADR-015）
SUB_DIMS = [
    "step_completeness",
    "multi_metric_coverage",
    "insight_accuracy",
    "rubric_score",
]


def _eval_input_for(q: dict, allow_preview: bool) -> dict | None:
    if "eval_input" in q:
        return q["eval_input"]
    if not allow_preview:
        return None
    preview = q.get("final_answer_preview")
    if preview is None:
        return None
    return {
        "question_id": q["question_id"],
        "agent_response": preview,
        "mentioned_steps": [],
        "mentioned_metrics": [],
        "extracted_insights": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact", type=Path)
    ap.add_argument("--compare", action="store_true", help="与产物里记录的分数逐维对比")
    ap.add_argument(
        "--allow-preview",
        action="store_true",
        help="产物没有 eval_input 时退而用 200 字预览（趋势参考，结论不可当真）",
    )
    ap.add_argument(
        "--rejudge",
        action="store_true",
        help="用当前 prompt 重跑 rubric LLM judge（要花钱调 LLM）；默认复用产物里存的 rubric",
    )
    ap.add_argument(
        "--write",
        action="store_true",
        help="把重评结果写成 <原名>_rescored.json（保留原 agent 跑的出处，另记评分器出处）",
    )
    args = ap.parse_args()

    data = json.loads(args.artifact.read_text(encoding="utf-8"))
    # 默认不调 LLM：产物里存了 rubric 就复用，重放既精确又免费。改确定性维度时这是
    # 常态路径。要看「换了 judge prompt 会怎样」才需要 --rejudge（照 P3 的
    # scripts/rejudge_baseline.py）。
    ev = MultiStepAnalysisEvaluator(use_llm_judge=args.rejudge)

    skipped = []
    no_rubric = []
    rows = []
    for q in data.get("per_question", []):
        ei = _eval_input_for(q, args.allow_preview)
        if ei is None:
            skipped.append(q.get("question_id", "?"))
            continue
        if not args.rejudge:
            stored = (q.get("sub_scores") or {}).get("analysis_rubric")
            if stored:
                ei = {**ei, "precomputed_rubric": stored}
            else:
                # 早于 ADR-016 的产物没有 rubric——该维退出计分，总分按三维归一。
                # 与存了 rubric 的题不同口径，必须说出来。
                no_rubric.append(q.get("question_id", "?"))
        rows.append((q, ev.evaluate_response(**ei)))

    if skipped:
        print(
            f"[replay] 跳过 {len(skipped)} 题（产物无 eval_input）：{', '.join(skipped)}\n"
            f"         该产物早于 2026-08-17 的 payload 变更；加 --allow-preview 可用预览凑合看。",
            file=sys.stderr,
        )
    if no_rubric:
        print(
            f"[replay] {len(no_rubric)} 题产物里没有 analysis_rubric：{', '.join(no_rubric)}\n"
            f"         这些题按确定性三维归一计分，与有 rubric 的题不同口径，不可直接对比。",
            file=sys.stderr,
        )
    if not rows:
        print("[replay] 无可重放的题目", file=sys.stderr)
        return 1

    def _stored(q: dict, dim: str) -> float:
        """产物里对应维度的值。rubric_score 需从存下的 4 维子分现算。"""
        subs = q.get("sub_scores") or {}
        if dim != "rubric_score":
            return subs.get(dim, float("nan"))
        rubric = subs.get("analysis_rubric")
        if not rubric:
            return float("nan")
        vals = [rubric[d] for d in JUDGE_DIMS if isinstance(rubric.get(d), (int, float))]
        return sum(vals) / len(vals) if vals else float("nan")

    header = f"{'qid':<16}" + "".join(f"{d[:13]:>15}" for d in SUB_DIMS) + f"{'combined':>11}"
    print(header)
    print("-" * len(header))
    for q, s in rows:
        vals = [getattr(s, d) for d in SUB_DIMS]
        print(
            f"{q['question_id']:<16}"
            + "".join(f"{'—':>15}" if v is None else f"{v:>15.3f}" for v in vals)
            + f"{s.combined_score:>11.3f}"
        )
        if args.compare and "sub_scores" in q:
            old = [_stored(q, d) for d in SUB_DIMS]
            deltas = "".join(
                f"{'—':>15}" if n is None else f"{n - o:>+15.3f}" for n, o in zip(vals, old)
            )
            print(f"{'  └ Δ vs 产物':<16}{deltas}{s.combined_score - q.get('score', 0):>+11.3f}")

    avg = sum(s.combined_score for _, s in rows) / len(rows)
    print(f"\n重放 avg = {avg:.4f}（n={len(rows)}）", end="")
    if args.compare:
        print(f"，产物记录 avg = {data.get('avg_score')}")
    else:
        print()

    if args.write:
        _write_rescored(args.artifact, data, rows, avg, rejudged=args.rejudge)
    return 0


def _write_rescored(src: Path, data: dict, rows: list, avg: float, rejudged: bool) -> None:
    """写重评产物。

    为什么单独一份而不是覆盖原产物：原产物是「那次 agent 跑 + 当时评分器」的忠实记录，
    覆盖掉就再也无法回答「分数变化里多少来自评分器、多少来自 agent」。改评分器后重跑
    agent 也不行——agent 跑间本身有波动（实测 q001 两轮 0.700 / 0.679），会把两个变量
    混在一起。重评保持 agent 输出不变、只换评分器，归因才干净。

    `ran_at` / `run_metadata` 原样保留（它们描述的是 agent 那次跑），评分器出处另记在
    `rescored_*` 字段里——这两个出处混淆过一次就再也说不清了。
    """
    from datetime import UTC, datetime

    from chat_bi_agent.eval.run_metadata import build_run_metadata

    out = dict(data)
    by_id = {q["question_id"]: s for q, s in rows}
    passed = 0
    per_q = []
    for q in data.get("per_question", []):
        q2 = dict(q)
        s = by_id.get(q.get("question_id"))
        if s is not None:
            q2["score"] = round(s.combined_score, 4)
            q2["sub_scores"] = {
                "step_completeness": round(s.step_completeness, 4),
                "multi_metric_coverage": round(s.multi_metric_coverage, 4),
                "insight_accuracy": round(s.insight_accuracy, 4),
                "reasoning_quality": round(s.reasoning_quality, 4),
                "business_relevance": round(s.business_relevance, 4),
                "analysis_rubric": s.analysis_rubric,
            }
            q2["rubric_unavailable"] = not s.rubric_available
            passed += s.combined_score >= 0.7
        per_q.append(q2)

    out["per_question"] = per_q
    out["passed_questions"] = passed
    out["pass_rate"] = round(passed / len(rows), 4) if rows else 0.0
    out["avg_score"] = round(avg, 4)
    out["rubric_unavailable_questions"] = [
        q["question_id"] for q in per_q if q.get("rubric_unavailable")
    ]
    # 出处：agent 那次跑的 ran_at / run_metadata 原样不动，评分器的出处单独记。
    out["rescored_from"] = src.name
    out["rescored_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    out["rescored_judge_rerun"] = rejudged
    out["rescorer_metadata"] = build_run_metadata()

    dst = src.with_name(src.stem + "_rescored.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写入重评产物 → {dst}")
    print(
        "  注意：agent 回答沿用原产物（ran_at / run_metadata 未改），"
        "仅评分器为当前代码（rescorer_metadata）。"
    )


if __name__ == "__main__":
    sys.exit(main())
