"""Three few-shot example Plans, one per major question category from
multi_step_analysis_evaluation.yaml."""

FEW_SHOTS = [
    {
        "question": (
            "对比春节前（2 月 1-14 日）和春节假期（2 月 15-23 日）的现金支取行为。"
            "包括总额、日均、涉及的客户数量和主要渠道分布。"
        ),
        "plan_json": {
            "plan_type": "temporal_comparison",
            "steps": [
                {
                    "id": "step1",
                    "question": (
                        "查询 fct_transaction 中 2026-02-01 到 2026-02-14 期间，"
                        "transaction_type = 'WITHDRAW' 的交易，按 channel 分组，"
                        "统计总额、交易笔数、去重客户数"
                    ),
                    "rationale": "建立春节前现金支取的基线数据，按渠道分段为后续对比做准备",
                    "depends_on": [],
                    "context_keys": [],
                    "expected_metrics": [
                        "withdraw_total_amount",
                        "withdraw_count",
                        "unique_customer_count",
                    ],
                },
                {
                    "id": "step2",
                    "question": (
                        "查询 fct_transaction 中 2026-02-15 到 2026-02-23 期间，"
                        "transaction_type = 'WITHDRAW' 的交易，按 channel 分组，"
                        "统计总额、交易笔数、去重客户数"
                    ),
                    "rationale": "获取春节期间的对应数据，与 step1 形成对比组",
                    "depends_on": [],
                    "context_keys": [],
                    "expected_metrics": [
                        "withdraw_total_amount",
                        "withdraw_count",
                        "unique_customer_count",
                    ],
                },
            ],
        },
    },
    {
        "question": (
            "安鑫 90 天理财产品（PROD_WEA_0000）在到期日（2026-05-14）前后的持有人行为分析。"
            "包括：到期前 7 天的持有人数、到期后赎回率、续作率和资金流向。"
        ),
        "plan_json": {
            "plan_type": "lifecycle",
            "steps": [
                {
                    "id": "step1",
                    "question": (
                        "从 fct_holding 查询 snapshot_dt = 2026-05-07 时 "
                        "product_id = 'PROD_WEA_0000' 的去重持有人数和总持有额"
                    ),
                    "rationale": "建立产品到期前一周的持有人基线",
                    "depends_on": [],
                    "context_keys": [],
                    "expected_metrics": ["holder_count", "total_holding_amount"],
                },
                {
                    "id": "step2",
                    "question": (
                        "从 fct_transaction 查询 transaction_date = 2026-05-14、"
                        "transaction_type = 'WITHDRAW'、product_id = 'PROD_WEA_0000' 的"
                        "赎回交易，统计去重客户数和赎回总额"
                    ),
                    "rationale": "测量到期日当天的赎回行为",
                    "depends_on": ["step1"],
                    "context_keys": [],
                    "expected_metrics": ["redemption_customer_count", "redemption_amount"],
                },
                {
                    "id": "step3",
                    "question": (
                        "从 fct_transaction 查询 2026-05-15 到 2026-05-21、"
                        "transaction_type = 'TRANSFER' 或 'DEPOSIT' 的转入交易，"
                        "按 target_product_id 分组，统计客户数和金额"
                    ),
                    "rationale": "追踪赎回后的续作资金流向",
                    "depends_on": ["step2"],
                    "context_keys": [],
                    "expected_metrics": ["continuation_count", "continuation_amount"],
                },
            ],
        },
    },
    {
        "question": (
            "基于已有数据（2025-01 到 2026-05），预测 2026 年 6 月的贷款申请量。"
        ),
        "plan_json": {
            "plan_type": "prediction",
            "steps": [
                {
                    "id": "step1",
                    "question": (
                        "查询 fct_transaction 中 2025-01 到 2026-05 期间、"
                        "transaction_type = 'LOAN' 的交易，按 transaction_date 聚合到月，"
                        "统计每月的笔数、总金额、平均金额"
                    ),
                    "rationale": "建立贷款申请的历史月度时间序列基线",
                    "depends_on": [],
                    "context_keys": [],
                    "expected_metrics": ["monthly_loan_count", "monthly_loan_amount"],
                },
                {
                    "id": "step2",
                    "question": (
                        "查询 fct_transaction 中 2026-05-01 到 2026-05-31 期间、"
                        "transaction_type = 'LOAN' 的交易，按 customer_tier 分组，"
                        "统计笔数和金额"
                    ),
                    "rationale": "识别 LPR 下调后的近月需求构成，为预测做客户分层基础",
                    "depends_on": ["step1"],
                    "context_keys": [],
                    "expected_metrics": ["loan_count_by_tier", "loan_amount_by_tier"],
                },
            ],
        },
    },
]
