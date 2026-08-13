"""生成 metric_routing_evaluation.yaml：34 题，逐题真打 PG 回填行数。

设计原则（防"照着 catalog 写题"的自证陷阱）：
- 题面按业务提问方式写，尽量不逐字复用 catalog alias
- 显式标注 expected_route 作为 ground truth，才能算 prefilter 的准确率/召回率
- 非指标题不是凑数：明细查询 / 目录表达不了的聚合 / 多步分析，三类都要有
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from chat_bi_agent.agents.shared.sql_executor import SQLExecutor  # noqa: E402

Q: list[dict] = []


def add(qid, question, sql, route, cols=None, filters=None, criteria=None, note=None):
    Q.append(
        {
            "id": qid,
            "question": question,
            "expected_sql": sql.strip(),
            "expected_result_columns": cols or [],
            "expected_filters": filters or [],
            "expected_route": route,
            "evaluation_criteria": criteria or [],
            **({"route_note": note} if note else {}),
        }
    )


# ============ 指标型（expected_route = metric）============

add(
    "mr_m01",
    "杭州分行（BR_CITY_0000）2026 年 5 月的存款余额是多少？",
    """SELECT AVG(balance) AS avg_deposit_balance FROM fct_balance_daily fbd
JOIN dim_account da ON fbd.account_id = da.account_id
WHERE da.account_type IN ('CURRENT','SAVING') AND fbd.branch_id = 'BR_CITY_0000'
  AND fbd.dt >= DATE '2026-05-01' AND fbd.dt <= DATE '2026-05-31'""",
    "metric",
    [],
    [{"branch_id": "BR_CITY_0000"}],
    [{"time_window": "是否正确限定 5 月"}],
)

add(
    "mr_m02",
    "2026 年 5 月高净值客户的存款余额平均是多少？",
    """SELECT AVG(fbd.balance) AS avg_deposit_balance FROM fct_balance_daily fbd
JOIN dim_account da ON fbd.account_id = da.account_id
JOIN dim_customer dc ON fbd.customer_id = dc.customer_id
WHERE da.account_type IN ('CURRENT','SAVING') AND dc.customer_tier = 'HIGH_NET_WORTH'
  AND fbd.dt >= DATE '2026-05-01' AND fbd.dt <= DATE '2026-05-31'""",
    "metric",
    [],
    [{"customer_tier": "HIGH_NET_WORTH"}],
    [{"enum": "高净值是否归一化为 HIGH_NET_WORTH"}],
)

add(
    "mr_m03",
    "2026 年 5 月各分行的贷款余额分别是多少？",
    """SELECT fbd.branch_id, AVG(fbd.balance) AS avg_loan_balance FROM fct_balance_daily fbd
JOIN dim_account da ON fbd.account_id = da.account_id
WHERE da.account_type = 'LOAN'
  AND fbd.dt >= DATE '2026-05-01' AND fbd.dt <= DATE '2026-05-31'
GROUP BY fbd.branch_id""",
    "metric",
    ["branch_id"],
    [],
    [{"grouping": "是否按分行分组"}],
)

add(
    "mr_m04",
    "全行客户的资产管理规模总额是多少？",
    "SELECT SUM(aum) AS total_aum FROM dim_customer WHERE is_active = TRUE",
    "metric",
    [],
    [],
    [{"aggregation": "是否用 SUM"}],
)

add(
    "mr_m05",
    "大众层级的客户一共有多少人？",
    "SELECT COUNT(DISTINCT customer_id) AS customer_count FROM dim_customer WHERE customer_tier = 'MASS'",
    "metric",
    [],
    [{"customer_tier": "MASS"}],
    [{"enum": "大众→MASS"}],
)

add(
    "mr_m06",
    "杭州（BR_CITY_0000）和南京（BR_CITY_0002）两个分行各有多少大众客户？",
    """SELECT branch_id, COUNT(DISTINCT customer_id) AS customer_count FROM dim_customer
WHERE branch_id IN ('BR_CITY_0000','BR_CITY_0002') AND customer_tier = 'MASS'
GROUP BY branch_id""",
    "metric",
    ["branch_id"],
    [{"branch_id": ["BR_CITY_0000", "BR_CITY_0002"]}, {"customer_tier": "MASS"}],
    [{"multi_value": "是否用 IN 而非丢掉约束"}],
)

add(
    "mr_m07",
    "风险等级 R5 的理财类产品有几款？",
    "SELECT COUNT(*) AS product_count FROM dim_product WHERE product_category = 'WEALTH' AND risk_level = 'R5'",
    "metric",
    [],
    [{"product_category": "WEALTH"}, {"risk_level": "R5"}],
    [{"enum": "理财→WEALTH"}],
)

add(
    "mr_m08",
    "2026 年 5 月通过 ATM 渠道的交易总金额是多少？",
    """SELECT SUM(amount) AS total_amount FROM fct_transaction
WHERE transaction_channel = 'ATM' AND dt >= DATE '2026-05-01' AND dt <= DATE '2026-05-31'""",
    "metric",
    [],
    [{"channel": "ATM"}],
    [{"column_name": "渠道列是 transaction_channel"}],
)

add(
    "mr_m09",
    "2026 年 5 月一共发生了多少笔交易？",
    "SELECT COUNT(*) AS transaction_count FROM fct_transaction WHERE dt >= DATE '2026-05-01' AND dt <= DATE '2026-05-31'",
    "metric",
    [],
    [],
    [{"aggregation": "COUNT 而非 SUM"}],
)

add(
    "mr_m10",
    "2026 年 5 月平均每笔交易的金额是多少？",
    "SELECT AVG(amount) AS avg_transaction_amount FROM fct_transaction WHERE dt >= DATE '2026-05-01' AND dt <= DATE '2026-05-31'",
    "metric",
    [],
    [],
    [{"aggregation": "AVG 而非 SUM/COUNT"}],
)

add(
    "mr_m11",
    "2026 年 5 月客户持仓市值合计是多少？",
    "SELECT SUM(market_value) AS total_market_value FROM fct_holding WHERE snapshot_dt >= DATE '2026-05-01' AND snapshot_dt <= DATE '2026-05-31'",
    "metric",
    [],
    [],
    [{"table": "是否用 fct_holding"}],
)

add(
    "mr_m12",
    "2026 年 5 月客户投资的盈亏合计是多少？",
    "SELECT SUM(pnl) AS total_pnl FROM fct_holding WHERE snapshot_dt >= DATE '2026-05-01' AND snapshot_dt <= DATE '2026-05-31'",
    "metric",
    [],
    [],
    [{"column": "是否用 pnl 列"}],
)

add(
    "mr_m13",
    "2026 年 9 月发生了多少起风险事件？",
    "SELECT COUNT(*) AS risk_event_count FROM fct_risk_event WHERE dt >= DATE '2026-09-01' AND dt <= DATE '2026-09-30'",
    "metric",
    [],
    [],
    [{"table": "是否用 fct_risk_event"}],
)

add(
    "mr_m14",
    "严重程度为 CRITICAL 的风险事件一共有多少起？",
    "SELECT COUNT(*) AS risk_event_count FROM fct_risk_event WHERE severity = 'CRITICAL'",
    "metric",
    [],
    [{"severity": "CRITICAL"}],
    [{"enum": "severity 枚举"}],
)

add(
    "mr_m15",
    "春节储蓄活动一共触达了多少人次？",
    "SELECT COUNT(*) AS response_count FROM fct_campaign_response WHERE campaign_name = '春节储蓄活动'",
    "metric",
    [],
    [{"campaign_name": "春节储蓄活动"}],
    [{"string_filter": "活动名是否原样匹配"}],
)

add(
    "mr_m16",
    "理财产品推荐活动带来的转化金额是多少？",
    "SELECT SUM(conversion_amount) AS total_conversion_amount FROM fct_campaign_response WHERE campaign_name = '理财产品推荐'",
    "metric",
    [],
    [{"campaign_name": "理财产品推荐"}],
    [{"string_filter": "活动名匹配"}],
)

add(
    "mr_m17",
    "状态为 ACTIVE 的账户一共有多少个？",
    "SELECT COUNT(DISTINCT account_id) AS account_count FROM dim_account WHERE status = 'ACTIVE'",
    "metric",
    [],
    [{"status": "ACTIVE"}],
    [{"enum": "status 枚举"}],
)

add(
    "mr_m18",
    "高净值客户的平均年龄是多少？",
    "SELECT AVG(age) AS avg_age FROM dim_customer WHERE is_active = TRUE AND customer_tier = 'HIGH_NET_WORTH'",
    "metric",
    [],
    [{"customer_tier": "HIGH_NET_WORTH"}],
    [{"aggregation": "AVG(age)"}],
)

add(
    "mr_m19",
    "华东地区有多少家网点？",
    "SELECT COUNT(*) AS branch_count FROM dim_branch WHERE region = '华东'",
    "metric",
    [],
    [{"region": "华东"}],
    [{"string_filter": "区域名匹配"}],
)

add(
    "mr_m20",
    "2026 年 5 月各账户类型的日均余额分别是多少？",
    """SELECT da.account_type, AVG(fbd.balance) AS avg_total_balance FROM fct_balance_daily fbd
JOIN dim_account da ON fbd.account_id = da.account_id
WHERE fbd.dt >= DATE '2026-05-01' AND fbd.dt <= DATE '2026-05-31'
GROUP BY da.account_type""",
    "metric",
    ["account_type"],
    [],
    [{"grouping": "按账户类型分组"}],
)

# ============ 非指标型（expected_route = nl2sql）============

# --- 明细/列表查询：语义层只出聚合，天然不该命中 ---
add(
    "mr_n01",
    "查询浦东分行（BR_CITY_0006）所有高净值客户的客户 ID、姓名和客户等级。",
    """SELECT customer_id, customer_name, customer_tier FROM dim_customer
WHERE branch_id = 'BR_CITY_0006' AND customer_tier = 'HIGH_NET_WORTH'""",
    "nl2sql",
    ["customer_id", "customer_name", "customer_tier"],
    [{"branch_id": "BR_CITY_0006"}, {"customer_tier": "HIGH_NET_WORTH"}],
    [{"detail_query": "返回明细行而非聚合"}],
    note="明细查询——语义层只出聚合值",
)

add(
    "mr_n02",
    "列出 2026 年 4 月 28 日当天所有交易的交易 ID、账户 ID、金额和交易类型。",
    """SELECT transaction_id, account_id, amount, transaction_type FROM fct_transaction
WHERE dt = DATE '2026-04-28'""",
    "nl2sql",
    ["transaction_id", "account_id", "amount", "transaction_type"],
    [],
    [{"detail_query": "明细行"}],
    note="明细查询",
)

add(
    "mr_n03",
    "找出 2026 年 2 月 15 日至 23 日，通过 ATM 或柜台完成的现金支取交易，返回日期、账户 ID、金额和渠道。",
    """SELECT dt, account_id, amount, transaction_channel FROM fct_transaction
WHERE dt >= DATE '2026-02-15' AND dt <= DATE '2026-02-23'
  AND transaction_channel IN ('ATM','COUNTER') AND transaction_type = 'WITHDRAW'""",
    "nl2sql",
    ["dt", "account_id", "amount", "transaction_channel"],
    [{"transaction_channel": ["ATM", "COUNTER"]}],
    [{"detail_query": "明细行"}],
    note="明细查询，虽含'现金支取'这类指标同义词",
)

add(
    "mr_n04",
    "列出所有状态为冻结的账户及其客户 ID。",
    "SELECT account_id, customer_id FROM dim_account WHERE status = 'FROZEN'",
    "nl2sql",
    ["account_id", "customer_id"],
    [{"status": "FROZEN"}],
    [{"detail_query": "明细行"}],
    note="明细查询",
)

add(
    "mr_n05",
    "查询 2026 年 9 月所有风险事件的事件 ID、类型、严重程度和描述。",
    """SELECT event_id, event_type, severity, description FROM fct_risk_event
WHERE dt >= DATE '2026-09-01' AND dt <= DATE '2026-09-30'""",
    "nl2sql",
    ["event_id", "event_type", "severity", "description"],
    [],
    [{"detail_query": "明细行"}],
    note="明细查询",
)

# --- 目录表达不了的聚合：应正确返回 metric_id=null ---
add(
    "mr_n06",
    "按产品分类统计各类产品的平均风险等级评分，并按评分从高到低排序。",
    """SELECT product_category, AVG(CAST(SUBSTRING(risk_level FROM 2) AS INT)) AS avg_risk_score
FROM dim_product GROUP BY product_category ORDER BY avg_risk_score DESC""",
    "nl2sql",
    ["product_category"],
    [],
    [{"out_of_catalog": "AVG(risk_level) 不在目录里"}],
    note="目录无此指标——risk_level 只是维度不是度量",
)

add(
    "mr_n07",
    "各分行客户的平均持仓成本是多少？",
    "SELECT branch_id, AVG(cost_basis) AS avg_cost FROM fct_holding GROUP BY branch_id",
    "nl2sql",
    ["branch_id"],
    [],
    [{"out_of_catalog": "cost_basis 未建成指标"}],
    note="目录无此指标",
)

add(
    "mr_n08",
    "各风险偏好等级的客户数占全部客户的比例是多少？",
    """SELECT risk_appetite, COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS ratio
FROM dim_customer GROUP BY risk_appetite""",
    "nl2sql",
    ["risk_appetite"],
    [],
    [{"out_of_catalog": "占比需窗口函数，模板表达不了"}],
    note="占比类——模板只出单指标聚合",
)

add(
    "mr_n09",
    "2026 年 5 月哪一天的交易金额最高？",
    """SELECT dt, SUM(amount) AS total FROM fct_transaction
WHERE dt >= DATE '2026-05-01' AND dt <= DATE '2026-05-31'
GROUP BY dt ORDER BY total DESC LIMIT 1""",
    "nl2sql",
    ["dt"],
    [],
    [{"out_of_catalog": "argmax 需 ORDER BY + LIMIT，spec 无此表达"}],
    note="argmax——spec 无排序/取顶",
)

add(
    "mr_n10",
    "开户时间超过 3 年的客户有多少人？",
    "SELECT COUNT(*) AS n FROM dim_customer WHERE open_date <= CURRENT_DATE - INTERVAL '3 years'",
    "nl2sql",
    [],
    [],
    [{"out_of_catalog": "open_date 未建成 filter"}],
    note="目录里 customer_count 无 open_date 过滤",
)

# --- 多步/分析型 ---
add(
    "mr_n11",
    "对比 2026 年 4 月和 5 月的存款余额变化。",
    """SELECT to_char(fbd.dt,'YYYY-MM') AS m, AVG(fbd.balance) AS avg_balance
FROM fct_balance_daily fbd JOIN dim_account da ON fbd.account_id = da.account_id
WHERE da.account_type IN ('CURRENT','SAVING')
  AND fbd.dt >= DATE '2026-04-01' AND fbd.dt <= DATE '2026-05-31'
GROUP BY 1 ORDER BY 1""",
    "nl2sql",
    [],
    [],
    [{"multi_window": "跨两个时间窗对比，单 spec 表达不了"}],
    note="双时间窗对比——spec 只有一个 time_window",
)

add(
    "mr_n12",
    "存款余额最高的前 5 个分行是哪些？",
    """SELECT fbd.branch_id, AVG(fbd.balance) AS avg_balance
FROM fct_balance_daily fbd JOIN dim_account da ON fbd.account_id = da.account_id
WHERE da.account_type IN ('CURRENT','SAVING')
GROUP BY fbd.branch_id ORDER BY avg_balance DESC LIMIT 5""",
    "nl2sql",
    ["branch_id"],
    [],
    [{"top_n": "Top-N 需 ORDER BY + LIMIT"}],
    note="Top-N——spec 无排序/取顶",
)

add(
    "mr_n13",
    "2026 年 5 月哪些客户既发生过交易又触发过风险事件？",
    """SELECT DISTINCT ft.customer_id FROM fct_transaction ft
JOIN fct_risk_event fre ON ft.customer_id = fre.customer_id
WHERE ft.dt >= DATE '2026-05-01' AND ft.dt <= DATE '2026-05-31'""",
    "nl2sql",
    ["customer_id"],
    [],
    [{"set_op": "跨两个 fact 表求交集"}],
    note="跨事实表交集——单 metric 模板表达不了",
)

add(
    "mr_n14",
    "2026 年 2 月春节前后的现金支取行为有什么变化？",
    """SELECT dt, COUNT(*) AS n, SUM(amount) AS total FROM fct_transaction
WHERE transaction_type = 'WITHDRAW' AND dt >= DATE '2026-02-01' AND dt <= DATE '2026-02-28'
GROUP BY dt ORDER BY dt""",
    "nl2sql",
    ["dt"],
    [{"transaction_type": "WITHDRAW"}],
    [{"open_ended": "开放式分析题，非单一指标取数"}],
    note="开放式分析——P2 场景，不该走语义层",
)


def main() -> int:
    e = SQLExecutor()
    bad = []
    for q in Q:
        rows, err = e.execute(q["expected_sql"])
        if err:
            bad.append(f"{q['id']}: {err.splitlines()[0]}")
            continue
        n = len(rows)
        q["expected_result_count"] = {"min": n, "max": n}
        q["_actual_rows"] = n
    if bad:
        print("gold SQL 跑不通：")
        for b in bad:
            print("  ", b)
        return 1

    n_metric = sum(1 for q in Q if q["expected_route"] == "metric")
    print(
        f"总题量 {len(Q)}，指标型 {n_metric}（{n_metric / len(Q):.1%}），非指标 {len(Q) - n_metric}"
    )
    zero = [q["id"] for q in Q if q["_actual_rows"] == 0]
    if zero:
        print("返 0 行的题（需确认是否合理）:", zero)

    # 输出 YAML（手写以保持中文可读、不转义）
    out = [
        "# Metric Routing 评测集（2026-08-13）",
        "#",
        "# 为什么另起一套：原 precision_retrieval_evaluation.yaml 的 6 题里只有 2 题是",
        "# 指标型问题，即使 prefilter 完美命中率上限也只有 0.333，无法衡量语义层。",
        "#",
        "# 设计原则：",
        "# - 题面按业务提问方式写，尽量不逐字复用 catalog alias（防「照着目录写题」的自证）",
        "# - expected_route 是 ground truth：metric = 该走语义层，nl2sql = 不该走。",
        "#   有了它才能算 prefilter 的准确率/召回率，而不只是「触发了多少次」",
        "# - 非指标题分三类，都不是凑数：",
        "#     明细查询（语义层只出聚合）",
        "#     目录表达不了的聚合（占比/argmax/Top-N/未建模的列）",
        "#     多步分析（跨时间窗对比、跨事实表交集、开放式）",
        "#",
        "# expected_result_count 由 gold SQL 真打 PG 回填，不是手写估的。",
        "",
        "evaluation_questions:",
    ]

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    for q in Q:
        out.append(f"  - id: {q['id']}")
        out.append(f'    question: "{esc(q["question"])}"')
        out.append("    expected_sql: |")
        for line in q["expected_sql"].split("\n"):
            out.append(f"      {line}")
        out.append(f"    expected_route: {q['expected_route']}")
        if q.get("route_note"):
            out.append(f'    route_note: "{esc(q["route_note"])}"')
        cols = q["expected_result_columns"]
        out.append(f"    expected_result_columns: {cols if cols else '[]'}")
        if q["expected_filters"]:
            out.append("    expected_filters:")
            for f in q["expected_filters"]:
                for k, v in f.items():
                    if isinstance(v, list):
                        out.append(f"      - {k}: {v}")
                    else:
                        out.append(f'      - {k}: "{esc(str(v))}"')
        else:
            out.append("    expected_filters: []")
        rc = q["expected_result_count"]
        out.append(f"    expected_result_count: {{min: {rc['min']}, max: {rc['max']}}}")
        if q["evaluation_criteria"]:
            out.append("    evaluation_criteria:")
            for c in q["evaluation_criteria"]:
                for k, v in c.items():
                    out.append(f'      - {k}: "{esc(str(v))}"')
        out.append("")

    dest = Path("src/chat_bi_agent/data/metric_routing_evaluation.yaml")
    dest.write_text("\n".join(out), encoding="utf-8")
    print("wrote", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
