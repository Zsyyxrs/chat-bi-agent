"""Phase 2 验证：对 6 题 happy path 测召回，Top-4 必须覆盖 expected 表。

运行：
    python scripts/verify_schema_recall.py

期望：
    6/6 通过
"""

import sys

from dotenv import load_dotenv

load_dotenv()

from chat_bi_agent.agents.shared.schema_linker import SchemaLinker
from chat_bi_agent.llm.langfuse_setup import flush
from chat_bi_agent.schema.loader import SchemaLoader

HAPPY_PATH = [
    (
        "q001",
        "查询上海分行（BR_CITY_0006）所有高净值客户的客户 ID、姓名和客户等级。",
        {"dim_customer"},
    ),
    ("q002", "统计有多少个产品分类为 '理财' 且风险等级为 'MEDIUM'？", {"dim_product"}),
    (
        "q003",
        "查询 2026 年 5 月 14 日当天发生的所有交易，返回交易 ID、账户 ID、交易金额和交易类型。",
        {"fct_transaction"},
    ),
    (
        "q004",
        "找出 2026 年 2 月 15-23 日期间，交易渠道为 ATM 或 COUNTER 的现金支取交易。返回交易日期、账户 ID、金额和交易渠道。",
        {"fct_transaction"},
    ),
    (
        "q006",
        "统计杭州（BR_CITY_0000）和南京（BR_CITY_0002）分行的 MASS 层客户数量。",
        {"dim_customer"},
    ),
    ("q007", "按产品分类统计各类产品的平均风险等级评分，并按评分从高到低排序。", {"dim_product"}),
]


def main() -> int:
    loader = SchemaLoader()
    loader.load()
    print(f"加载 {len(loader.docs)} 张表，构建 embedding 索引中...")
    loader.build_index()

    linker = SchemaLinker(loader=loader, top_k=4)

    passes = 0
    for qid, question, expected_tables in HAPPY_PATH:
        matches = linker.link(question)
        top_names = {m.name for m in matches}
        covered = expected_tables.issubset(top_names)
        status = "✅" if covered else "❌"
        rank_info = ", ".join(f"{m.name}={m.score:.3f}" for m in matches)
        print(f"{status} {qid}: expected={expected_tables}, top4=[{rank_info}]")
        if covered:
            passes += 1

    print(f"\n=== Recall: {passes}/{len(HAPPY_PATH)} ===")
    return 0 if passes == len(HAPPY_PATH) else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        flush()
    sys.exit(exit_code)
