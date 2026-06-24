"""V2 Reference SQL: 与 agent 实际窗口对齐。"""
import sys
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('/Users/zhushangyi/CourseData/LLM_Projects/chat-bi-agent/.env')
from chat_bi_agent.data.db import DatabaseConfig, get_cursor

CHECKS = {
    # q001: agent 用 5/11-5/20 vs 5/01-5/10
    "attribution_q001": dict(metric="retail_deposit_balance", direction="down", sql="""
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
    """),
    # q002: agent 用 snapshot_dt=5/31 vs 4/30 on PROD_WEA_0032/0033 (转入产品)
    "attribution_q002": dict(metric="wealth_product_balance", direction="up", sql="""
        WITH cur AS (
          SELECT AVG(h.market_value) v FROM fct_holding h
          WHERE h.snapshot_dt=DATE '2026-05-31'
            AND h.product_id IN ('PROD_WEA_0032','PROD_WEA_0033')
        ), pri AS (
          SELECT AVG(h.market_value) v FROM fct_holding h
          WHERE h.snapshot_dt=DATE '2026-04-30'
            AND h.product_id IN ('PROD_WEA_0032','PROD_WEA_0033')
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """),
    # q003: agent 用 2/15-2/23 vs 2/06-2/14, ATM/COUNTER × BASIC+MASS × WITHDRAW
    "attribution_q003": dict(metric="cash_withdrawal_amount", direction="up", sql="""
        WITH cur AS (
          SELECT SUM(t.amount) v FROM fct_transaction t
          JOIN dim_customer c ON t.customer_id=c.customer_id
          WHERE t.dt BETWEEN DATE '2026-02-15' AND DATE '2026-02-23'
            AND t.transaction_type='WITHDRAW' AND t.transaction_channel IN ('ATM','COUNTER')
            AND c.customer_tier IN ('BASIC','MASS')
        ), pri AS (
          SELECT SUM(t.amount) v FROM fct_transaction t
          JOIN dim_customer c ON t.customer_id=c.customer_id
          WHERE t.dt BETWEEN DATE '2026-02-06' AND DATE '2026-02-14'
            AND t.transaction_type='WITHDRAW' AND t.transaction_channel IN ('ATM','COUNTER')
            AND c.customer_tier IN ('BASIC','MASS')
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """),
    # q004: agent 用 2/15-2/28 vs 2/01-2/14, BASIC+MASS 日均余额
    "attribution_q004": dict(metric="average_daily_balance", direction="down", sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          WHERE b.dt BETWEEN DATE '2026-02-15' AND DATE '2026-02-28'
            AND c.customer_tier IN ('BASIC','MASS')
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          WHERE b.dt BETWEEN DATE '2026-02-01' AND DATE '2026-02-14'
            AND c.customer_tier IN ('BASIC','MASS')
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """),
    # q005: agent 用 6/26-7/20 vs 6/01-6/25, LOAN
    "attribution_q005": dict(metric="loan_balance", direction="up", sql="""
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
    """),
    # q006: agent 用 8/11-8/20 vs 8/01-8/10, 杭州+南京 × MASS+AFFLUENT × SAVING
    "attribution_q006": dict(metric="saving_deposit_balance", direction="up", sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-08-11' AND DATE '2026-08-20'
            AND c.branch_id IN ('BR_CITY_0000','BR_CITY_0002')
            AND c.customer_tier IN ('MASS','AFFLUENT')
            AND a.account_type='SAVING'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          JOIN dim_account a ON b.account_id=a.account_id
          WHERE b.dt BETWEEN DATE '2026-08-01' AND DATE '2026-08-10'
            AND c.branch_id IN ('BR_CITY_0000','BR_CITY_0002')
            AND c.customer_tier IN ('MASS','AFFLUENT')
            AND a.account_type='SAVING'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """),
    # q007: agent 用 5/14-5/20 vs 5/07-5/13, BR_CITY_0006 × HNW
    "attribution_q007": dict(metric="AUM", direction="down", sql="""
        WITH cur AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          WHERE b.dt BETWEEN DATE '2026-05-14' AND DATE '2026-05-20'
            AND c.branch_id='BR_CITY_0006' AND c.customer_tier='HIGH_NET_WORTH'
        ), pri AS (
          SELECT AVG(b.balance) v FROM fct_balance_daily b
          JOIN dim_customer c ON b.customer_id=c.customer_id
          WHERE b.dt BETWEEN DATE '2026-05-07' AND DATE '2026-05-13'
            AND c.branch_id='BR_CITY_0006' AND c.customer_tier='HIGH_NET_WORTH'
        )
        SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 FROM cur,pri
    """),
    # q008: 续作率 42% (设计题, 业务真值)
    "attribution_q008": dict(metric="renewal_rate", direction="absolute", sql=None),
}

def main():
    cfg = DatabaseConfig()
    for qid, info in CHECKS.items():
        if info["sql"] is None:
            print(f"  {qid}: (design) {info['metric']} = 42.0%"); continue
        try:
            with get_cursor(cfg) as cur:
                cur.execute(info["sql"])
                row = cur.fetchone()
                pop = float(row[0]) if row and row[0] is not None else None
                print(f"  {qid}: {info['metric']} {info['direction']} = {pop:+.2f}%" if pop is not None else f"  {qid}: NULL")
        except Exception as e:
            print(f"  {qid}: SQL error: {e}")

if __name__ == "__main__":
    main()
