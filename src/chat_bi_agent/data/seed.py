"""Seed script: orchestrate all dimension and fact table generation."""

import sys
from datetime import date

import click

from chat_bi_agent.data.db import DatabaseConfig, get_cursor
from chat_bi_agent.data.dimension_generator import DimensionGenerator
from chat_bi_agent.data.event_loader import EventLoader
from chat_bi_agent.data.propagation_engine import PropagationEngine, PropagationRule
from chat_bi_agent.data.transaction_generator import TransactionGenerator


def insert_rows(cursor, table: str, rows: list[dict], batch_size: int = 1000) -> int:
    """Insert rows in batches. Returns count of inserted rows."""
    if not rows:
        return 0

    col_names = list(rows[0].keys())
    # 过滤掉元数据列（_propagations 等）
    col_names = [c for c in col_names if not c.startswith("_")]
    col_str = ", ".join(col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"

    inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        for row in batch:
            values = tuple(row.get(c) for c in col_names)
            cursor.execute(insert_sql, values)
        inserted += len(batch)
        if (i + batch_size) % (batch_size * 10) == 0:
            print(f"  {table}: inserted {inserted} rows...", file=sys.stderr)

    return inserted


def apply_event_propagations(
    row: dict, events: list, engine: PropagationEngine, current_date: date
) -> None:
    """对单行应用事件传导规则。"""
    for event in events:
        # 跳过时间不符的事件
        if (current_date - event.date).days < -20 or (current_date - event.date).days > 40:
            continue

        for prop_dict in event.propagation:
            rule = PropagationRule(
                target_table=prop_dict.get("target_table", ""),
                target_column=prop_dict.get("target_column", ""),
                metric_name=prop_dict.get("metric_name", ""),
                delta=prop_dict.get("delta", 0),
                delay_days=prop_dict.get("delay_days", 0),
                ramp_days=prop_dict.get("ramp_days", 0),
                ramp_type=prop_dict.get("ramp_type", "linear"),
                affected_account_sample=prop_dict.get("affected_account_sample", 1.0),
                affected_customer_sample=prop_dict.get("affected_customer_sample", 1.0),
                renewal_rate=prop_dict.get("renewal_rate"),
                related_products=prop_dict.get("related_products"),
                transaction_type=prop_dict.get("transaction_type"),
                transaction_channel=prop_dict.get("transaction_channel"),
            )
            engine.apply_rule_to_row(rule, row, event.date, current_date, event_id=event.id)


def seed_dimensions(
    config: DatabaseConfig, generator: DimensionGenerator, branch_count: int = 50
) -> dict[str, int]:
    """Seed all dimension tables. Returns counts."""
    counts = {}

    with get_cursor(config) as cursor:
        # dim_branch
        print("⏳ Seeding dim_branch...", file=sys.stderr)
        branches = list(generator.generate_branches(count=branch_count))
        counts["dim_branch"] = insert_rows(cursor, "dim_branch", branches)

        branch_ids = [b["branch_id"] for b in branches]

        # dim_product
        print("⏳ Seeding dim_product...", file=sys.stderr)
        products = list(generator.generate_products(count=100))
        counts["dim_product"] = insert_rows(cursor, "dim_product", products)

        product_ids = [p["product_id"] for p in products]

        # dim_customer
        print("⏳ Seeding dim_customer...", file=sys.stderr)
        customers = list(generator.generate_customers(branch_ids=branch_ids, count=5000))
        counts["dim_customer"] = insert_rows(cursor, "dim_customer", customers)

        customer_ids = [c["customer_id"] for c in customers]

        # dim_account
        print("⏳ Seeding dim_account...", file=sys.stderr)
        accounts = list(
            generator.generate_accounts(
                customer_ids=customer_ids,
                product_ids=product_ids,
                branch_ids=branch_ids,
                count=10000,
            )
        )
        counts["dim_account"] = insert_rows(cursor, "dim_account", accounts)

        account_ids = [a["account_id"] for a in accounts]

        # dim_date
        print("⏳ Seeding dim_date...", file=sys.stderr)
        dates = list(
            generator.generate_dates(
                start_date=date(2025, 1, 1), end_date=date(2026, 12, 31)
            )
        )
        counts["dim_date"] = insert_rows(cursor, "dim_date", dates)

    return counts, branch_ids, customer_ids, product_ids, account_ids


def seed_facts(
    config: DatabaseConfig,
    generator: TransactionGenerator,
    branch_ids: list[str],
    customer_ids: list[str],
    product_ids: list[str],
    account_ids: list[str],
    transaction_rows: int = 100000,
    events: list = None,
) -> dict[str, int]:
    """Seed all fact tables. Returns counts."""
    counts = {}
    events = events or []
    engine = PropagationEngine() if events else None

    with get_cursor(config) as cursor:
        # fct_transaction
        print(f"⏳ Seeding fct_transaction (~{transaction_rows} rows)...", file=sys.stderr)
        txn_batch = []
        txn_count = 0
        for row in generator.generate_transactions(
            account_ids=account_ids,
            customer_ids=customer_ids,
            product_ids=product_ids,
            branch_ids=branch_ids,
            start_date=date(2025, 1, 1),
            end_date=date(2026, 5, 31),
            transactions_per_account_per_month=transaction_rows / len(account_ids) / 17,
        ):
            # 应用事件传导规则
            if engine and events:
                apply_event_propagations(row, events, engine, row.get("dt"))

            txn_batch.append(row)
            txn_count += 1
            if len(txn_batch) >= 1000 or txn_count >= transaction_rows:
                insert_rows(cursor, "fct_transaction", txn_batch, batch_size=1000)
                txn_batch = []
                if txn_count >= transaction_rows:
                    break

        counts["fct_transaction"] = txn_count

        # fct_balance_daily
        print("⏳ Seeding fct_balance_daily...", file=sys.stderr)
        bal_batch = []
        bal_count = 0
        for row in generator.generate_balance_daily(
            account_ids=account_ids,
            customer_ids=customer_ids,
            product_ids=product_ids,
            branch_ids=branch_ids,
            start_date=date(2025, 1, 1),
            end_date=date(2026, 5, 31),
        ):
            if engine and events:
                apply_event_propagations(row, events, engine, row.get("dt"))

            bal_batch.append(row)
            if len(bal_batch) >= 1000:
                bal_count += insert_rows(cursor, "fct_balance_daily", bal_batch)
                bal_batch = []

        if bal_batch:
            bal_count += insert_rows(cursor, "fct_balance_daily", bal_batch)

        counts["fct_balance_daily"] = bal_count

        # fct_holding
        print("⏳ Seeding fct_holding...", file=sys.stderr)
        holdings = list(
            generator.generate_holdings(
                customer_ids=customer_ids,
                product_ids=product_ids,
                branch_ids=branch_ids,
                count=3000,
            )
        )
        counts["fct_holding"] = insert_rows(cursor, "fct_holding", holdings)

        # fct_risk_event
        print("⏳ Seeding fct_risk_event...", file=sys.stderr)
        risk_batch = []
        risk_count = 0
        for row in generator.generate_risk_events(
            customer_ids=customer_ids,
            account_ids=account_ids,
            branch_ids=branch_ids,
            start_date=date(2025, 1, 1),
            end_date=date(2026, 5, 31),
        ):
            risk_batch.append(row)
            if len(risk_batch) >= 1000:
                risk_count += insert_rows(cursor, "fct_risk_event", risk_batch)
                risk_batch = []

        if risk_batch:
            risk_count += insert_rows(cursor, "fct_risk_event", risk_batch)

        counts["fct_risk_event"] = risk_count

        # fct_campaign_response
        print("⏳ Seeding fct_campaign_response...", file=sys.stderr)
        camp_batch = []
        camp_count = 0
        for row in generator.generate_campaign_responses(
            customer_ids=customer_ids,
            product_ids=product_ids,
            branch_ids=branch_ids,
            start_date=date(2025, 1, 1),
            end_date=date(2026, 5, 31),
        ):
            camp_batch.append(row)
            if len(camp_batch) >= 1000:
                camp_count += insert_rows(cursor, "fct_campaign_response", camp_batch)
                camp_batch = []

        if camp_batch:
            camp_count += insert_rows(cursor, "fct_campaign_response", camp_batch)

        counts["fct_campaign_response"] = camp_count

    return counts


@click.command()
@click.option(
    "--host",
    default="localhost",
    help="PostgreSQL host (default: localhost)",
)
@click.option(
    "--port",
    type=int,
    default=5432,
    help="PostgreSQL port (default: 5432)",
)
@click.option(
    "--database",
    default="chatbi",
    help="Database name (default: chatbi)",
)
@click.option(
    "--user",
    default="chatbi",
    help="Database user (default: chatbi)",
)
@click.option(
    "--password",
    default="chatbi_dev",
    help="Database password (default: chatbi_dev)",
)
@click.option(
    "--rows",
    type=int,
    default=100000,
    help="Target transaction rows (default: 100000)",
)
@click.option(
    "--truncate",
    is_flag=True,
    default=False,
    help="Truncate all tables before seeding",
)
@click.option(
    "--seed",
    type=int,
    default=42,
    help="Random seed (default: 42)",
)
@click.option(
    "--with-events",
    is_flag=True,
    default=False,
    help="Apply event propagations (for root-cause attribution evaluation)",
)
def main(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    rows: int,
    truncate: bool,
    seed: int,
    with_events: bool,
):
    """Seed chat-bi-agent database with mock banking data and optional event propagations."""

    config = DatabaseConfig(host=host, port=port, database=database, user=user, password=password)

    print(f"🚀 Seeding {database}@{host}:{port} with {rows} transaction rows...", file=sys.stderr)

    # Load events if requested (for attribution/RCA evaluation)
    events = []
    if with_events:
        print("📌 Loading event propagations...", file=sys.stderr)
        loader = EventLoader()
        events = loader.load_all_events()
        print(f"   Loaded {len(events)} events for attribution analysis", file=sys.stderr)

    # Truncate if requested
    if truncate:
        print("🗑️  Truncating all tables...", file=sys.stderr)
        tables = [
            "fct_campaign_response",
            "fct_risk_event",
            "fct_holding",
            "fct_balance_daily",
            "fct_transaction",
            "dim_account",
            "dim_customer",
            "dim_product",
            "dim_branch",
            "dim_date",
        ]
        with get_cursor(config) as cursor:
            for table in tables:
                cursor.execute(f"TRUNCATE TABLE {table} CASCADE")

    # Generate dimensions
    dim_gen = DimensionGenerator(seed=seed)
    (
        dim_counts,
        branch_ids,
        customer_ids,
        product_ids,
        account_ids,
    ) = seed_dimensions(config, dim_gen, branch_count=50)

    # Generate facts
    txn_gen = TransactionGenerator(seed=seed, events=events)
    fact_counts = seed_facts(
        config,
        txn_gen,
        branch_ids=branch_ids,
        customer_ids=customer_ids,
        product_ids=product_ids,
        account_ids=account_ids,
        transaction_rows=rows,
        events=events,
    )

    all_counts = {**dim_counts, **fact_counts}

    # Summary
    print("\n✅ Seeding complete!", file=sys.stderr)
    print("\nTable row counts:", file=sys.stderr)
    total = 0
    for table in [
        "dim_branch",
        "dim_customer",
        "dim_product",
        "dim_account",
        "dim_date",
        "fct_transaction",
        "fct_balance_daily",
        "fct_holding",
        "fct_risk_event",
        "fct_campaign_response",
    ]:
        count = all_counts.get(table, 0)
        total += count
        print(f"  {table:30s}: {count:>10,}", file=sys.stderr)

    print(f"\n{'Total':30s}: {total:>10,}", file=sys.stderr)


if __name__ == "__main__":
    main()
