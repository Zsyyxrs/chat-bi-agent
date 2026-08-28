#!/usr/bin/env python3
"""Bootstrap 生产 Q-SQL 池：从 P1 eval YAML+baseline JSON、Langfuse trace 拉 pass 样本。

设计原则：
- **同域 pool**（中文银行 schema，dialect=postgres）——与 BIRD 跨库池 严格隔离
- **来源多路可合并**：
    --source p1_eval   从 YAML fixture (question) + baseline JSON (passing SQL) 建
    --source langfuse  从 Langfuse trace 拉 score=1 的 (question, sql)
    --source both      合并去重（默认）
- **只灌 pass 样本**：确定这条 SQL 是"能正确解出该问题"的 → 不能进负例
- **gold 执行门禁**（默认开）：只认 👍 是不够的。每条 gold 打到真库，报错/空集/
  聚合没匹配到任何行的一律拒收。`d525ae0fc0e3` 是被 👍 过、进了池子、又在
  2026-08-28 被人工剔除的样本——它的 gold 返回 `alert_count = 0`，因为
  `dim_branch.city` 根本没有 '上海'。这种样本当 few-shot 有害：它教模型去
  filter 一个库里不存在的值
- **样本量说明**：写这个脚本时 P1 eval 只有 6 gold 题——n 太小做 A/B 无意义；
  真正等 Langfuse 攒够 30+ 条用户 👍 样本后才有统计意义（见 ADR-012 后续跟进）

用法：
    python scripts/bootstrap_prod_pool.py                        # both, 默认
    python scripts/bootstrap_prod_pool.py --source p1_eval       # 只 P1 baseline
    python scripts/bootstrap_prod_pool.py --source langfuse      # 只 Langfuse
    python scripts/bootstrap_prod_pool.py --dry-run              # 不 embed 不写
    python scripts/bootstrap_prod_pool.py --langfuse-days-back 30
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

from chat_bi_agent.agents.shared.example_retriever import (
    ExamplePool,
    QAExample,
    compute_example_id,
)

DEFAULT_P1_YAML = REPO_ROOT / "src/chat_bi_agent/data/precision_retrieval_evaluation.yaml"
DEFAULT_P1_BASELINE = REPO_ROOT / "results/baseline_p2_validator_reflector_2026-06-03.json"
DEFAULT_OUTPUT = REPO_ROOT / "data/example_pool_prod.jsonl"
EMBED_BATCH_SIZE = 10


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--source",
        choices=["p1_eval", "langfuse", "both"],
        default="both",
    )
    p.add_argument("--p1-yaml", type=Path, default=DEFAULT_P1_YAML)
    p.add_argument("--p1-baseline", type=Path, default=DEFAULT_P1_BASELINE)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument(
        "--score-threshold",
        type=float,
        default=1.0,
        help="P1 baseline / Langfuse score ≥ 此值才纳入 pool（默认 1.0 = 严格 pass）",
    )
    p.add_argument(
        "--langfuse-days-back",
        type=int,
        default=30,
        help="拉最近 N 天的 Langfuse trace（默认 30）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="不 embed 不写盘，只报告将会灌入几条",
    )
    p.add_argument(
        "--no-verify-gold",
        action="store_true",
        help=(
            "跳过 gold 执行门禁（默认开启：把每条 gold 打到真库，什么都没解出来的不收）。"
            "只在没有 PG 可连时用，且会打印警告——关掉它就等于回到「只认 👍」"
        ),
    )
    return p.parse_args()


def load_p1_eval(yaml_path: Path, baseline_path: Path, score_threshold: float) -> list[QAExample]:
    """从 P1 eval YAML + baseline JSON 抽 pass 样本。

    YAML 提供 question 文本；baseline JSON 提供实际预测 SQL + score。
    只保留 score >= score_threshold 的样本；SQL 用实际 predicted（这是模型上次能
    解开该题的自我风格，比 YAML 的 expected_sql 更贴合生产 model 行为）。
    """
    if not yaml_path.exists():
        print(f"[prod-pool] p1_eval YAML 不存在: {yaml_path}", file=sys.stderr)
        return []
    if not baseline_path.exists():
        print(f"[prod-pool] p1_eval baseline JSON 不存在: {baseline_path}", file=sys.stderr)
        return []

    yaml_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    qs_by_id = {q["id"]: q for q in yaml_data.get("evaluation_questions", [])}

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    ts = dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    examples: list[QAExample] = []
    skipped_low_score = skipped_no_sql = skipped_no_yaml = 0
    for row in baseline.get("per_question", []):
        qid = row.get("question_id")
        score = float(row.get("score") or 0.0)
        sql = (row.get("sql") or "").strip()

        if score < score_threshold:
            skipped_low_score += 1
            continue
        if not sql:
            skipped_no_sql += 1
            continue
        yaml_q = qs_by_id.get(qid)
        if yaml_q is None:
            skipped_no_yaml += 1
            continue
        question = yaml_q["question"].strip()
        examples.append(
            QAExample(
                example_id=compute_example_id(question, sql),
                question=question,
                sql=sql,
                dialect="postgres",
                source=f"p1_eval_baseline:{qid}",
                tags=["prod", "p1_eval_gold"],
                ts=ts,
                embedding=None,
            )
        )

    print(
        f"[prod-pool] p1_eval: {len(examples)} 条纳入 "
        f"(跳过 score<{score_threshold}: {skipped_low_score}, no_sql: {skipped_no_sql}, no_yaml: {skipped_no_yaml})",
        flush=True,
    )
    return examples


def load_langfuse(days_back: int, score_threshold: float) -> list[QAExample]:
    """从 Langfuse 拉 P1 trace + user_feedback/judge_pass score ≥ threshold 的样本。

    当前状态：反馈闭环（#3）还没上线，Langfuse 里不会有 user_feedback score。
    本函数留接口占位，能连上 Langfuse 就试；连不上 or 0 条就返回空列表，不报错。
    """
    try:
        from chat_bi_agent.llm.langfuse_setup import get_client
    except Exception as e:
        print(f"[prod-pool] langfuse: 无法 import langfuse_setup — 跳过 ({e})", flush=True)
        return []

    try:
        client = get_client()
    except Exception as e:
        print(f"[prod-pool] langfuse: client 初始化失败 — 跳过 ({e})", flush=True)
        return []

    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_back)
    try:
        # Langfuse Python SDK 提供 fetch_traces；不同版本 API 略有差异，
        # 用 client.api.trace.list 作为最底层入口。
        # 过滤：name 是 p1_nl2sql_run（P1 agent trace 名），时间 >= since
        traces = client.api.trace.list(
            name="p1_nl2sql_run",
            from_timestamp=since,
        )
        trace_list = list(getattr(traces, "data", traces) or [])
    except Exception as e:
        print(f"[prod-pool] langfuse: trace 拉取失败 — 跳过 ({e})", flush=True)
        return []

    if not trace_list:
        print(
            f"[prod-pool] langfuse: 最近 {days_back} 天没有 p1_nl2sql_run trace "
            "（等 #3 反馈闭环上线 + 用户使用 Streamlit 后自然攒起）",
            flush=True,
        )
        return []

    ts = dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    examples: list[QAExample] = []
    for tr in trace_list:
        # trace.scores 是分数 ID 列表（字符串），需逐个用 score_v_2.get_by_id 拉详情。
        score_ids = getattr(tr, "scores", None) or []
        pass_score = 0.0
        for sid in score_ids:
            try:
                s = client.api.score_v_2.get_by_id(score_id=sid)
            except Exception:
                continue
            if getattr(s, "name", "") in ("user_feedback", "judge_pass"):
                val = getattr(s, "value", None)
                if val is not None:
                    pass_score = max(pass_score, float(val))
        if pass_score < score_threshold:
            continue
        # trace.input 结构由 langfuse @observe 装饰器生成：
        #   {"args": [...positional], "kwargs": {"question": "..."}}
        # Streamlit 里 P1 用 question=... 关键字调用，所以从 kwargs 取。
        raw_input = getattr(tr, "input", None) or {}
        question = None
        if isinstance(raw_input, dict):
            kwargs = raw_input.get("kwargs") or {}
            question = raw_input.get("question") or (
                kwargs.get("question") if isinstance(kwargs, dict) else None
            )
        if not question:
            continue
        # output 一般是 P1AgentResult dict-ified
        raw_output = getattr(tr, "output", None) or {}
        sql = (raw_output.get("sql") if isinstance(raw_output, dict) else None) or ""
        if not sql:
            continue
        examples.append(
            QAExample(
                example_id=compute_example_id(question, sql),
                question=question,
                sql=sql,
                dialect="postgres",
                source=f"langfuse:{getattr(tr, 'id', 'unknown')}",
                tags=["prod", "langfuse_user_pass"],
                ts=ts,
                embedding=None,
            )
        )
    print(f"[prod-pool] langfuse: {len(examples)} 条纳入", flush=True)
    return examples


def dedup_by_id(examples: list[QAExample]) -> list[QAExample]:
    seen: set[str] = set()
    out: list[QAExample] = []
    for ex in examples:
        if ex.example_id in seen:
            continue
        seen.add(ex.example_id)
        out.append(ex)
    return out


def gold_yielded_nothing(rows: list[dict] | None, err: str | None) -> str | None:
    """判断这条 gold 是不是「什么都没解出来」。返回原因，None = 有数据。

    三种都算没解出来：

    1. SQL 报错
    2. 结果集为空
    3. **单行、且每个值都是 0 / NULL** —— 聚合匹配不到任何行时长这样。
       `d525ae0fc0e3` 就是这一类：`COUNT(...)` 返回单行 `{'alert_count': 0}`，
       只判空集会漏掉它，而它恰恰是这个门禁存在的全部理由。

    第 3 条只在单行时生效。分组结果里某一组是 0 是正常的（「各渠道笔数」里
    某渠道为 0），多行一律放行，不误杀。
    """
    if err is not None:
        return f"SQL 报错：{err}"
    if not rows:
        return "结果集为空——SQL 跑得通但没有任何行匹配"
    if len(rows) == 1 and all(v in (0, None) or v == 0 for v in rows[0].values()):
        return f"聚合没有匹配到任何行（单行全零/全空）：{rows[0]}"
    return None


def reject_empty_gold(examples, execute_fn):
    """把 gold 打到真库，滤掉什么都没解出来的。返回 (留下, [(丢掉, 原因)])。

    **只认 👍 是不够的。** `d525ae0fc0e3` 是被 👍 过、进了池子、又在 2026-08-28
    被人工剔除的样本——有人给一个返回 0 行的答案点了赞。返回空的样本当 few-shot
    是有害的：它教模型去 filter 一个库里不存在的值。

    `execute_fn` 抛异常时**不接**，直接让它冒上去。探针连不上就静默放行等于门禁
    自动失效——宁可让 nightly cron 失败，也不要往池子里灌未经验证的样本。
    """
    kept, rejected = [], []
    for ex in examples:
        rows, err = execute_fn(ex.sql)
        reason = gold_yielded_nothing(rows, err)
        if reason is None:
            kept.append(ex)
        else:
            rejected.append((ex, reason))
    return kept, rejected


def batch_embed(examples: list[QAExample], batch_size: int) -> None:
    """就地填充 examples 的 embedding。"""
    from chat_bi_agent.llm import qwen_client

    for i in range(0, len(examples), batch_size):
        batch = examples[i : i + batch_size]
        vecs = qwen_client.embed([ex.question for ex in batch])
        for ex, v in zip(batch, vecs):
            ex.embedding = v
        print(
            f"[prod-pool] embedded {min(i + batch_size, len(examples))}/{len(examples)}",
            flush=True,
        )


def main() -> int:
    args = parse_args()
    examples: list[QAExample] = []

    if args.source in ("p1_eval", "both"):
        examples += load_p1_eval(args.p1_yaml, args.p1_baseline, args.score_threshold)
    if args.source in ("langfuse", "both"):
        examples += load_langfuse(args.langfuse_days_back, args.score_threshold)

    unique = dedup_by_id(examples)
    dupes = len(examples) - len(unique)
    print(
        f"[prod-pool] 合计 {len(unique)} 条 unique（去重 {dupes} 条）",
        flush=True,
    )

    if not unique:
        print(
            "[prod-pool] 无样本可灌入；先跑 P1 eval 生成 baseline 或等 Langfuse 攒数据",
            flush=True,
        )
        return 0

    # gold 执行门禁：放在 embedding 之前，被拒的样本一分钱 embedding 都不花
    if args.no_verify_gold:
        print(
            "[prod-pool] ⚠ gold 执行门禁已关闭——只认 👍，不验证 SQL 真的解出了东西。"
            "d525ae0fc0e3 就是这么混进池子的",
            flush=True,
        )
    else:
        from chat_bi_agent.agents.shared.sql_executor import SQLExecutor

        print(f"[prod-pool] gold 执行门禁：把 {len(unique)} 条 gold 打到真库 ...", flush=True)
        unique, rejected = reject_empty_gold(unique, SQLExecutor().execute)
        if rejected:
            print(f"[prod-pool] ✗ 拒收 {len(rejected)} 条（gold 什么都没解出来）：")
            for ex, reason in rejected:
                print(f"    {ex.example_id} [{ex.source}] {ex.question[:44]}")
                print(f"      {reason}")
        print(f"[prod-pool] ✓ 通过 {len(unique)} 条", flush=True)
        if not unique:
            print("[prod-pool] 全部被拒，无样本可灌入")
            return 0

    if args.dry_run:
        print("[prod-pool] --dry-run：跳过 embedding + 落盘")
        for ex in unique[:5]:
            print(f"  - {ex.example_id} [{ex.source}] {ex.question[:60]}")
        if len(unique) > 5:
            print(f"  ... 以及另外 {len(unique) - 5} 条")
        return 0

    print(f"[prod-pool] 开始 embedding via DashScope (batch={EMBED_BATCH_SIZE}) ...", flush=True)
    batch_embed(unique, EMBED_BATCH_SIZE)

    # 若 output 已存在，合并去重（保留旧的 embedding，追加新样本）
    if args.output.exists():
        old = ExamplePool.load(args.output)
        merged = ExamplePool(old.examples + unique)
        merged.save(args.output)
        added = len(merged) - len(old)
        print(f"[prod-pool] 合并写入 {args.output}: 原 {len(old)} + 新 {added}")
    else:
        pool = ExamplePool(unique)
        pool.save(args.output)
        print(f"[prod-pool] 新建并写入 {len(pool)} 条到 {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
