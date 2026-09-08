"""用已落盘的 P1 产物离线重放评分，不跑 agent、不调 LLM。

为什么需要它：改一次评分器就要重跑全量 eval 的话，等于每次修 bug 都要付一次
LLM 成本，还要承受跑间噪声（P1 同配置反复跑 avg 落在 0.965~0.977，单题波动可达
±0.4）——那样根本分不清「分数变了」是因为修好了评分器还是因为模型这次手气好。

P1 可以做到完全确定性的重放：产物里存了每题**生成的 SQL**，把它重新打一次库
就能拿到结果集，评分器的其余入参都在题库里。全程零 LLM 调用。
这与 `replay_p2_scoring.py` 是同一个思路（P2 因单题 300~500s 而需要它，
P1 则是为了不让评分器的修复动到模型这一侧的变量）。

前提：docker compose 已起、chatbi-pg healthy、且库里是产物生成时的同一批数据。
重灌过数据的话行数会变，重放结果不可当真。

用法：
    python scripts/replay_p1_scoring.py results/baseline_p1_eval_2026-08-15.json
    python scripts/replay_p1_scoring.py <产物> --write results/xxx.restated.json
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "src"))

from chat_bi_agent.agents.shared.sql_executor import SQLExecutor  # noqa: E402
from chat_bi_agent.eval.precision_retrieval_evaluator import (  # noqa: E402
    PrecisionRetrievalEvaluator,
)

DIMS = (
    ("table_score", "表选择"),
    ("filter_accuracy", "过滤"),
    ("column_score", "列选择"),
)
FLAGS = (
    ("aggregation_correct", "聚合"),
    ("result_count_correct", "行数"),
    ("sql_syntactically_correct", "语法"),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artifact", type=Path, help="P1 baseline JSON")
    ap.add_argument("--write", type=Path, default=None, help="把重述后的产物写到此路径")
    args = ap.parse_args()

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    per_q = payload.get("per_question", [])
    if not per_q:
        print(f"产物里没有 per_question：{args.artifact}", file=sys.stderr)
        return 1

    executor = SQLExecutor()
    ev = PrecisionRetrievalEvaluator(gold_executor=executor)

    print(f"重放 {args.artifact.name}（{len(per_q)} 题，零 LLM 调用）\n")
    print(f"{'题':<16}{'原分':>8}{'重放':>9}{'差':>9}   变化的维度")

    restated: list[dict] = []
    old_sum = new_sum = 0.0
    n_scored = 0
    for q in per_q:
        qid, sql = q.get("question_id"), q.get("sql")
        old = q.get("score")
        if not sql or old is None:
            # 被拒绝渲染 / 未产出 SQL 的题没法重放，原样保留
            restated.append(dict(q))
            print(f"{qid:<16}{'—':>8}{'—':>9}{'—':>9}   跳过（产物里没有 SQL）")
            continue

        rows, err = executor.execute(sql)
        s = ev.evaluate_response(qid, sql, rows, err)
        new = round(s.combined_score, 4)

        changed = []
        for attr, label in DIMS + FLAGS:
            if attr in q and q[attr] != getattr(s, attr):
                changed.append(f"{label} {q[attr]}→{getattr(s, attr)}")
        row = dict(q)
        row.update(
            {
                "score": new,
                "score_before_replay": old,
                "result_match": s.result_match,
                "group_by_match": s.group_by_match,
            }
        )
        restated.append(row)
        old_sum += old
        new_sum += new
        n_scored += 1
        delta = new - old
        mark = "" if abs(delta) < 1e-9 else ("  ← " + ("、".join(changed) if changed else "变了"))
        print(f"{qid:<16}{old:>8.4f}{new:>9.4f}{delta:>+9.4f}{mark}")

    if n_scored:
        print(
            f"\navg  {old_sum / n_scored:.4f} → {new_sum / n_scored:.4f}"
            f"  （{n_scored} 题参与重放）"
        )

    if args.write:
        out = dict(payload)
        out["per_question"] = restated
        if n_scored:
            out["avg_score"] = round(new_sum / n_scored, 4)
        out["replayed_from"] = str(args.artifact)
        out["replay_note"] = (
            "离线重放评分（不调 LLM，SQL 取自原产物并重打真库）。"
            "avg_score 已按当前评分器重述；原值见每题 score_before_replay。"
        )
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已写出重述产物 → {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
