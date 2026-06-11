"""Transaction and fact table generators with business rules (workday/month-end effects)."""

import random
from datetime import date, datetime, timedelta
from typing import Generator

from faker import Faker

fake = Faker("zh_CN")


class TransactionGenerator:
    """Generate transaction and fact table rows with realistic banking patterns."""

    def __init__(self, seed: int = 42, events: list = None):
        self.seed = seed
        self.events = events or []
        random.seed(seed)
        Faker.seed(seed)

    def generate_transactions(
        self,
        account_ids: list[str],
        customer_ids: list[str],
        product_ids: list[str],
        branch_ids: list[str],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        transactions_per_account_per_month: float = 2.5,
        force_specs: list | None = None,
    ) -> Generator[dict, None, None]:
        """
        Generate transaction data with business rules:
        - Month-end effect: higher transaction volume on month-end days
        - Workday effect: weekends have fewer transactions
        - Amount distribution: skewed towards smaller amounts
        - force_specs: optional list of ForcedTxnSpec for anchor accounts
        """

        transaction_id = 1
        current = start_date

        # Pre-compute forced injections per date.
        forced_inserts: dict[date, list[dict]] = {}
        if force_specs:
            for spec in force_specs:
                window_start = max(start_date, spec.event_date - timedelta(days=5))
                window_end = min(end_date, spec.event_date + timedelta(days=10))
                window_days = max(1, (window_end - window_start).days + 1)
                for acct_id in spec.account_ids:
                    for k in range(spec.min_txn_per_customer):
                        offset = (k * 7) % window_days
                        d = window_start + timedelta(days=offset)
                        forced_inserts.setdefault(d, []).append({
                            "_event_id": spec.event_id,
                            "account_id": acct_id,
                            "transaction_type": spec.txn_type,
                            "transaction_channel": (spec.channels or ["MOBILE"])[0],
                        })

        while current <= end_date:
            # Month-end multiplier (5x volume on last 3 days of month)
            is_month_end_window = (current + timedelta(days=3)).month != current.month
            month_end_multiplier = 5.0 if is_month_end_window else 1.0

            # Workday effect
            is_weekend = current.weekday() >= 5
            workday_multiplier = 0.3 if is_weekend else 1.0

            # Compute transactions for this day
            daily_transaction_count = int(
                len(account_ids)
                * transactions_per_account_per_month
                * (1 / 30.0)
                * month_end_multiplier
                * workday_multiplier
            )

            for _ in range(max(1, daily_transaction_count)):
                account_id = random.choice(account_ids)
                customer_id = random.choice(customer_ids)
                counter_account_id = (
                    random.choice(account_ids) if random.random() > 0.2 else None
                )

                txn_types = ["DEPOSIT", "WITHDRAW", "TRANSFER", "PAYMENT", "INTEREST", "FEE"]
                txn_type_dist = random.choices(
                    txn_types, weights=[15, 20, 30, 25, 5, 5]
                )[0]

                channels = ["MOBILE", "INTERNET", "COUNTER", "ATM", "AGENT", "API"]
                channel_dist = random.choices(channels, weights=[40, 30, 10, 10, 5, 5])[0]

                # Amount distribution: skewed (most transactions are small)
                if txn_type_dist == "INTEREST":
                    amount = round(random.uniform(0.1, 500), 2)
                elif txn_type_dist == "FEE":
                    amount = round(random.uniform(1, 100), 2)
                else:
                    # 80% < 10k, 15% 10k-100k, 5% > 100k
                    r = random.random()
                    if r < 0.80:
                        amount = round(random.uniform(10, 10000), 2)
                    elif r < 0.95:
                        amount = round(random.uniform(10000, 100000), 2)
                    else:
                        amount = round(random.uniform(100000, 1000000), 2)

                balance_after = round(random.uniform(0, 10000000), 2)

                transaction_time = datetime.combine(
                    current, fake.time_object()
                )

                yield {
                    "transaction_id": transaction_id,
                    "dt": current,
                    "transaction_time": transaction_time,
                    "account_id": account_id,
                    "customer_id": customer_id,
                    "counter_account_id": counter_account_id,
                    "transaction_type": txn_type_dist,
                    "transaction_channel": channel_dist,
                    "amount": amount,
                    "currency": "CNY",
                    "balance_after": balance_after,
                    "branch_id": random.choice(branch_ids),
                    "product_id": random.choice(product_ids) if random.random() > 0.7 else None,
                    "description": fake.sentence(nb_words=4),
                }

                transaction_id += 1

            # Inject forced transactions for today
            for forced in forced_inserts.get(current, []):
                yield {
                    "transaction_id": transaction_id,
                    "dt": current,
                    "transaction_time": datetime.combine(current, fake.time_object()),
                    "account_id": forced["account_id"],
                    "customer_id": random.choice(customer_ids),
                    "counter_account_id": None,
                    "transaction_type": forced["transaction_type"],
                    "transaction_channel": forced["transaction_channel"],
                    "amount": round(random.uniform(100, 5000), 2),
                    "currency": "CNY",
                    "balance_after": round(random.uniform(0, 100000), 2),
                    "branch_id": random.choice(branch_ids),
                    "product_id": None,
                    "description": f"forced txn for event {forced.get('_event_id', '')}",
                }
                transaction_id += 1

            current += timedelta(days=1)

    def generate_balance_daily(
        self,
        account_ids: list[str],
        customer_ids: list[str],
        product_ids: list[str],
        branch_ids: list[str],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        force_account_ids: list[str] | None = None,
        anchor_metadata: dict[str, dict] | None = None,
    ) -> Generator[dict, None, None]:
        """Generate daily balance snapshots (one per account per day).

        force_account_ids: optional list of anchor accounts to snapshot daily
        regardless of the random sampling rate applied to ``account_ids``.
        anchor_metadata: optional account_id -> {customer_id, product_id, branch_id}
        lookup. When set, anchor rows use real dim_account values instead of random
        picks, so verify_events SQL filters resolve to the anchored cohort.
        """

        meta = anchor_metadata or {}
        current = start_date
        while current <= end_date:
            sampled = account_ids[: max(1, len(account_ids) // 100)]
            anchors = force_account_ids or []
            for account_id in list(sampled) + list(anchors):
                if account_id in meta:
                    m = meta[account_id]
                    customer_id = m["customer_id"]
                    product_id = m["product_id"]
                    branch_id = m["branch_id"]
                    # Anchor base balance is deterministic — random.expovariate's
                    # CV=1 makes verify_events percent-change noise dominate the
                    # event signal on 50-customer cohorts. Propagation still runs.
                    balance = 100000.0
                else:
                    customer_id = random.choice(customer_ids)
                    product_id = random.choice(product_ids) if random.random() > 0.6 else None
                    branch_id = random.choice(branch_ids)
                    balance = round(random.expovariate(1 / 100000), 2)

                avg_balance_mtd = balance * random.uniform(0.8, 1.2)

                yield {
                    "dt": current,
                    "account_id": account_id,
                    "customer_id": customer_id,
                    "product_id": product_id,
                    "branch_id": branch_id,
                    "balance": balance,
                    "avg_balance_mtd": round(avg_balance_mtd, 2),
                    "currency": "CNY",
                }

            current += timedelta(days=1)

    def generate_holdings(
        self,
        customer_ids: list[str],
        product_ids: list[str],
        branch_ids: list[str],
        snapshot_date: date = date(2026, 5, 31),
        count: int = 1000,
    ) -> Generator[dict, None, None]:
        """Generate fund/wealth holdings snapshot."""

        for i in range(count):
            customer_id = random.choice(customer_ids)
            product_id = random.choice(product_ids)
            branch_id = random.choice(branch_ids)

            amount = round(random.uniform(10000, 1000000), 2)
            shares = round(random.uniform(100, 100000), 4)
            cost_basis = amount * random.uniform(0.9, 1.1)
            market_value = cost_basis * random.uniform(0.85, 1.15)
            pnl = market_value - cost_basis

            yield {
                "snapshot_dt": snapshot_date,
                "customer_id": customer_id,
                "account_id": f"622202{i:010d}",
                "product_id": product_id,
                "branch_id": branch_id,
                "holding_amount": amount,
                "holding_shares": shares,
                "market_value": round(market_value, 2),
                "cost_basis": round(cost_basis, 2),
                "pnl": round(pnl, 2),
                "currency": "CNY",
            }

    def generate_risk_events(
        self,
        customer_ids: list[str],
        account_ids: list[str],
        branch_ids: list[str],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        daily_event_rate: float = 0.01,
    ) -> Generator[dict, None, None]:
        """Generate risk events (low frequency, ~1% of days have an event)."""

        event_id = 1
        current = start_date

        while current <= end_date:
            if random.random() < daily_event_rate:
                event_types = ["OVERDUE", "FRAUD", "AML_ALERT", "CREDIT_DOWNGRADE", "DISPUTE"]
                severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

                yield {
                    "event_id": event_id,
                    "event_time": datetime.combine(current, fake.time_object()),
                    "dt": current,
                    "customer_id": random.choice(customer_ids),
                    "account_id": random.choice(account_ids) if random.random() > 0.3 else None,
                    "event_type": random.choice(event_types),
                    "severity": random.choice(severities),
                    "amount": (
                        round(random.uniform(1000, 100000), 2)
                        if random.random() > 0.3
                        else None
                    ),
                    "status": random.choice(["OPEN", "INVESTIGATING", "CLOSED", "CONFIRMED"]),
                    "branch_id": random.choice(branch_ids),
                    "description": fake.sentence(nb_words=5),
                }

                event_id += 1

            current += timedelta(days=1)

    def generate_campaign_responses(
        self,
        customer_ids: list[str],
        product_ids: list[str],
        branch_ids: list[str],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        daily_response_rate: float = 0.05,
    ) -> Generator[dict, None, None]:
        """Generate marketing campaign responses."""

        response_id = 1
        campaigns = [
            f"CAMP_{i:04d}" for i in range(50)
        ]
        campaign_names = [
            "春节储蓄活动",
            "理财产品推荐",
            "信用卡申请",
            "基金定投",
            "保险覆盖",
        ]

        current = start_date
        while current <= end_date:
            daily_responses = int(len(customer_ids) * daily_response_rate)
            for _ in range(daily_responses):
                customer_id = random.choice(customer_ids)
                campaign_id = random.choice(campaigns)
                response_types = ["NO_RESPONSE", "CLICKED", "INTERESTED", "CONVERTED", "REJECTED"]

                response_type = random.choices(
                    response_types, weights=[50, 20, 15, 10, 5]
                )[0]

                conversion_time = None
                conversion_amount = None
                if response_type == "CONVERTED":
                    conversion_time = datetime.combine(
                        current + timedelta(days=random.randint(0, 7)),
                        fake.time_object(),
                    )
                    conversion_amount = round(random.uniform(10000, 500000), 2)

                yield {
                    "response_id": response_id,
                    "campaign_id": campaign_id,
                    "campaign_name": random.choice(campaign_names),
                    "customer_id": customer_id,
                    "touch_time": datetime.combine(current, fake.time_object()),
                    "dt": current,
                    "channel": random.choice(["SMS", "APP_PUSH", "CALL", "EMAIL", "IN_PERSON"]),
                    "response_type": response_type,
                    "conversion_time": conversion_time,
                    "conversion_amount": conversion_amount,
                    "product_id": (
                        random.choice(product_ids) if response_type == "CONVERTED" else None
                    ),
                    "branch_id": random.choice(branch_ids),
                }

                response_id += 1

            current += timedelta(days=1)
