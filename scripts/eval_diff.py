"""对比两份 baseline JSON 的 per-question 分数变化。

Usage:
    # 显式指定两份 baseline
    python scripts/eval_diff.py results/baseline_p3_rca_2026-06-28.json \\
                                results/baseline_p3_rca_2026-06-30.json

    # 自动找某 phase 的最新两份 baseline
    python scripts/eval_diff.py --phase p3

注意：
    LLM judge 评分有噪声，单次 ±0.05 内的变化不一定是真退化；
    建议把 --threshold 设到 0.05 以上再判定回归。
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"

PHASE_PATTERNS = {
    "p1": "baseline_p2_validator_reflector_*.json",
    "p2": "baseline_p2_analysis_*.json",
    "p3": "baseline_p3_rca_*.json",
}


def _question_score(q: dict) -> float:
    s = q.get("score")
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, dict):
        for k in ("overall_score", "overall", "total"):
            v = s.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return 0.0


def compute_diff(prev: dict, curr: dict) -> list[dict]:
    """返回 per-question 差异列表：[{question_id, prev_score, curr_score, delta, status}]"""
    prev_by_id = {q["question_id"]: q for q in prev.get("per_question", [])}
    curr_by_id = {q["question_id"]: q for q in curr.get("per_question", [])}
    all_ids = sorted(set(prev_by_id) | set(curr_by_id))

    rows: list[dict] = []
    for qid in all_ids:
        p = prev_by_id.get(qid)
        c = curr_by_id.get(qid)
        p_score = _question_score(p) if p else None
        c_score = _question_score(c) if c else None
        if p_score is None:
            status = "new"
            delta = None
        elif c_score is None:
            status = "removed"
            delta = None
        else:
            delta = round(c_score - p_score, 4)
            status = "same"
        rows.append(
            {
                "question_id": qid,
                "prev_score": p_score,
                "curr_score": c_score,
                "delta": delta,
                "status": status,
            }
        )
    return rows


def render_diff(prev_path: Path, curr_path: Path, threshold: float) -> str:
    prev = json.loads(prev_path.read_text(encoding="utf-8"))
    curr = json.loads(curr_path.read_text(encoding="utf-8"))
    rows = compute_diff(prev, curr)

    lines: list[str] = []
    lines.append("# Eval Diff\n")
    lines.append(f"- prev: `{prev_path.name}` ({prev.get('ran_at', '')})")
    lines.append(f"- curr: `{curr_path.name}` ({curr.get('ran_at', '')})")
    lines.append(
        f"- avg_score: {prev.get('avg_score', 0):.3f} → {curr.get('avg_score', 0):.3f} "
        f"(Δ {curr.get('avg_score', 0) - prev.get('avg_score', 0):+.3f})"
    )
    lines.append("")
    lines.append("## Per-question\n")
    lines.append("| question_id | prev | curr | Δ | flag |")
    lines.append("|-------------|-----:|-----:|--:|:----:|")
    regress = 0
    improve = 0
    for r in rows:
        p_str = f"{r['prev_score']:.3f}" if r["prev_score"] is not None else "—"
        c_str = f"{r['curr_score']:.3f}" if r["curr_score"] is not None else "—"
        d = r["delta"]
        d_str = f"{d:+.3f}" if d is not None else "—"
        flag = ""
        if r["status"] == "new":
            flag = "NEW"
        elif r["status"] == "removed":
            flag = "DROP"
        elif d is not None:
            if d <= -threshold:
                flag = "⚠️ regress"
                regress += 1
            elif d >= threshold:
                flag = "↑ improve"
                improve += 1
        lines.append(f"| {r['question_id']} | {p_str} | {c_str} | {d_str} | {flag} |")
    lines.append("")
    lines.append(f"**Regressions (Δ ≤ -{threshold}): {regress}** · Improvements (Δ ≥ +{threshold}): {improve}")
    return "\n".join(lines)


def _latest_two(phase: str) -> tuple[Path, Path]:
    files = sorted(RESULTS_DIR.glob(PHASE_PATTERNS[phase]), key=lambda p: p.stat().st_mtime)
    if len(files) < 2:
        raise SystemExit(f"phase={phase} 至少需要两份 baseline JSON 才能 diff（找到 {len(files)} 份）")
    return files[-2], files[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff per-question scores between two baseline JSONs")
    parser.add_argument("prev", nargs="?", help="较早的 baseline JSON 路径")
    parser.add_argument("curr", nargs="?", help="较新的 baseline JSON 路径")
    parser.add_argument(
        "--phase", choices=list(PHASE_PATTERNS), help="自动取该 phase 最新两份 baseline"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.05, help="回归/改善判定阈值（默认 0.05）"
    )
    parser.add_argument("--out", type=Path, help="把 markdown 写入文件（默认 stdout）")
    args = parser.parse_args()

    if args.phase:
        prev_path, curr_path = _latest_two(args.phase)
    elif args.prev and args.curr:
        prev_path = Path(args.prev)
        curr_path = Path(args.curr)
    else:
        parser.error("需要 prev curr 两个位置参数，或使用 --phase 自动选取")

    md = render_diff(prev_path, curr_path, args.threshold)
    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"Diff → {args.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
