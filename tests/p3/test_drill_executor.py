"""Tests for run_drill_down (wraps P1NL2SQLAgent + Pareto)."""

from decimal import Decimal

from chat_bi_agent.agents.p3.drill_executor import run_drill_down
from chat_bi_agent.agents.p3.types import DrillRequest
from tests.p3.conftest import FakeP1Agent, FakeP1Result


def test_run_drill_down_happy():
    requests = [
        DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解"),
        DrillRequest(dimension="customer_tier", nl_question="按 customer_tier 拆解"),
    ]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, SUM(balance) FROM t GROUP BY branch_id",
                rows=[
                    {"branch_id": "BR_CITY_0006", "balance": 80.0},
                    {"branch_id": "BR_CITY_0002", "balance": 20.0},
                ],
            ),
            "qid__drill_1": FakeP1Result(
                question_id="qid__drill_1",
                sql="SELECT customer_tier, SUM(balance) FROM t GROUP BY customer_tier",
                rows=[
                    {"customer_tier": "HIGH_NET_WORTH", "balance": 81.0},
                    {"customer_tier": "AFFLUENT", "balance": 19.0},
                ],
            ),
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert len(results) == 2
    assert results[0].dimension == "branch_id"
    assert results[0].skipped is False
    assert results[0].pareto_top_k[0]["key"] == "BR_CITY_0006"
    assert results[1].pareto_top_k[0]["key"] == "HIGH_NET_WORTH"


def test_run_drill_down_p1_failure_marks_skipped():
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql=None,
                rows=None,
                execution_error="timeout",
                error_class="TIMEOUT",
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert len(results) == 1
    assert results[0].skipped is True
    assert results[0].error_class == "TIMEOUT"
    assert results[0].pareto_top_k == []


def test_run_drill_down_partial_failure_continues():
    requests = [
        DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解"),
        DrillRequest(dimension="customer_tier", nl_question="按 customer_tier 拆解"),
    ]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql=None,
                rows=None,
                execution_error="timeout",
                error_class="TIMEOUT",
            ),
            "qid__drill_1": FakeP1Result(
                question_id="qid__drill_1",
                sql="SELECT customer_tier, SUM(balance) FROM t GROUP BY customer_tier",
                rows=[{"customer_tier": "HIGH_NET_WORTH", "balance": 100.0}],
            ),
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is True
    assert results[1].skipped is False
    assert results[1].pareto_top_k[0]["key"] == "HIGH_NET_WORTH"


def test_run_drill_down_empty_rows_marks_skipped():
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, SUM(balance) FROM t GROUP BY branch_id",
                rows=[],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is True


def test_run_drill_down_accepts_decimal_values():
    # psycopg2 RealDictCursor returns NUMERIC/DECIMAL as Decimal — Decimal is NOT
    # a subclass of numbers.Real, so prior code skipped these as "non-numeric" and
    # _infer_value_col raised ValueError → skipped=True even when rows were healthy.
    requests = [DrillRequest(dimension="product_id", nl_question="按 product_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT product_id, SUM(balance) AS current_balance FROM t GROUP BY product_id",
                rows=[
                    {"product_id": "PROD_A", "current_balance": Decimal("5000.00")},
                    {"product_id": "PROD_B", "current_balance": Decimal("3000.00")},
                    {"product_id": "PROD_C", "current_balance": Decimal("2000.00")},
                ],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is False
    assert len(results[0].pareto_top_k) >= 1
    # top key should be PROD_A (largest), not None or the Decimal value
    assert results[0].pareto_top_k[0]["key"] == "PROD_A"


def test_run_drill_down_handles_null_value_col():
    # SQL NULL aggregates (e.g. AVG over empty filter group) come back as None;
    # _compute_pareto must coerce to 0 instead of crashing on abs(None).
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, AVG(balance) AS avg_bal FROM t GROUP BY branch_id",
                rows=[
                    {"branch_id": "BR_A", "avg_bal": 100.0},
                    {"branch_id": "BR_B", "avg_bal": None},  # NULL aggregate
                    {"branch_id": "BR_C", "avg_bal": 50.0},
                ],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is False
    keys = [item["key"] for item in results[0].pareto_top_k]
    assert "BR_A" in keys  # BR_B (None→0) should not dominate


def test_run_drill_down_prefers_change_over_current_for_pareto():
    # 4-列 PoP SQL：current_*, prior_*, *_change, *_change_pct。归因下钻必须按
    # *_change 排序（谁推动了变化），而不是按 current_* 排（谁规模最大）。
    # 旧 _infer_value_col 拿首个数值列 → 选 current_holding_amount → Top K 是
    # 当前规模 Top（如 q002 选到 PROD_WEA_0040 因为 current 19M 最大，但其 change
    # 只 +1.5%，不是归因主犯）。
    requests = [DrillRequest(dimension="product_id", nl_question="按 product_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql=(
                    "SELECT product_id, current_holding_amount, prior_holding_amount, "
                    "holding_amount_change, holding_amount_change_pct FROM t"
                ),
                rows=[
                    # current 最大但 change 小：旧逻辑会让它登顶
                    {
                        "product_id": "BIG_STABLE",
                        "current_holding_amount": Decimal("19000000"),
                        "prior_holding_amount": Decimal("18900000"),
                        "holding_amount_change": Decimal("100000"),
                        "holding_amount_change_pct": Decimal("0.0053"),
                    },
                    # current 小但 change 大：归因真凶，新逻辑应排第 1
                    {
                        "product_id": "SMALL_MOVER",
                        "current_holding_amount": Decimal("500000"),
                        "prior_holding_amount": Decimal("2000000"),
                        "holding_amount_change": Decimal("-1500000"),
                        "holding_amount_change_pct": Decimal("-0.75"),
                    },
                    {
                        "product_id": "MID_FLAT",
                        "current_holding_amount": Decimal("5000000"),
                        "prior_holding_amount": Decimal("5050000"),
                        "holding_amount_change": Decimal("-50000"),
                        "holding_amount_change_pct": Decimal("-0.0099"),
                    },
                ],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is False
    top = results[0].pareto_top_k
    assert top, "expected at least one Pareto row"
    assert top[0]["key"] == "SMALL_MOVER"  # ranked by |change|, not current_*


def test_run_drill_down_falls_back_to_change_pct_then_current():
    # 没有绝对 *_change 列 → 选 *_change_pct；都没 → 退回 current_*；都没 → 任意数值列。
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, current_balance, balance_change_pct FROM t",
                rows=[
                    {"branch_id": "BR_A", "current_balance": 100.0, "balance_change_pct": 0.10},
                    {"branch_id": "BR_B", "current_balance": 200.0, "balance_change_pct": -0.50},
                ],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1)
    assert results[0].skipped is False
    # BR_B's |change_pct| = 0.5 > BR_A's 0.1, so BR_B leads even though BR_A has bigger current
    assert results[0].pareto_top_k[0]["key"] == "BR_B"


def _q006_like_p1():
    # q006 真实情形：杭州/南京 × SAVING drill 返回 3 行；
    # 期望事件方向是 + (七夕活动拉升)，但有一个噪声分行跌幅远大于事件涨幅。
    # 若不传 expected_sign，pareto top1 会被噪声分行抢走（|change| 最大）。
    return FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, current_balance, prior_balance, balance_change FROM t",
                rows=[
                    {
                        "branch_id": "BR_SUB_0000",
                        "current_balance": Decimal("67378"),
                        "prior_balance": Decimal("160015"),
                        "balance_change": Decimal("-92637"),
                    },
                    {
                        "branch_id": "BR_CITY_0000",
                        "current_balance": Decimal("107200"),
                        "prior_balance": Decimal("100000"),
                        "balance_change": Decimal("7200"),
                    },
                    {
                        "branch_id": "BR_CITY_0002",
                        "current_balance": Decimal("107200"),
                        "prior_balance": Decimal("100000"),
                        "balance_change": Decimal("7200"),
                    },
                ],
            )
        }
    )


def test_sign_aware_filters_out_opposite_direction_outlier():
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    results = run_drill_down(
        question_id="qid",
        requests=requests,
        p1_agent=_q006_like_p1(),
        expected_sign=1,  # 事件方向 + (上涨)
    )
    keys = [item["key"] for item in results[0].pareto_top_k]
    # 噪声 BR_SUB_0000 (-92637) 被剔除；剩两个 + 方向分行进 top_k
    assert "BR_SUB_0000" not in keys
    assert set(keys) == {"BR_CITY_0000", "BR_CITY_0002"}


def test_no_sign_hint_keeps_legacy_behavior():
    # 不传 expected_sign 时，仍按 |value| 排序，BR_SUB_0000 是 top1
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=_q006_like_p1())
    assert results[0].pareto_top_k[0]["key"] == "BR_SUB_0000"


def test_sign_aware_falls_back_when_no_matching_rows():
    # 期望 +，但所有行都是 -：fallback 到原逻辑，不丢数据
    requests = [DrillRequest(dimension="branch_id", nl_question="按 branch_id 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT branch_id, balance_change FROM t",
                rows=[
                    {"branch_id": "BR_X", "balance_change": Decimal("-100")},
                    {"branch_id": "BR_Y", "balance_change": Decimal("-50")},
                ],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1, expected_sign=1)
    keys = [item["key"] for item in results[0].pareto_top_k]
    assert "BR_X" in keys  # 没有 + 方向行 → 回退到全行排序，top1 还是最大 |change|


def test_sign_aware_negative_direction():
    # 期望 - 方向（如安鑫到期），剔除正向噪声
    requests = [DrillRequest(dimension="customer_tier", nl_question="按 customer_tier 拆解")]
    p1 = FakeP1Agent(
        responses={
            "qid__drill_0": FakeP1Result(
                question_id="qid__drill_0",
                sql="SELECT customer_tier, balance_change FROM t",
                rows=[
                    {"customer_tier": "BASIC", "balance_change": Decimal("500")},
                    {"customer_tier": "HIGH_NET_WORTH", "balance_change": Decimal("-200")},
                    {"customer_tier": "AFFLUENT", "balance_change": Decimal("-150")},
                ],
            )
        }
    )
    results = run_drill_down(question_id="qid", requests=requests, p1_agent=p1, expected_sign=-1)
    keys = [item["key"] for item in results[0].pareto_top_k]
    assert "BASIC" not in keys  # 正向噪声剔除
    assert results[0].pareto_top_k[0]["key"] == "HIGH_NET_WORTH"
