"""Run 4 verification SQLs against seeded DB.

Returns 0 iff all events landed within ±30% tolerance.
"""

import argparse
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

from chat_bi_agent.data.db import DatabaseConfig, get_cursor  # noqa: E402

TOLERANCE_REL = 0.30


@dataclass
class EventCheck:
    event_id: str
    label: str
    sql: str
    expected_pct: float


# SQL template: each query returns (pct_change, event_window_row_count)
ANXIN_SQL = """
WITH baseline AS (
  SELECT AVG(b.balance) AS v, COUNT(*) AS n
  FROM fct_balance_daily b
  JOIN dim_customer c ON b.customer_id = c.customer_id
  JOIN dim_product  p ON b.product_id  = p.product_id
  WHERE b.branch_id = 'BR_CITY_0006'
    AND c.customer_tier IN ('HIGH_NET_WORTH','AFFLUENT')
    AND p.product_subcategory = '活期存款'
    AND b.dt BETWEEN DATE '2026-05-01' AND DATE '2026-05-13'
),
ev AS (
  SELECT AVG(b.balance) AS v, COUNT(*) AS n
  FROM fct_balance_daily b
  JOIN dim_customer c ON b.customer_id = c.customer_id
  JOIN dim_product  p ON b.product_id  = p.product_id
  WHERE b.branch_id = 'BR_CITY_0006'
    AND c.customer_tier IN ('HIGH_NET_WORTH','AFFLUENT')
    AND p.product_subcategory = '活期存款'
    AND b.dt BETWEEN DATE '2026-05-19' AND DATE '2026-05-25'
)
SELECT (ev.v - baseline.v) / NULLIF(baseline.v, 0) * 100.0, ev.n
FROM baseline, ev;
"""

QIXI_SQL = """
WITH baseline AS (
  SELECT AVG(b.balance) AS v, COUNT(*) AS n
  FROM fct_balance_daily b
  JOIN dim_customer c ON b.customer_id = c.customer_id
  JOIN dim_product  p ON b.product_id  = p.product_id
  WHERE b.branch_id IN ('BR_CITY_0000','BR_CITY_0002')
    AND c.customer_tier IN ('MASS','AFFLUENT')
    AND p.product_subcategory IN ('定期存款','大额存单')
    AND b.dt BETWEEN DATE '2026-07-27' AND DATE '2026-08-09'
),
ev AS (
  SELECT AVG(b.balance) AS v, COUNT(*) AS n
  FROM fct_balance_daily b
  JOIN dim_customer c ON b.customer_id = c.customer_id
  JOIN dim_product  p ON b.product_id  = p.product_id
  WHERE b.branch_id IN ('BR_CITY_0000','BR_CITY_0002')
    AND c.customer_tier IN ('MASS','AFFLUENT')
    AND p.product_subcategory IN ('定期存款','大额存单')
    AND b.dt BETWEEN DATE '2026-08-18' AND DATE '2026-08-24'
)
SELECT (ev.v - baseline.v) / NULLIF(baseline.v, 0) * 100.0, ev.n
FROM baseline, ev;
"""

LPR_SQL = """
WITH baseline AS (
  SELECT AVG(b.balance) AS v, COUNT(*) AS n
  FROM fct_balance_daily b
  JOIN dim_product p ON b.product_id = p.product_id
  WHERE p.product_category = 'LOAN'
    AND b.dt BETWEEN DATE '2026-06-01' AND DATE '2026-06-19'
),
ev AS (
  SELECT AVG(b.balance) AS v, COUNT(*) AS n
  FROM fct_balance_daily b
  JOIN dim_product p ON b.product_id = p.product_id
  WHERE p.product_category = 'LOAN'
    AND b.dt BETWEEN DATE '2026-07-26' AND DATE '2026-08-01'
)
SELECT (ev.v - baseline.v) / NULLIF(baseline.v, 0) * 100.0, ev.n
FROM baseline, ev;
"""

SPRING_FESTIVAL_SQL = """
WITH baseline AS (
  SELECT SUM(t.amount)::float / 14.0 AS daily_v, COUNT(*) AS n
  FROM fct_transaction t
  JOIN dim_customer c ON t.customer_id = c.customer_id
  WHERE t.transaction_type = 'WITHDRAW'
    AND t.transaction_channel IN ('ATM','COUNTER')
    AND c.customer_tier IN ('BASIC','MASS')
    AND t.dt BETWEEN DATE '2026-02-01' AND DATE '2026-02-14'
),
ev AS (
  SELECT SUM(t.amount)::float / 9.0 AS daily_v, COUNT(*) AS n
  FROM fct_transaction t
  JOIN dim_customer c ON t.customer_id = c.customer_id
  WHERE t.transaction_type = 'WITHDRAW'
    AND t.transaction_channel IN ('ATM','COUNTER')
    AND c.customer_tier IN ('BASIC','MASS')
    AND t.dt BETWEEN DATE '2026-02-15' AND DATE '2026-02-23'
)
SELECT (ev.daily_v - baseline.daily_v) / NULLIF(baseline.daily_v, 0) * 100.0, ev.n
FROM baseline, ev;
"""

CHECKS = [
    EventCheck("anxin_90_expire", "上海HNW/AFFLUENT × 活期存款 mid-May 跌", ANXIN_SQL, -8.5),
    EventCheck("qixi_deposit_campaign", "杭州/南京 MASS/AFFLUENT × 定期 八月增", QIXI_SQL, +12.0),
    EventCheck("lpr_cut_q2", "全行 LOAN 贷款余额 7月底 增", LPR_SQL, +5.5),
    EventCheck(
        "spring_festival_withdrawal",
        "BASIC/MASS × ATM/COUNTER 春节取现增",
        SPRING_FESTIVAL_SQL,
        +25.0,
    ),
]


def run_checks(event_id: str | None = None) -> int:
    config = DatabaseConfig()
    failures = []
    selected = [c for c in CHECKS if event_id is None or c.event_id == event_id]
    if not selected:
        print(f"❌ unknown event_id: {event_id}")
        return 2

    with get_cursor(config) as cur:
        for chk in selected:
            cur.execute(chk.sql)
            row = cur.fetchone()
            if row is None or row[0] is None:
                failures.append((chk, "NULL — 0 rows in event/baseline window"))
                print(f"[✗] {chk.label}: NULL")
                continue
            actual = float(row[0])
            n_event = int(row[1] or 0)
            if n_event == 0:
                failures.append((chk, "event window has 0 rows"))
                print(f"[✗] {chk.label}: event window empty")
                continue
            rel_err = abs(actual - chk.expected_pct) / abs(chk.expected_pct)
            ok = rel_err <= TOLERANCE_REL
            print(
                f"[{'✓' if ok else '✗'}] {chk.label}: "
                f"actual={actual:+.2f}%, expected={chk.expected_pct:+.2f}%, n={n_event}"
            )
            if not ok:
                failures.append((chk, f"rel_err={rel_err:.2%}"))

    if failures:
        print(f"\n❌ {len(failures)}/{len(selected)} event(s) failed verification:")
        for chk, msg in failures:
            print(f"   - {chk.event_id}: {msg}")
        return 1
    print(f"\n✅ All {len(selected)} event(s) verified.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify seeded events landed in DB")
    parser.add_argument("--event-id", help="Verify only the named event")
    args = parser.parse_args()
    return run_checks(event_id=args.event_id)


if __name__ == "__main__":
    sys.exit(main())
