"""结果集比对：让评分器看见"语义不忠实"。

动机：mr_n12 问"存款余额最高的前 5 个分行"，语义层丢掉 Top-5 返回全部分行，
但表/过滤/聚合三项全对，combined_score 只扣 0.075——分数根本暴露不了它，
是逐题读 SQL 才发现的。

刻意**不计入 combined_score**：现有权重和为 1.0，加权重维度会改变所有历史分数、
废掉 baseline 可比性。这里只做诊断字段；是否并入总分是一个需要重建基线的独立决定。
"""

from unittest.mock import MagicMock

from chat_bi_agent.eval.precision_retrieval_evaluator import PrecisionRetrievalEvaluator


def _ev(gold_rows, err=None):
    ex = MagicMock()
    ex.execute = MagicMock(return_value=(gold_rows, err))
    return PrecisionRetrievalEvaluator(gold_executor=ex)


def test_result_match_none_without_executor():
    """不注入 executor 时不做比对，保持原行为。"""
    ev = PrecisionRetrievalEvaluator()
    s = ev.evaluate_response("precision_q001", "SELECT 1", [{"a": 1}], None)
    assert s.result_match is None


def test_result_match_true_on_identical_rows():
    ev = _ev([{"branch_id": "B1", "n": 10}])
    s = ev.evaluate_response("precision_q001", "SELECT 1", [{"branch_id": "B1", "n": 10}], None)
    assert s.result_match is True


def test_result_match_ignores_row_order():
    """行序不该影响判定——SQL 无 ORDER BY 时行序本就不保证。"""
    ev = _ev([{"b": "X", "n": 1}, {"b": "Y", "n": 2}])
    s = ev.evaluate_response(
        "precision_q001", "SELECT 1", [{"b": "Y", "n": 2}, {"b": "X", "n": 1}], None
    )
    assert s.result_match is True


def test_result_match_ignores_column_names():
    """gold 与生成 SQL 的列别名常常不同，比的是值不是列名。"""
    ev = _ev([{"branch_id": "B1", "avg_balance": 100.0}])
    s = ev.evaluate_response(
        "precision_q001", "SELECT 1", [{"bid": "B1", "avg_deposit_balance": 100.0}], None
    )
    assert s.result_match is True


def test_result_match_false_on_extra_rows():
    """mr_n12 的真实情形：该返 5 行却返了全部。"""
    gold = [{"b": f"B{i}", "n": i} for i in range(5)]
    actual = [{"b": f"B{i}", "n": i} for i in range(50)]
    s = _ev(gold).evaluate_response("precision_q001", "SELECT 1", actual, None)
    assert s.result_match is False


def test_result_match_tolerates_float_noise():
    """浮点末位差异不算不一致。"""
    ev = _ev([{"v": 100.00000001}])
    s = ev.evaluate_response("precision_q001", "SELECT 1", [{"v": 100.0}], None)
    assert s.result_match is True


def test_result_match_false_on_different_value():
    ev = _ev([{"v": 100.0}])
    s = ev.evaluate_response("precision_q001", "SELECT 1", [{"v": 101.0}], None)
    assert s.result_match is False


def test_result_match_none_when_gold_sql_fails():
    """gold SQL 自己跑不通时不下结论，避免误判 agent。"""
    ev = _ev(None, err="relation does not exist")
    s = ev.evaluate_response("precision_q001", "SELECT 1", [{"v": 1}], None)
    assert s.result_match is None


def test_result_match_does_not_change_combined_score():
    """诊断字段不得影响总分——否则历史 baseline 全部失效。"""
    sql = "SELECT branch_id, COUNT(*) AS n FROM dim_customer GROUP BY branch_id"
    rows = [{"branch_id": "B1", "n": 1}]
    plain = PrecisionRetrievalEvaluator().evaluate_response("precision_q001", sql, rows, None)
    with_match = _ev([{"branch_id": "ZZZ", "n": 999}]).evaluate_response(
        "precision_q001", sql, rows, None
    )
    assert with_match.result_match is False
    assert with_match.combined_score == plain.combined_score


# ---- 日期/时间戳的表示差异不该算作"结果不一致" ----
#
# 2026-09-08 实测（新增窗口函数题 q012）：gold 写 `DATE_TRUNC('month', dt)` 返回
# timestamptz，模型写 `DATE_TRUNC('month', dt)::DATE` 返回 date，**两边金额逐行完全
# 相同**，却被判 result_match=False——归一化对非数值列走 str(v)，
# "2026-01-01 00:00:00+08:00" != "2026-01-01"。
#
# 这是类级缺陷：任何 gold 返回 timestamp 列的题都会中招。且因为 result_match 不计入
# combined_score，它误判时分数看不出来，只有诊断字段是红的——「有测试、还是绿的，
# 反而让人以为已经防住了」的又一种形态。


def test_date_and_midnight_timestamp_of_the_same_day_are_equal():
    """月/日粒度聚合最常见的一对写法，语义相同就不该判为不一致。"""
    from datetime import date, datetime, timedelta, timezone

    tz8 = timezone(timedelta(hours=8))
    ev = _ev([{"month": datetime(2026, 1, 1, 0, 0, tzinfo=tz8), "amt": 3279010.57}])
    s = ev.evaluate_response(
        "precision_q001", "SELECT 1", [{"txn_month": date(2026, 1, 1), "amt": 3279010.57}], None
    )
    assert s.result_match is True


def test_timestamps_with_a_real_time_of_day_still_compare_by_their_time():
    """只归一"零点"这一种情形。带真实时分秒的时间戳必须照旧逐值比，
    否则会把「按天聚合」和「按小时聚合」判成相同。"""
    from datetime import datetime

    ev = _ev([{"t": datetime(2026, 1, 1, 9, 30)}])
    s = ev.evaluate_response(
        "precision_q001", "SELECT 1", [{"t": datetime(2026, 1, 1, 14, 0)}], None
    )
    assert s.result_match is False


def test_different_days_are_still_different():
    """归一化不能宽到把不同日期抹平。"""
    from datetime import date, datetime

    ev = _ev([{"d": datetime(2026, 1, 1, 0, 0)}])
    s = ev.evaluate_response("precision_q001", "SELECT 1", [{"d": date(2026, 1, 2)}], None)
    assert s.result_match is False
