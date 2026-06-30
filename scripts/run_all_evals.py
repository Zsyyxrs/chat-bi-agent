"""一键跑齐 P1+P2+P3 评估并生成汇总 markdown 报告。

Usage:
    python scripts/run_all_evals.py                     # 跑齐 3 个 phase
    python scripts/run_all_evals.py --only p3           # 只跑 P3
    python scripts/run_all_evals.py --skip p1 --skip p2 # 跳过 P1/P2
    python scripts/run_all_evals.py --p3-limit 2        # P3 --limit 透传（debug）
    python scripts/run_all_evals.py --report-only       # 不跑 eval，仅基于现有最新 baseline 出报告

输出：
    results/eval_report_<YYYY-MM-DD>.md

注意：
    - P1 runner 历史遗留，输出文件名是 baseline_p2_validator_reflector_*.json（pattern 兼容）。
    - 任一 phase 失败不会阻断后续 phase；最终报告标注 missing。
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"

PHASES: dict[str, dict[str, Any]] = {
    "p1": {
        "module": "chat_bi_agent.runners.run_p1_eval",
        "pattern": "baseline_p2_validator_reflector_*.json",
        "label": "P1 NL2SQL",
    },
    "p2": {
        "module": "chat_bi_agent.runners.run_p2_eval",
        "pattern": "baseline_p2_analysis_*.json",
        "label": "P2 Multi-Step Analysis",
    },
    "p3": {
        "module": "chat_bi_agent.runners.run_p3_eval",
        "pattern": "baseline_p3_rca_*.json",
        "label": "P3 RCA",
    },
}


def run_phase(phase: str, extra_args: list[str]) -> int:
    cmd = [sys.executable, "-m", PHASES[phase]["module"], *extra_args]
    print(f"\n{'=' * 64}\n[{phase}] {' '.join(cmd)}\n{'=' * 64}", flush=True)
    return subprocess.call(cmd, cwd=REPO_ROOT)


def latest_baseline(pattern: str) -> Path | None:
    files = sorted(RESULTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _question_score(q: dict) -> float:
    """所有 phase 的 per_question 都有 score（可能是 float 或嵌套 dict）。"""
    s = q.get("score")
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, dict):
        for k in ("overall_score", "overall", "total"):
            v = s.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return 0.0


def render_report(phase_results: dict[str, dict | None]) -> str:
    lines: list[str] = []
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"# Eval Report — {ts}\n")

    lines.append("## Summary\n")
    lines.append("| Phase | Label | Total | Passed | Avg Score | Source |")
    lines.append("|-------|-------|------:|-------:|----------:|--------|")
    for phase in PHASES:
        data = phase_results.get(phase)
        label = PHASES[phase]["label"]
        if data is None:
            lines.append(f"| {phase} | {label} | — | — | — | (no baseline) |")
            continue
        d = data["json"]
        extra = ""
        if "event_hit_count" in d:
            extra = f" · event_hit {d['event_hit_count']}/{d['total_questions']}"
        lines.append(
            f"| {phase} | {label} | "
            f"{d['total_questions']} | {d['passed_questions']} | "
            f"{d['avg_score']:.3f}{extra} | `{data['path'].name}` |"
        )
    lines.append("")

    for phase in PHASES:
        data = phase_results.get(phase)
        if data is None:
            continue
        d = data["json"]
        lines.append(f"## {PHASES[phase]['label']}\n")
        lines.append(f"- baseline_id: `{d['baseline_id']}`")
        lines.append(f"- ran_at: {d['ran_at']}")
        lines.append(f"- total / passed: {d['total_questions']} / {d['passed_questions']}")
        lines.append(f"- avg_score: **{d['avg_score']:.3f}**")
        if "event_hit_count" in d:
            lines.append(f"- event_hit: {d['event_hit_count']} / {d['total_questions']}")
        lines.append("")
        lines.append("| question_id | score | latency_ms |")
        lines.append("|-------------|------:|-----------:|")
        for q in d.get("per_question", []):
            qid = q.get("question_id", "?")
            score = _question_score(q)
            latency = q.get("latency_ms", 0)
            lines.append(f"| {qid} | {score:.3f} | {int(latency)} |")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run P1/P2/P3 evals end-to-end + aggregate markdown report",
    )
    parser.add_argument("--only", choices=list(PHASES), help="只跑某一个 phase")
    parser.add_argument(
        "--skip",
        choices=list(PHASES),
        action="append",
        default=[],
        help="跳过某个 phase（可重复）",
    )
    parser.add_argument("--p3-limit", type=int, help="P3 runner --limit 透传")
    parser.add_argument(
        "--report-only", action="store_true", help="不跑 eval，仅基于现有最新 baseline 出报告"
    )
    args = parser.parse_args()

    if args.only:
        to_run = [args.only]
    else:
        to_run = [p for p in PHASES if p not in args.skip]

    if not args.report_only:
        for phase in to_run:
            extra: list[str] = []
            if phase == "p3" and args.p3_limit is not None:
                extra = ["--limit", str(args.p3_limit)]
            rc = run_phase(phase, extra)
            if rc != 0:
                print(f"[{phase}] runner exit code {rc} — 继续后续 phase", file=sys.stderr)

    phase_results: dict[str, dict | None] = {}
    for phase in PHASES:
        path = latest_baseline(PHASES[phase]["pattern"])
        if path is None:
            phase_results[phase] = None
            continue
        phase_results[phase] = {
            "path": path,
            "json": json.loads(path.read_text(encoding="utf-8")),
        }

    report = render_report(phase_results)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"eval_report_{datetime.now(UTC).strftime('%Y-%m-%d')}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
