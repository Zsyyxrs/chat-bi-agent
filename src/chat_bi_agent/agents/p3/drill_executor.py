"""P3 drill-down executor: per-dimension P1 call + Pareto TopN contribution."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from numbers import Real
from typing import Any

from chat_bi_agent.agents.p3.types import DrillRequest, DrillResult

# 注意：Decimal 不是 numbers.Real 的子类（Python 标准库历史遗留），
# psycopg2 把 NUMERIC 列还原成 Decimal，所以必须显式纳入。
_NUMERIC_TYPES = (int, float, Decimal, Real)


def _infer_value_col(rows: list[dict], dim_hint: str) -> str:
    """Return the numeric column best suited for attribution-style ranking.

    优先级：
      1. *_change（绝对变化）—— 归因下钻"谁推动了变化"的正解
      2. *_change_pct（相对变化）—— 次选，当绝对变化不存在时用
      3. current_*（当期值）—— 单窗 fallback，与旧行为兼容
      4. 任意非维度数值列 —— 兜底

    旧行为只取第一个数值列，碰到 4 列 PoP SQL 会拿到 current_<metric>，导致
    Pareto 排序退化成"当前规模 Top K"而非"变化贡献 Top K"。

    Raises ValueError if no numeric column found.
    """
    if not rows:
        raise ValueError("cannot infer value column from empty rows")
    sample = rows[0]
    numeric_cols = [
        c
        for c, v in sample.items()
        if c != dim_hint and isinstance(v, _NUMERIC_TYPES) and not isinstance(v, bool)
    ]
    if not numeric_cols:
        raise ValueError(f"no numeric column found in rows (sample keys={list(sample)})")

    abs_change = [c for c in numeric_cols if c.endswith("_change")]
    if abs_change:
        return abs_change[0]
    pct_change = [c for c in numeric_cols if c.endswith("_change_pct")]
    if pct_change:
        return pct_change[0]
    current_cols = [c for c in numeric_cols if c.startswith("current_")]
    if current_cols:
        return current_cols[0]
    return numeric_cols[0]


def _compute_pareto(
    rows: list[dict],
    value_col: str,
    threshold: float = 0.6,
    top_k_cap: int = 3,
    expected_sign: int = 0,
) -> list[dict]:
    """Sort rows by |value| desc, return top items until cum_share >= threshold or len >= top_k_cap.

    Returns: [{"key", "value", "share", "cum_share"}, ...]

    Rules:
      - Sort by |value| descending.
      - Total = sum of |value|. If total == 0, return [].
      - Stop when cum_share >= threshold OR len == top_k_cap (whichever first).
      - "key" is the first non-numeric field of each row (the dimension value).
      - expected_sign (+1/-1/0): 当 fact_anchor 给定整体变化方向时，先按方向过滤再
        排序——避免反向大额噪声（如 q006 BR_SUB_0000 跌 92637 vs 七夕活动涨 7200）
        抢占 Top1。0 表示无方向提示（保留旧行为）；过滤后若无匹配行则回退全集。
    """
    if not rows:
        return []

    # SQL NULL → dict value=None；不能用 r.get(col, 0.0) 兜底，得显式过滤。
    def _v(r: dict) -> float:
        v = r.get(value_col)
        return 0.0 if v is None else float(v)

    if expected_sign != 0:
        matched = [r for r in rows if (_v(r) > 0) == (expected_sign > 0) and _v(r) != 0]
        if matched:
            rows = matched

    total = sum(abs(_v(r)) for r in rows)
    if total == 0:
        return []

    sorted_rows = sorted(rows, key=lambda r: abs(_v(r)), reverse=True)

    out: list[dict] = []
    cum = 0.0
    for r in sorted_rows:
        val = _v(r)
        share = abs(val) / total
        cum += share
        # extract the "key": first non-numeric, non-value_col field
        key = None
        for k, v in r.items():
            if k == value_col:
                continue
            if not isinstance(v, _NUMERIC_TYPES) or isinstance(v, bool):
                key = v
                break
        out.append({"key": key, "value": val, "share": share, "cum_share": cum})
        if cum >= threshold or len(out) >= top_k_cap:
            break
    return out


_DRILL_AUGMENT = (
    "\n\n【SQL 生成约束（来自 P3 drill_down）】\n"
    '本次查询是"按维度拆解 + 环比对比"的归因下钻，SQL 必须满足：\n'
    "1. 必须 GROUP BY 下钻维度（dimension），每个维度值一行。\n"
    "2. 输出列必须同时包含 4 列：\n"
    "   - current_<metric>：分析期聚合值\n"
    "   - prior_<metric>：对照期聚合值\n"
    "   - <metric>_change：current - prior（绝对变化，可正可负）\n"
    "   - <metric>_change_pct：(current - prior) / NULLIF(prior, 0)（相对变化，小数形式）\n"
    "   下游按列名后缀提取 *_change 做归因排序；缺一不可。\n"
    "3. 时间窗口选择：\n"
    "   - nl_question 给出具体窗口 → 该窗口作分析期，对照期取**紧接其前的等长窗口**。\n"
    "   - current 必须对应分析期（事件期间/题面询问的窗口），prior 必须对应对照期。\n"
    "4. 维度筛选条件（WHERE 里的 branch_id / customer_tier / account_type 等）必须\n"
    "   完整继承 nl_question 给出的代码值（如 BR_CITY_0006、HIGH_NET_WORTH），\n"
    "   不要简化、翻译成中文、或自己造编码。\n"
    "5. 快照表（fct_holding 等只在月末有数据的表）必须用**单日等值**条件\n"
    "   （WHERE snapshot_dt = DATE 'YYYY-MM-DD'），禁止 BETWEEN 区间——\n"
    "   区间会同时聚合多个月末快照（如 4/30 + 5/31 都计入 current），导致双倍计数。\n"
    "   若 nl_question 仍写成 'YYYY-MM-DD 至 YYYY-MM-DD'，取区间右端点对应的月末作为单日。"
)


def _execute_single_drill(
    question_id: str,
    i: int,
    req: DrillRequest,
    p1_agent: Any,
    expected_sign: int = 0,
) -> DrillResult:
    """Execute one drill in isolation; safe for parallel use (no shared state)."""
    sub_qid = f"{question_id}__drill_{i}"
    p1_result = p1_agent.run(sub_qid, req.nl_question + _DRILL_AUGMENT)

    if p1_result.rows is None or p1_result.sql is None or not p1_result.rows:
        return DrillResult(
            dimension=req.dimension,
            nl_question=req.nl_question,
            sql=p1_result.sql or "",
            rows=p1_result.rows or [],
            pareto_top_k=[],
            error_class=p1_result.error_class,
            skipped=True,
        )

    try:
        value_col = _infer_value_col(p1_result.rows, dim_hint=req.dimension)
        top_k = _compute_pareto(
            p1_result.rows, value_col=value_col, expected_sign=expected_sign
        )
    except ValueError:
        return DrillResult(
            dimension=req.dimension,
            nl_question=req.nl_question,
            sql=p1_result.sql,
            rows=p1_result.rows,
            pareto_top_k=[],
            error_class=None,
            skipped=True,
        )

    return DrillResult(
        dimension=req.dimension,
        nl_question=req.nl_question,
        sql=p1_result.sql,
        rows=p1_result.rows,
        pareto_top_k=top_k,
        error_class=None,
        skipped=False,
    )


def run_drill_down(
    question_id: str,
    requests: list[DrillRequest],
    p1_agent: Any,
    expected_sign: int = 0,
) -> list[DrillResult]:
    """Execute each DrillRequest via P1 and compute Pareto TopN.

    Drills run in parallel via ThreadPoolExecutor since each P1 call is an
    independent LLM round-trip (~30-60s wall time). Sequential 3-4 drills
    took 120-240s; parallel runs in ~max-single-drill time (~30-60s).

    Each drill is independent: a failure in one does not stop the others.
    PoP augment is injected so drill SQL always emits 4-col current/prior/change
    output—没有这条约束 LLM 经常退化成单窗 SQL，下游归因 Pareto 没法按变化排序。
    """
    if not requests:
        return []

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        results = list(
            pool.map(
                lambda pair: _execute_single_drill(
                    question_id, pair[0], pair[1], p1_agent, expected_sign=expected_sign
                ),
                enumerate(requests),
            )
        )
    return results
