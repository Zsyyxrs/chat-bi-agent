"""V2 Reference SQL: 与 agent 实际窗口对齐。"""

import sys

sys.path.insert(0, "src")
from dotenv import load_dotenv

load_dotenv("/Users/zhushangyi/CourseData/LLM_Projects/chat-bi-agent/.env")
from chat_bi_agent.data.db import DatabaseConfig, get_cursor

CHECKS = {
    # q001: agent 用 5/11-5/20 vs 5/01-5/10
    "attribution_q001": dict(
        metric="retail_deposit_balance",
        direction="down",
        sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-05-11' AND DATE '2026-05-20'
            AND c.branch_id='BR_CITY_0006' AND c.customer_tier='HIGH_NET_WORTH'
            AND a.account_type='CURRENT'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-05-01' AND DATE '2026-05-10'
            AND c.branch_id='BR_CITY_0006' AND c.customer_tier='HIGH_NET_WORTH'
            AND a.account_type='CURRENT'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """,
    ),
    # q002: 题面未点名具体产品，agent 视角看到的是 WEALTH 大类整体净效果。
    #       agent fact_anchor 走 fct_balance_daily AVG 5/11-5/20 vs 5/01-5/10。
    "attribution_q002": dict(
        metric="wealth_category_balance",
        direction="down",
        sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_product p ON b.product_id=p.product_id
          WHERE b.dt BETWEEN DATE '2026-05-11' AND DATE '2026-05-20'
            AND p.product_category='WEALTH'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_product p ON b.product_id=p.product_id
          WHERE b.dt BETWEEN DATE '2026-05-01' AND DATE '2026-05-10'
            AND p.product_category='WEALTH'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """,
    ),
    # q003: 题面只点 ATM/柜面 + 现金支取（全行），客群是 agent drill 后才知。
    "attribution_q003": dict(
        metric="cash_withdrawal_amount",
        direction="up",
        sql="""
        WITH cur AS (
          SELECT SUM(t.amount) v FROM fct_transaction t
          WHERE t.dt BETWEEN DATE '2026-02-15' AND DATE '2026-02-23'
            AND t.transaction_type='WITHDRAW' AND t.transaction_channel IN ('ATM','COUNTER')
        ), pri AS (
          SELECT SUM(t.amount) v FROM fct_transaction t
          WHERE t.dt BETWEEN DATE '2026-02-06' AND DATE '2026-02-14'
            AND t.transaction_type='WITHDRAW' AND t.transaction_channel IN ('ATM','COUNTER')
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """,
    ),
    # q004: 题面只点"日均余额波动"，客群和 account_type 都是 agent drill 后才知。
    "attribution_q004": dict(
        metric="average_daily_balance",
        direction="down",
        sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          WHERE b.dt BETWEEN DATE '2026-02-15' AND DATE '2026-02-28'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          WHERE b.dt BETWEEN DATE '2026-02-01' AND DATE '2026-02-14'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """,
    ),
    # q005: agent 用 6/26-7/20 vs 6/01-6/25, LOAN
    "attribution_q005": dict(
        metric="loan_balance",
        direction="up",
        sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-06-26' AND DATE '2026-07-20'
            AND a.account_type='LOAN'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-06-01' AND DATE '2026-06-25'
            AND a.account_type='LOAN'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """,
    ),
    # q006: P1 视角——题面给"杭州+南京"，P1 实际生成 `br.city IN ('杭州','南京')`，
    #       不限定 branch_id 编码，不限定 customer_tier（事件库 affected_* P1 看不到）。
    "attribution_q006": dict(
        metric="saving_deposit_balance",
        direction="up",
        sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          JOIN dim_branch br ON c.branch_id=br.branch_id
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-08-11' AND DATE '2026-08-20'
            AND br.city IN ('杭州','南京')
            AND a.account_type='SAVING'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          JOIN dim_branch br ON c.branch_id=br.branch_id
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-08-01' AND DATE '2026-08-10'
            AND br.city IN ('杭州','南京')
            AND a.account_type='SAVING'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """,
    ),
    # q007: P1 视角——题面"全行 5/14-5/20"，P1 不限定 branch/tier（事件库看不到）。
    #       Scope 缩窄 由 G-eval scope rubric 评，不在 quant 里测。
    "attribution_q007": dict(
        metric="AUM",
        direction="up",  # 全行口径实测为微涨
        sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          WHERE b.dt BETWEEN DATE '2026-05-14' AND DATE '2026-05-20'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          WHERE b.dt BETWEEN DATE '2026-05-07' AND DATE '2026-05-13'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """,
    ),
}


def main():
    cfg = DatabaseConfig()
    for qid, info in CHECKS.items():
        if info["sql"] is None:
            print(f"  {qid}: (design) {info['metric']} = 42.0%")
            continue
        try:
            with get_cursor(cfg) as cur:
                cur.execute(info["sql"])
                row = cur.fetchone()
                pop = float(row[0]) if row and row[0] is not None else None
                print(
                    f"  {qid}: {info['metric']} {info['direction']} = {pop:+.2f}%"
                    if pop is not None
                    else f"  {qid}: NULL"
                )
        except Exception as e:
            print(f"  {qid}: SQL error: {e}")


if __name__ == "__main__":
    main()
