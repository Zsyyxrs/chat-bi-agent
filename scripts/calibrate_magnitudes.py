"""Per-question reference SQL → actual observable PoP. Backfill into eval YAML."""
import os, sys
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('/Users/zhushangyi/CourseData/LLM_Projects/chat-bi-agent/.env')
from chat_bi_agent.data.db import DatabaseConfig, get_cursor

CHECKS = {
    # q001: 上海浦东分行 × HNW × 活期 × 5月中旬
    "attribution_q001": dict(
        metric="retail_deposit_balance",
        direction="down",
        sql="""
          WITH cur AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            JOIN dim_account a ON b.account_id=a.account_id
            WHERE b.dt BETWEEN DATE '2026-05-14' AND DATE '2026-05-23'
              AND c.branch_id='BR_CITY_0006'
              AND c.customer_tier='HIGH_NET_WORTH'
              AND a.account_type='CURRENT'
          ), pri AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            JOIN dim_account a ON b.account_id=a.account_id
            WHERE b.dt BETWEEN DATE '2026-05-04' AND DATE '2026-05-13'
              AND c.branch_id='BR_CITY_0006'
              AND c.customer_tier='HIGH_NET_WORTH'
              AND a.account_type='CURRENT'
          )
          SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 AS pop FROM cur,pri
        """,
    ),
    # q002: 安鑫 × WEALTH × 转入产品(_0032/_0033) 市值增
    "attribution_q002": dict(
        metric="wealth_product_balance",
        direction="up",
        sql="""
          WITH cur AS (
            SELECT AVG(h.market_value) v FROM fct_holding h
            WHERE h.snapshot_dt BETWEEN DATE '2026-05-14' AND DATE '2026-05-23'
              AND h.product_id IN ('PROD_WEA_0032','PROD_WEA_0033')
          ), pri AS (
            SELECT AVG(h.market_value) v FROM fct_holding h
            WHERE h.snapshot_dt BETWEEN DATE '2026-05-04' AND DATE '2026-05-13'
              AND h.product_id IN ('PROD_WEA_0032','PROD_WEA_0033')
          )
          SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 AS pop FROM cur,pri
        """,
    ),
    # q003: 春节 × ATM+COUNTER × BASIC+MASS 取现增
    "attribution_q003": dict(
        metric="cash_withdrawal_amount",
        direction="up",
        sql="""
          WITH cur AS (
            SELECT SUM(t.amount) v FROM fct_transaction t
            JOIN dim_customer c ON t.customer_id=c.customer_id
            WHERE t.dt BETWEEN DATE '2026-02-15' AND DATE '2026-02-23'
              AND t.transaction_type='WITHDRAW'
              AND t.transaction_channel IN ('ATM','COUNTER')
              AND c.customer_tier IN ('BASIC','MASS')
          ), pri AS (
            SELECT SUM(t.amount) v FROM fct_transaction t
            JOIN dim_customer c ON t.customer_id=c.customer_id
            WHERE t.dt BETWEEN DATE '2026-02-06' AND DATE '2026-02-14'
              AND t.transaction_type='WITHDRAW'
              AND t.transaction_channel IN ('ATM','COUNTER')
              AND c.customer_tier IN ('BASIC','MASS')
          )
          SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 AS pop FROM cur,pri
        """,
    ),
    # q004: 春节 × BASIC+MASS × 日均余额降
    "attribution_q004": dict(
        metric="average_daily_balance",
        direction="down",
        sql="""
          WITH cur AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            WHERE b.dt BETWEEN DATE '2026-02-15' AND DATE '2026-02-23'
              AND c.customer_tier IN ('BASIC','MASS')
          ), pri AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            WHERE b.dt BETWEEN DATE '2026-02-06' AND DATE '2026-02-14'
              AND c.customer_tier IN ('BASIC','MASS')
          )
          SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 AS pop FROM cur,pri
        """,
    ),
    # q005: LPR × 7月中后 × 贷款余额增 (7天延迟)
    "attribution_q005": dict(
        metric="loan_balance",
        direction="up",
        sql="""
          WITH cur AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_account a ON b.account_id=a.account_id
            WHERE b.dt BETWEEN DATE '2026-07-04' AND DATE '2026-07-20'
              AND a.account_type='LOAN'
          ), pri AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_account a ON b.account_id=a.account_id
            WHERE b.dt BETWEEN DATE '2026-06-03' AND DATE '2026-06-19'
              AND a.account_type='LOAN'
          )
          SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 AS pop FROM cur,pri
        """,
    ),
    # q006: 七夕 × 杭州+南京 × MASS+AFFLUENT × 定期增
    "attribution_q006": dict(
        metric="saving_deposit_balance",
        direction="up",
        sql="""
          WITH cur AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            JOIN dim_account a ON b.account_id=a.account_id
            WHERE b.dt BETWEEN DATE '2026-08-12' AND DATE '2026-08-19'
              AND c.branch_id IN ('BR_CITY_0000','BR_CITY_0002')
              AND c.customer_tier IN ('MASS','AFFLUENT')
              AND a.account_type='SAVING'
          ), pri AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            JOIN dim_account a ON b.account_id=a.account_id
            WHERE b.dt BETWEEN DATE '2026-08-01' AND DATE '2026-08-09'
              AND c.branch_id IN ('BR_CITY_0000','BR_CITY_0002')
              AND c.customer_tier IN ('MASS','AFFLUENT')
              AND a.account_type='SAVING'
          )
          SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 AS pop FROM cur,pri
        """,
    ),
    # q007: 安鑫 × BR_CITY_0006 × HNW × AUM 降
    "attribution_q007": dict(
        metric="AUM",
        direction="down",
        sql="""
          WITH cur AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            WHERE b.dt BETWEEN DATE '2026-05-14' AND DATE '2026-05-20'
              AND c.branch_id='BR_CITY_0006'
              AND c.customer_tier='HIGH_NET_WORTH'
          ), pri AS (
            SELECT AVG(b.balance) v FROM fct_balance_daily b
            JOIN dim_customer c ON b.customer_id=c.customer_id
            WHERE b.dt BETWEEN DATE '2026-05-07' AND DATE '2026-05-13'
              AND c.branch_id='BR_CITY_0006'
              AND c.customer_tier='HIGH_NET_WORTH'
          )
          SELECT (cur.v-pri.v)/NULLIF(pri.v,0)*100 AS pop FROM cur,pri
        """,
    ),
    # q008: 续作率 — 不算 PoP, 算固定率 (安鑫产品续作客户 / 到期客户)
    "attribution_q008": dict(
        metric="renewal_rate",
        direction="absolute",
        sql=None,  # design question, value is 42 directly from event YAML
    ),
}

def main():
    cfg = DatabaseConfig()
    results = {}
    for qid, info in CHECKS.items():
        if info["sql"] is None:
            results[qid] = (info["metric"], info["direction"], 42.0)
            print(f"  {qid}: (design) {info['metric']} = 42.0%")
            continue
        try:
            with get_cursor(cfg) as cur:
                cur.execute(info["sql"])
                row = cur.fetchone()
                pop = float(row[0]) if row and row[0] is not None else None
                results[qid] = (info["metric"], info["direction"], pop)
                print(f"  {qid}: {info['metric']} {info['direction']} = {pop:+.2f}%" if pop is not None else f"  {qid}: NULL")
        except Exception as e:
            print(f"  {qid}: SQL error: {e}")
            results[qid] = (info["metric"], info["direction"], None)
    return results

if __name__ == "__main__":
    main()
