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
