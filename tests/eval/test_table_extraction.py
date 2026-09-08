"""表选择维度的表名抽取：CTE 名不是表。

2026-09-08 查出的系统性偏置。原实现是正则 `(?:FROM|JOIN)\\s+(\\w+)`，它把
`FROM txn_agg`（引用一个 CTE）也算成表名。后果不是随机噪声，而是**惩罚「用 CTE 写」
这个风格选择**，且不对称：gold 惯用派生子查询（`FROM (` 不匹配 `\\w+`，逃过），
生成 SQL 用 `WITH` 就中招。

table_score 权重 0.2 且**计入 combined_score**，所以这是实打实的压分：
四道用 CTE 的题（q008/q012/q013/q014）表选择本应全是 1.000，被误算成 0.5~0.6，
各扣掉 0.08~0.10。已发布的 P1 baseline 因此被低估：0.9646 实为 0.9771。

偏置的严重程度还随题目难度上升——分析型 SQL（窗口、多步、嵌套）天然用 CTE，
咬得最狠的恰恰是最该测准的那批题。
"""

import pytest

from chat_bi_agent.eval.precision_retrieval_evaluator import (
    PrecisionRetrievalEvaluator,
    tables_in_sql,
)


def test_cte_name_is_not_a_table():
    """本次修复的核心：引用 CTE 不等于读了一张表。"""
    sql = "WITH agg AS (SELECT a, SUM(b) s FROM fct_transaction GROUP BY a) SELECT * FROM agg"
    assert tables_in_sql(sql) == {"fct_transaction"}


def test_multiple_ctes_are_all_excluded():
    sql = """
    WITH a AS (SELECT x FROM t1), b AS (SELECT y FROM a JOIN t2 ON TRUE)
    SELECT * FROM b
    """
    assert tables_in_sql(sql) == {"t1", "t2"}


def test_derived_table_alias_is_not_a_table():
    """派生子查询的别名同样不是表（老正则碰巧漏掉了它，这里把行为钉死）。"""
    assert tables_in_sql("SELECT * FROM (SELECT x FROM t1) d") == {"t1"}


def test_real_tables_and_joins_are_all_collected():
    sql = """
    SELECT * FROM fct_transaction ft
    JOIN dim_customer c ON ft.customer_id = c.customer_id
    JOIN dim_branch b ON ft.branch_id = b.branch_id
    """
    assert tables_in_sql(sql) == {"fct_transaction", "dim_customer", "dim_branch"}


def test_unparseable_sql_falls_back_to_regex_rather_than_returning_empty():
    """返回空集合会让 Jaccard 变 0、把 table_score 打到底——
    解析不了时宁可退回老正则的粗略结果，也不能凭空判它一个 0 分。"""
    assert tables_in_sql("SELECT FROM dim_customer WHERE ((") == {"dim_customer"}


def test_expected_and_generated_use_the_same_extractor():
    """两侧必须同源。原实现是两个函数体逐字相同的方法——
    改一个漏一个就会让 gold 与生成 SQL 按不同规则抽表，Jaccard 直接失去意义。"""
    ev = PrecisionRetrievalEvaluator()
    assert ev._extract_tables_from_expected_sql.__func__ is ev._extract_tables_from_sql.__func__


@pytest.mark.parametrize("qid", ["precision_q008", "precision_q012", "precision_q013"])
def test_cte_using_answers_now_score_full_marks_on_table_selection(qid):
    """回归锚：这三题的生成 SQL 都用了 CTE，而它们选的表与 gold 完全一致。

    修复前它们的 table_score 分别是 0.5 / 0.5 / 0.6——扣分理由是「给自己的 CTE
    起了名字」。
    """
    generated = {
        # q008：2026-08-15 已发布 baseline 里记录的生成 SQL（结构简化，保留 CTE 与表）
        "precision_q008": """
            WITH balance_comparison AS (
              SELECT fbd.account_id FROM fct_balance_daily fbd
              JOIN dim_account da ON fbd.account_id = da.account_id
              GROUP BY fbd.account_id
            ) SELECT * FROM balance_comparison
        """,
        "precision_q012": """
            WITH monthly_data AS (
              SELECT DATE_TRUNC('month', dt) m, SUM(amount) a
              FROM fct_transaction GROUP BY 1
            ) SELECT * FROM monthly_data
        """,
        "precision_q013": """
            WITH customer_txn_agg AS (
              SELECT b.branch_name, c.customer_name, SUM(t.amount) total
              FROM fct_transaction t
              JOIN dim_customer c ON t.customer_id = c.customer_id
              JOIN dim_branch b ON t.branch_id = b.branch_id
              GROUP BY 1, 2
            ), ranked_customers AS (SELECT * FROM customer_txn_agg)
            SELECT * FROM ranked_customers
        """,
    }[qid]
    ev = PrecisionRetrievalEvaluator()
    gold = ev.get_question(qid)["expected_sql"]
    assert tables_in_sql(gold) == tables_in_sql(generated)
