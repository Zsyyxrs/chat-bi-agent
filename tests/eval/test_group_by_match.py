"""分组键比对：抓「聚合粒度错了但结果看起来对」这一类。

动机（2026-09-08，P1 题集新增 q013）：模型写 `GROUP BY b.branch_name, c.customer_name`，
按姓名而非客户号聚合。库里 5230 个客户只有 3801 个不同姓名、同分行同名 11 组，
这写法会把不同客户的交易额并成一笔。而那一跑**行数 45 对得上、result_match 判 True、
六个评分维度全部为它背书，拿了 0.837**——冲突恰好没落进任何分行的 top 3。

随后在 schema 里点明姓名不唯一，模型改对了分组键，**分数还是 0.837**。
一个千分位都没动。这说明现有维度不是打偏了，是根本不在度量聚合粒度。

判据用**子集**而不是相等：gold 的分组键必须全部出现在生成 SQL 的分组键里。
相等会误报——改对之后的 SQL 多带了 branch_name/customer_name，那是对 id 的函数依赖列，
一起 GROUP BY 无害。反方向（多分了一个键、把行拆细）由 result_count 与 result_match 承担。

与 result_match 同样**刻意不计入 combined_score**：加权重会改变所有历史分数、
废掉 baseline 可比性。
"""

from unittest.mock import MagicMock

from chat_bi_agent.eval.precision_retrieval_evaluator import (
    PrecisionRetrievalEvaluator,
    group_by_keys,
)

# ---- q013 实录：改 schema 提示前后的两版真实生成 SQL ----

Q013_GOLD = """
SELECT dbr.branch_name, dc.customer_name, r.total_amount, r.rn AS rank_in_branch
FROM (
  SELECT c.branch_id, c.customer_id, c.total_amount,
         ROW_NUMBER() OVER (
           PARTITION BY c.branch_id ORDER BY c.total_amount DESC, c.customer_id
         ) AS rn
  FROM (
    SELECT ft.branch_id, ft.customer_id, SUM(ft.amount) AS total_amount
    FROM fct_transaction ft
    WHERE ft.dt >= DATE '2026-06-01' AND ft.dt <= DATE '2026-06-30'
    GROUP BY 1, 2
  ) c
) r
JOIN dim_branch dbr ON r.branch_id = dbr.branch_id
JOIN dim_customer dc ON r.customer_id = dc.customer_id
WHERE r.rn <= 3 AND dbr.branch_level = 'CITY'
ORDER BY dbr.branch_name, r.rn
"""

Q013_GENERATED_BEFORE = """
WITH customer_txn_agg AS (
  SELECT b.branch_name, c.customer_name, SUM(t.amount) AS total_amount
  FROM fct_transaction t
  JOIN dim_customer c ON t.customer_id = c.customer_id
  JOIN dim_branch b ON t.branch_id = b.branch_id
  WHERE t.dt >= DATE '2026-06-01' AND t.dt < DATE '2026-07-01' AND b.branch_level = 'CITY'
  GROUP BY b.branch_name, c.customer_name
), ranked_customers AS (
  SELECT branch_name, customer_name, total_amount,
         DENSE_RANK() OVER(PARTITION BY branch_name ORDER BY total_amount DESC) AS rn
  FROM customer_txn_agg
)
SELECT branch_name, customer_name, total_amount, rn
FROM ranked_customers WHERE rn <= 3 ORDER BY branch_name, rn
"""

Q013_GENERATED_AFTER = """
WITH txn_agg AS (
  SELECT b.branch_id, b.branch_name, c.customer_id, c.customer_name,
         SUM(t.amount) AS total_amount
  FROM fct_transaction t
  JOIN dim_customer c ON t.customer_id = c.customer_id
  JOIN dim_branch b ON t.branch_id = b.branch_id
  WHERE b.branch_level = 'CITY'
    AND t.dt >= DATE '2026-06-01' AND t.dt < DATE '2026-07-01'
  GROUP BY b.branch_id, b.branch_name, c.customer_id, c.customer_name
), ranked AS (
  SELECT branch_name, customer_name, total_amount,
         RANK() OVER (PARTITION BY branch_id ORDER BY total_amount DESC) AS rank_in_branch
  FROM txn_agg
)
SELECT branch_name, customer_name, total_amount, rank_in_branch
FROM ranked WHERE rank_in_branch <= 3 ORDER BY branch_name, rank_in_branch
"""


# ---- group_by_keys：把分组键归一成裸列名集合 ----


def test_strips_table_qualifiers_so_alias_choice_does_not_matter():
    """gold 用 ft.，生成 SQL 用 t.——别名不同不该算作分组键不同。"""
    assert group_by_keys("SELECT a, SUM(x) FROM t1 ft GROUP BY ft.a") == {"a"}


def test_resolves_positional_group_by_against_the_select_list():
    """gold 里大量使用 GROUP BY 1, 2；解析不出序号就等于这个诊断对 gold 失效。"""
    keys = group_by_keys(
        "SELECT ft.branch_id, ft.customer_id, SUM(ft.amount) FROM fct_transaction ft GROUP BY 1, 2"
    )
    assert keys == {"branch_id", "customer_id"}


def test_collects_columns_inside_expressions():
    """按 DATE_TRUNC('month', dt) 分组，粒度取决于 dt。"""
    assert group_by_keys("SELECT DATE_TRUNC('month', dt) m, SUM(x) FROM t GROUP BY 1") == {"dt"}


def test_collects_keys_across_every_scope():
    """分组可能发生在子查询/CTE 里，只看最外层等于什么都看不到。"""
    keys = group_by_keys(
        "WITH a AS (SELECT k, SUM(v) s FROM t GROUP BY k) SELECT k FROM a"
    )
    assert keys == {"k"}


def test_returns_none_for_unparseable_sql():
    assert group_by_keys("this is not sql at all ((") is None


def test_returns_empty_set_when_there_is_no_group_by():
    assert group_by_keys("SELECT * FROM t WHERE x = 1") == set()


# ---- 接进评分器的诊断字段 ----


def _ev(gold_rows=None):
    ex = MagicMock()
    ex.execute = MagicMock(return_value=(gold_rows if gold_rows is not None else [], None))
    return PrecisionRetrievalEvaluator(gold_executor=ex)


def test_flags_the_real_q013_regression():
    """本诊断存在的理由：这条 SQL 拿了 0.837、result_match 还是 True。"""
    ev = _ev()
    s = ev.evaluate_response("precision_q013", Q013_GENERATED_BEFORE, [], None)
    assert s.group_by_match is False


def test_accepts_the_fixed_q013_sql():
    """改对之后多带了 branch_name/customer_name（对 id 的函数依赖列），不该误报。"""
    ev = _ev()
    s = ev.evaluate_response("precision_q013", Q013_GENERATED_AFTER, [], None)
    assert s.group_by_match is True


def test_none_when_neither_side_groups():
    """两边都没有 GROUP BY 时这个诊断没有意义，报 None 而不是 True。"""
    ev = _ev()
    s = ev.evaluate_response("precision_q001", "SELECT customer_id FROM dim_customer", [], None)
    assert s.group_by_match is None


def test_group_by_match_does_not_change_combined_score():
    """与 result_match 同样刻意不计入总分——计入会改掉所有历史分数。"""
    ev = _ev()
    bad = ev.evaluate_response("precision_q013", Q013_GENERATED_BEFORE, [], None)
    good = ev.evaluate_response("precision_q013", Q013_GENERATED_AFTER, [], None)
    assert bad.group_by_match is False and good.group_by_match is True
    assert bad.combined_score == good.combined_score


# ---- runner 侧：诊断字段要落进结果 JSON，否则等于没做 ----


def test_runner_summarizes_any_diagnostic_field():
    """汇总器按字段名参数化，result_match 与 group_by_match 共用一份逻辑——
    复制一份出来改，就是本项目吃过两次亏的那个「双写必然漂移」。"""
    from chat_bi_agent.runners.run_p1_eval import _summarize_diagnostic

    per_q = [
        {"question_id": "a", "group_by_match": True},
        {"question_id": "b", "group_by_match": False},
        {"question_id": "c", "group_by_match": None},  # 未评估的不进分母
    ]
    assert _summarize_diagnostic(per_q, "group_by_match") == {
        "n_evaluated": 2,
        "n_matched": 1,
        "n_mismatched": 1,
        "match_rate": 0.5,
        "mismatched_ids": ["b"],
    }


def test_runner_diagnostic_summary_handles_all_unevaluated():
    """全是 None 时 match_rate 必须是 None，不能是 0——「没测」和「全错」是两回事。"""
    from chat_bi_agent.runners.run_p1_eval import _summarize_diagnostic

    out = _summarize_diagnostic([{"question_id": "a", "group_by_match": None}], "group_by_match")
    assert out["n_evaluated"] == 0
    assert out["match_rate"] is None
