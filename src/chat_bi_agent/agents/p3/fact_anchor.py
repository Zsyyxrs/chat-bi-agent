"""P3 fact_anchor step: wraps P1 NL2SQL to anchor metric + period-over-period change."""

import re
from decimal import Decimal
from typing import Any, Literal

from chat_bi_agent.agents.p3.types import FactAnchor

# psycopg2 returns PostgreSQL NUMERIC/DECIMAL as Decimal; treat as numeric here.
_NUMERIC_TYPES = (int, float, Decimal)


def _compute_change(
    current: float,
    prior: float | None,
    flat_band_pct: float = 0.5,
) -> tuple[float, float | None, float | None, Literal["up", "down", "flat"]]:
    """Compute period-over-period change.

    Returns (current, prior, change_pct, direction).
    - prior is None → pct None, direction "flat".
    - prior is 0 → pct None, direction inferred from sign of current.
    - |pct| < flat_band_pct → direction "flat".
    """
    if prior is None:
        return current, None, None, "flat"
    if prior == 0:
        if current > 0:
            return current, prior, None, "up"
        if current < 0:
            return current, prior, None, "down"
        return current, prior, None, "flat"

    pct = (current - prior) / prior * 100.0
    if abs(pct) < flat_band_pct:
        direction: Literal["up", "down", "flat"] = "flat"
    elif pct > 0:
        direction = "up"
    else:
        direction = "down"
    return current, prior, pct, direction


_DATE_LITERAL_RE = re.compile(r"'(\d{4}-\d{2}-\d{2})'")


def _extract_time_window(sql: str) -> str:
    """Extract a 'YYYY-MM-DD to YYYY-MM-DD' string from SQL date literals (best-effort)."""
    dates = _DATE_LITERAL_RE.findall(sql or "")
    if not dates:
        return ""
    if len(dates) == 1:
        return dates[0]
    return f"{min(dates)} to {max(dates)}"


def _infer_metric_name(rows: list[dict]) -> str:
    """Use the first numeric column name as the metric label (placeholder).

    Real metric naming would require Metric Platform integration; for MVP we
    surface the SQL column name to the synthesizer (e.g. 'current_balance').
    """
    if not rows:
        return "unknown_metric"
    sample = rows[0]
    for col, val in sample.items():
        if isinstance(val, _NUMERIC_TYPES) and not isinstance(val, bool):
            return col
    return "unknown_metric"


def _extract_current_prior(rows: list[dict]) -> tuple[float | None, float | None]:
    """Look for paired (current/prior) numeric columns; fall back to first numeric only."""
    if not rows:
        return None, None
    sample = rows[0]
    cur, prior = None, None
    for col, val in sample.items():
        if not isinstance(val, _NUMERIC_TYPES) or isinstance(val, bool):
            continue
        lc = col.lower()
        if any(t in lc for t in ("prior", "prev", "last", "lastyear", "yoy", "mom_prev")):
            prior = float(val)
        elif cur is None:
            cur = float(val)
    return cur, prior


_FACT_ANCHOR_AUGMENT = (
    "\n\n【SQL 生成约束（来自 P3 fact_anchor）】\n"
    '本次查询是用于"环比/同比对比"的根因锚定，SQL 必须满足：\n'
    '1. 同时聚合"分析期"和"对照期"两个时间窗口（不是单期），'
    "通过 WITH 子句或 CASE WHEN 都可以。\n"
    "2. 输出列必须有 current_<metric> 和 prior_<metric> 两个聚合列"
    "（如 current_balance / prior_balance），下游会按列名前缀提取双值算 change_pct。\n"
    "3. 时间窗口选择规则：\n"
    '   - 题面明确给出"X 月某日前后/期间"等具体窗口 → 该窗口作为分析期，'
    "对照期取**紧接其前的等长窗口**（如分析期 6/20-7/4，对照期 6/6-6/19）。\n"
    '   - 题面只说"X 月中旬/月末"等模糊窗口 → 按 SQL 提示规则 9 解读。\n'
    '   - current 必须对应"分析期"（事件期间/题目询问的窗口），'
    'prior 必须对应"对照期"（事件前的基线），不能颠倒。\n'
    "4. 快照表（fct_holding 等只在月末有数据的表）必须用最近的月末作为窗口，"
    "不能用月中区间：分析期=问题月份的月末，对照期=上月末。"
    '示例：题面问"5 月中旬持仓下降" → current=snapshot_dt=5/31，prior=snapshot_dt=4/30。\n'
    "5. 单行输出汇总值即可，不需要按 GROUP BY 维度展开（维度拆解由后续 drill 完成）。\n"
    '6. 度量选择默认按"金额"语义解读，除非题面显式写"笔数/次数"：\n'
    '   - "交易量/支取量/存取量/转账量/消费额/收入" → SUM(amount)\n'
    '   - "交易笔数/支取次数/transactions count" → COUNT(*)\n'
    '   银行域里"量"类量词缺省都是金额，不是计数；选错会让信号完全消失。\n'
    "7. 题面若出现具体城市/分行名（如\"杭州/南京/上海/北京/深圳和XX分行\"），\n"
    "   **必须**在 WHERE 加分支过滤（JOIN dim_branch + WHERE b.city IN (...)"
    " 或 b.branch_name LIKE），**严禁**当作\"全行\"bank-wide 查询。\n"
    "   反例（fact anchor 漏 pin 会让信号被全行数据稀释甚至反向）：\n"
    "     题面 \"杭州和南京分行的定期存款余额增长\" → \n"
    "     错：SELECT SUM(balance)... WHERE product_subcategory='定期存款'（全行）\n"
    "     对：SELECT SUM(balance)... WHERE product_subcategory='定期存款' AND b.city IN ('杭州','南京')"
)

# 关键词判断是否需要给 P1 加 PoP augment。
# 触发：题面有明显"询问变化/异动"的语义（PoP 类）。
# 跳过：方法论/设计/建模类元问题——这类题加 dual-window 强约束反而毁掉。
_POP_KEYWORDS: tuple[str, ...] = (
    "为什么",
    "下降",
    "上升",
    "增长",
    "波动",
    "变化",
    "异动",
    "突增",
    "下行",
    "上行",
    "暴跌",
    "暴涨",
    "拐点",
    "断崖",
    "骤降",
    "骤升",
    "+",  # +12%
    "%",  # 百分比类比较
)
_META_KEYWORDS: tuple[str, ...] = (
    "如何",
    "如果我想",
    "设计",
    "建模",
    "可以基于哪些",
    "指标体系",
    "方法论",
    "预警模型",
    "建议指标",
)


def _should_augment(question: str) -> bool:
    """决定是否给 P1 加 PoP 双窗口约束。

    元问题（"如何设计指标体系/模型"）不能 augment——会让 LLM 强造一个无意义的 PoP SQL。
    PoP 问题（含变化/异动语义）必须 augment——否则 LLM 容易写单窗口 SQL，fact_anchor
    抓不到 prior。两者都没有则保守不 augment。
    """
    if any(kw in question for kw in _META_KEYWORDS):
        return False
    if any(kw in question for kw in _POP_KEYWORDS):
        return True
    return False


def run_fact_anchor(
    question_id: str,
    question: str,
    p1_agent: Any,
) -> FactAnchor | None:
    """Call P1NL2SQLAgent and convert its result into a FactAnchor.

    Returns None if P1 fails (caller decides whether to abort the RCA).
    For PoP-style questions, the question is augmented to force P1 to
    emit a dual-window comparison SQL with current_/prior_ columns;
    meta/methodology questions skip the augment to avoid degenerate SQL.
    """
    if _should_augment(question):
        prompt = question + _FACT_ANCHOR_AUGMENT
    else:
        prompt = question
    p1_result = p1_agent.run(question_id, prompt)
    if p1_result.rows is None or p1_result.sql is None:
        return None

    rows = p1_result.rows
    cur, prior = _extract_current_prior(rows)
    if cur is None:
        return None

    cur, prior, change_pct, direction = _compute_change(current=cur, prior=prior)
    return FactAnchor(
        metric_name=_infer_metric_name(rows),
        time_window=_extract_time_window(p1_result.sql),
        current_value=cur,
        prior_value=prior,
        change_pct=change_pct,
        direction=direction,
        sql=p1_result.sql,
        rows=rows,
    )
