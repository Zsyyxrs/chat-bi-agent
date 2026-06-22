"""Transaction and fact table generators with business rules (workday/month-end effects).

Cross-fact relational consistency:
- fct_transaction / fct_balance_daily / fct_holding rows preserve
  account → customer / branch / product binding (no random reassignment).
- fct_risk_event / fct_campaign_response rows preserve customer → branch binding.
- Lifecycle: rows are not emitted before an account's open_date or after its
  close_date / a product's expiry_date.
"""

import random
from datetime import date, datetime, timedelta
from typing import Generator

from faker import Faker

fake = Faker("zh_CN")


class TransactionGenerator:
    """Generate transaction and fact table rows with realistic banking patterns."""

    # account_type that holds wealth/fund/insurance products and appears in fct_holding.
    _HOLDING_ACCOUNT_TYPES = {"INVESTMENT"}

    def __init__(self, seed: int = 42, events: list = None):
        self.seed = seed
        self.events = events or []
        random.seed(seed)
        Faker.seed(seed)

    # ------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------

    @staticmethod
    def _index_by(records: list[dict], key: str) -> dict[str, dict]:
        return {r[key]: r for r in records}

    @staticmethod
    def _sample_baseline_amount(txn_type: str) -> float:
        """Baseline transaction amount distribution shared by random and forced flows.

        Used to be: forced rows used `uniform(100, 5000)` (avg ~2500), but baseline
        used a heavy-tailed mix (avg ~40000). When forced rows dominated the count
        on event windows, aggregate SUM(amount) dropped instead of rising despite
        the +25% propagation bump — masking the planted signal. Aligning the two
        keeps the signal direction correct.

        WITHDRAW gets a tighter cap (≤200K) reflecting real ATM/counter cash
        withdrawal limits — without this, the heavy-tail (100K-1M) 5% slug
        dominates aggregate SUM(amount) noise and masks the spring-festival
        +25% propagation lift on small samples.
        """
        if txn_type == "INTEREST":
            return round(random.uniform(0.1, 500), 2)
        if txn_type == "FEE":
            return round(random.uniform(1, 100), 2)
        if txn_type == "WITHDRAW":
            r = random.random()
            if r < 0.85:
                return round(random.uniform(100, 5000), 2)  # ATM 主力区间
            if r < 0.98:
                return round(random.uniform(5000, 50000), 2)  # 柜面常规
            return round(random.uniform(50000, 200000), 2)  # 大额柜面取现
        r = random.random()
        if r < 0.80:
            return round(random.uniform(10, 10000), 2)
        if r < 0.95:
            return round(random.uniform(10000, 100000), 2)
        return round(random.uniform(100000, 1000000), 2)

    @staticmethod
    def _is_active(meta: dict | None, on_date: date) -> bool:
        if meta is None:
            return False
        open_d = meta.get("open_date")
        close_d = meta.get("close_date")
        if open_d is not None and on_date < open_d:
            return False
        if close_d is not None and on_date > close_d:
            return False
        return True

    # ------------------------------------------------------------
    # fct_transaction
    # ------------------------------------------------------------

    def generate_transactions(
        self,
        accounts: list[dict],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        transactions_per_account_per_month: float = 2.5,
        force_specs: list | None = None,
        anchor_metadata: dict[str, dict] | None = None,
    ) -> Generator[dict, None, None]:
        """
        Generate transaction data with business rules.

        Each row's customer_id / branch_id / product_id are derived from the chosen
        account, so JOINs to dim_account stay self-consistent. Transactions outside
        an account's open_date..close_date window are skipped. Forced anchor flows
        (force_specs) may reference accounts not in `accounts`; pass their dim_account
        metadata via `anchor_metadata`.
        """

        transaction_id = 1
        account_index = self._index_by(accounts, "account_id")
        anchor_meta = anchor_metadata or {}

        def lookup_account(acct_id: str) -> dict | None:
            if acct_id in account_index:
                return account_index[acct_id]
            if acct_id in anchor_meta:
                return anchor_meta[acct_id]
            return None

        forced_inserts: dict[date, list[dict]] = {}
        if force_specs:
            for spec in force_specs:
                window_start = max(
                    start_date,
                    spec.event_date + timedelta(days=spec.injection_start_offset_days),
                )
                window_end = min(
                    end_date,
                    spec.event_date + timedelta(days=spec.injection_end_offset_days),
                )
                window_days = max(1, (window_end - window_start).days + 1)
                for acct_id in spec.account_ids:
                    for k in range(spec.min_txn_per_customer):
                        offset = (k * 7) % window_days
                        d = window_start + timedelta(days=offset)
                        forced_inserts.setdefault(d, []).append(
                            {
                                "_event_id": spec.event_id,
                                "account_id": acct_id,
                                "transaction_type": spec.txn_type,
                                "transaction_channel": (spec.channels or ["MOBILE"])[0],
                            }
                        )

        regular_account_ids = list(account_index.keys())
        current = start_date

        while current <= end_date:
            is_month_end_window = (current + timedelta(days=3)).month != current.month
            month_end_multiplier = 5.0 if is_month_end_window else 1.0
            is_weekend = current.weekday() >= 5
            workday_multiplier = 0.3 if is_weekend else 1.0

            daily_transaction_count = int(
                max(1, len(regular_account_ids))
                * transactions_per_account_per_month
                * (1 / 30.0)
                * month_end_multiplier
                * workday_multiplier
            )

            for _ in range(max(1, daily_transaction_count)):
                if not regular_account_ids:
                    break
                account_id = random.choice(regular_account_ids)
                acct = account_index[account_id]
                if not self._is_active(acct, current):
                    continue
                customer_id = acct["customer_id"]
                branch_id = acct["branch_id"]
                counter_account_id = (
                    random.choice(regular_account_ids) if random.random() > 0.2 else None
                )

                txn_types = ["DEPOSIT", "WITHDRAW", "TRANSFER", "PAYMENT", "INTEREST", "FEE"]
                txn_type_dist = random.choices(txn_types, weights=[15, 20, 30, 25, 5, 5])[0]
                channels = ["MOBILE", "INTERNET", "COUNTER", "ATM", "AGENT", "API"]
                channel_dist = random.choices(channels, weights=[40, 30, 10, 10, 5, 5])[0]

                amount = self._sample_baseline_amount(txn_type_dist)

                balance_after = round(random.uniform(0, 10000000), 2)
                transaction_time = datetime.combine(current, fake.time_object())

                # 30% of transactions are product-attributed; uses account's bound product.
                if acct.get("product_id") and random.random() > 0.7:
                    product_id = acct["product_id"]
                else:
                    product_id = None

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
                    "branch_id": branch_id,
                    "product_id": product_id,
                    "description": fake.sentence(nb_words=4),
                }

                transaction_id += 1

            for forced in forced_inserts.get(current, []):
                acct = lookup_account(forced["account_id"])
                if acct is None or not self._is_active(acct, current):
                    continue
                yield {
                    "transaction_id": transaction_id,
                    "dt": current,
                    "transaction_time": datetime.combine(current, fake.time_object()),
                    "account_id": forced["account_id"],
                    "customer_id": acct["customer_id"],
                    "counter_account_id": None,
                    "transaction_type": forced["transaction_type"],
                    "transaction_channel": forced["transaction_channel"],
                    "amount": self._sample_baseline_amount(forced["transaction_type"]),
                    "currency": "CNY",
                    "balance_after": round(random.uniform(0, 100000), 2),
                    "branch_id": acct["branch_id"],
                    "product_id": acct.get("product_id"),
                    "description": f"forced txn for event {forced.get('_event_id', '')}",
                }
                transaction_id += 1

            current += timedelta(days=1)

    # ------------------------------------------------------------
    # fct_balance_daily
    # ------------------------------------------------------------

    def generate_balance_daily(
        self,
        accounts: list[dict],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        force_account_ids: list[str] | None = None,
        anchor_metadata: dict[str, dict] | None = None,
        account_close_dates: dict[str, date] | None = None,
        product_expiry_dates: dict[str, date] | None = None,
    ) -> Generator[dict, None, None]:
        """Generate daily balance snapshots (one per account per day).

        customer_id / branch_id / product_id on each row come from the bound
        account (or `anchor_metadata` for forced accounts not in `accounts`).
        Days outside an account's open_date..close_date window are skipped, as
        are non-anchor rows whose bound product is past its expiry.

        force_account_ids: anchor accounts to snapshot daily regardless of the
        random sampling rate applied to `accounts`.
        anchor_metadata: account_id -> {customer_id, product_id, branch_id, ...}
        for forced accounts not present in `accounts`.
        account_close_dates: account_id -> close_date override (event-driven
        closures derived from PRODUCT_EXPIRY lifecycle).
        product_expiry_dates: product_id -> expiry_date; non-anchor rows whose
        bound product has expired on the current date have product_id set to None.
        """

        account_index = self._index_by(accounts, "account_id")
        anchor_meta = anchor_metadata or {}
        closures = account_close_dates or {}
        expiries = product_expiry_dates or {}

        def effective_close(acct: dict | None, acct_id: str) -> date | None:
            override = closures.get(acct_id)
            if override is not None:
                return override
            return acct.get("close_date") if acct else None

        def effective_open(acct: dict | None) -> date | None:
            return acct.get("open_date") if acct else None

        # 跨整池随机抽 ~1%（同 ID 每天都有 balance 行，固定一组以保持连续性）。
        # 原来用切片取前 100 个 account_id，导致按 cohort 查询（如 HNW+上海+CURRENT）
        # 几乎必然为空——3 个上海 HNW CURRENT 账户的 i 大概率 > 100。
        all_ids = list(account_index.keys())
        sample_size = max(1, len(all_ids) // 100)
        sampled_ids = random.sample(all_ids, min(sample_size, len(all_ids)))
        anchors = list(force_account_ids or [])
        current = start_date
        while current <= end_date:
            for account_id in sampled_ids + anchors:
                is_anchor = account_id in anchor_meta or account_id in anchors
                acct = account_index.get(account_id) or anchor_meta.get(account_id)

                open_d = effective_open(acct)
                close_d = effective_close(acct, account_id)
                if open_d is not None and current < open_d:
                    continue
                if close_d is not None and current > close_d:
                    continue

                if is_anchor and account_id in anchor_meta:
                    m = anchor_meta[account_id]
                    customer_id = m["customer_id"]
                    product_id = m["product_id"]
                    branch_id = m["branch_id"]
                    # Anchor base balance is deterministic — random.expovariate's
                    # CV=1 makes verify_events percent-change noise dominate the
                    # event signal on 50-customer cohorts. Propagation still runs.
                    balance = 100000.0
                else:
                    customer_id = acct["customer_id"]
                    branch_id = acct["branch_id"]
                    bound_product = acct.get("product_id")
                    if bound_product and current > expiries.get(bound_product, current):
                        product_id = None
                    else:
                        product_id = bound_product
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

    # ------------------------------------------------------------
    # fct_holding
    # ------------------------------------------------------------

    def generate_holdings(
        self,
        accounts: list[dict],
        snapshot_date: date = date(2026, 5, 31),
        count: int = 1000,
        excluded_product_ids: set[str] | None = None,
    ) -> Generator[dict, None, None]:
        """Generate fund/wealth holdings snapshot.

        Holdings are tied to real INVESTMENT accounts: customer_id / product_id /
        branch_id / account_id all come from the chosen account row, so any JOIN
        back to dim_account stays consistent. Accounts whose product is in
        `excluded_product_ids` (or that are closed by `snapshot_date`) are
        dropped from the candidate pool.
        """

        excluded = excluded_product_ids or set()

        pool: list[dict] = []
        for a in accounts:
            if a.get("account_type") not in self._HOLDING_ACCOUNT_TYPES:
                continue
            product_id = a.get("product_id")
            if not product_id or product_id in excluded:
                continue
            close_d = a.get("close_date")
            if close_d is not None and snapshot_date > close_d:
                continue
            open_d = a.get("open_date")
            if open_d is not None and snapshot_date < open_d:
                continue
            pool.append(a)

        if not pool:
            raise ValueError(
                "generate_holdings: no INVESTMENT account candidates available "
                "(check account_type / product_id / lifecycle filters)"
            )

        # fct_holding pkey is (snapshot_dt, account_id, product_id). Since each
        # account is bound to a single product, one row per account is the most
        # we can emit on a given snapshot. Sample without replacement.
        sampled = random.sample(pool, k=min(count, len(pool)))
        # 确定性持仓：holding_amount / cost_basis 跨月稳定（按 account_id 种子），
        # 只让 market_value 做 ±2% 的月度漂移（按 (account_id, year-month) 种子）。
        # 这样事件信号（如 PROD_WEA_0030 因到期被排除）不会被随机噪声盖掉。
        month_key = snapshot_date.year * 12 + snapshot_date.month
        for acct in sampled:
            acct_id = acct["account_id"]
            base_rnd = random.Random(hash(("holding_base", acct_id)) & 0xFFFFFFFF)
            amount = round(base_rnd.uniform(10000, 1000000), 2)
            shares = round(base_rnd.uniform(100, 100000), 4)
            cost_basis = round(amount * base_rnd.uniform(0.9, 1.1), 2)

            drift_rnd = random.Random(hash(("holding_drift", acct_id, month_key)) & 0xFFFFFFFF)
            drift = drift_rnd.uniform(-0.02, 0.02)
            market_value = round(cost_basis * (1.0 + drift), 2)
            pnl = round(market_value - cost_basis, 2)

            yield {
                "snapshot_dt": snapshot_date,
                "customer_id": acct["customer_id"],
                "account_id": acct_id,
                "product_id": acct["product_id"],
                "branch_id": acct["branch_id"],
                "holding_amount": amount,
                "holding_shares": shares,
                "market_value": market_value,
                "cost_basis": cost_basis,
                "pnl": pnl,
                "currency": "CNY",
            }

    # ------------------------------------------------------------
    # fct_risk_event
    # ------------------------------------------------------------

    def generate_risk_events(
        self,
        customers: list[dict],
        accounts: list[dict],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        daily_event_rate: float = 0.01,
    ) -> Generator[dict, None, None]:
        """Generate risk events (low frequency, ~1% of days have an event).

        customer_id → branch_id is inherited from dim_customer. When an account
        is attached (70%), it is picked from that customer's accounts and the
        row's branch_id is overridden by the account's branch_id (handles the
        rare case of cross-branch accounts).
        """

        customer_index = self._index_by(customers, "customer_id")
        accounts_by_customer: dict[str, list[dict]] = {}
        for a in accounts:
            accounts_by_customer.setdefault(a["customer_id"], []).append(a)

        if not customer_index:
            return

        customer_ids = list(customer_index.keys())
        event_id = 1
        current = start_date

        while current <= end_date:
            if random.random() < daily_event_rate:
                event_types = ["OVERDUE", "FRAUD", "AML_ALERT", "CREDIT_DOWNGRADE", "DISPUTE"]
                severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

                cust = customer_index[random.choice(customer_ids)]
                branch_id = cust["branch_id"]
                account_id = None
                if random.random() > 0.3:
                    cust_accts = accounts_by_customer.get(cust["customer_id"]) or []
                    if cust_accts:
                        acct = random.choice(cust_accts)
                        account_id = acct["account_id"]
                        branch_id = acct["branch_id"]

                yield {
                    "event_id": event_id,
                    "event_time": datetime.combine(current, fake.time_object()),
                    "dt": current,
                    "customer_id": cust["customer_id"],
                    "account_id": account_id,
                    "event_type": random.choice(event_types),
                    "severity": random.choice(severities),
                    "amount": (
                        round(random.uniform(1000, 100000), 2) if random.random() > 0.3 else None
                    ),
                    "status": random.choice(["OPEN", "INVESTIGATING", "CLOSED", "CONFIRMED"]),
                    "branch_id": branch_id,
                    "description": fake.sentence(nb_words=5),
                }

                event_id += 1

            current += timedelta(days=1)

    # ------------------------------------------------------------
    # fct_campaign_response
    # ------------------------------------------------------------

    def generate_campaign_responses(
        self,
        customers: list[dict],
        product_ids: list[str],
        start_date: date = date(2025, 1, 1),
        end_date: date = date(2026, 5, 31),
        daily_response_rate: float = 0.05,
    ) -> Generator[dict, None, None]:
        """Generate marketing campaign responses.

        customer_id → branch_id is inherited from dim_customer. product_id stays
        independent (campaigns target catalogue-wide products), but is only set
        on CONVERTED rows, matching the original semantics.
        """

        response_id = 1
        campaigns = [f"CAMP_{i:04d}" for i in range(50)]
        campaign_names = [
            "春节储蓄活动",
            "理财产品推荐",
            "信用卡申请",
            "基金定投",
            "保险覆盖",
        ]

        if not customers:
            return

        current = start_date
        while current <= end_date:
            daily_responses = int(len(customers) * daily_response_rate)
            for _ in range(daily_responses):
                cust = random.choice(customers)
                campaign_id = random.choice(campaigns)
                response_types = ["NO_RESPONSE", "CLICKED", "INTERESTED", "CONVERTED", "REJECTED"]

                response_type = random.choices(response_types, weights=[50, 20, 15, 10, 5])[0]

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
                    "customer_id": cust["customer_id"],
                    "touch_time": datetime.combine(current, fake.time_object()),
                    "dt": current,
                    "channel": random.choice(["SMS", "APP_PUSH", "CALL", "EMAIL", "IN_PERSON"]),
                    "response_type": response_type,
                    "conversion_time": conversion_time,
                    "conversion_amount": conversion_amount,
                    "product_id": (
                        random.choice(product_ids) if response_type == "CONVERTED" else None
                    ),
                    "branch_id": cust["branch_id"],
                }

                response_id += 1

            current += timedelta(days=1)
