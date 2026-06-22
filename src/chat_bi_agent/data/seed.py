"""Seed script: orchestrate all dimension and fact table generation."""

import calendar
import sys
from dataclasses import dataclass, field
from datetime import date

import click

from chat_bi_agent.data.db import DatabaseConfig, get_cursor
from chat_bi_agent.data.dimension_generator import DimensionGenerator
from chat_bi_agent.data.event_loader import Event, EventLoader
from chat_bi_agent.data.propagation_engine import PropagationEngine, PropagationRule
from chat_bi_agent.data.scenario_anchor import anchor_event_populations
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


def _month_end_snapshots(start: date, end: date) -> list[date]:
    """Return month-end dates falling within [start, end] inclusive."""
    snapshots: list[date] = []
    year, month = start.year, start.month
    while True:
        last_day = calendar.monthrange(year, month)[1]
        d = date(year, month, last_day)
        if d > end:
            break
        if d >= start:
            snapshots.append(d)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return snapshots


@dataclass
class ExpiryLifecycle:
    """Outputs of apply_product_expiry_lifecycle, consumed by fact generators."""

    account_close_dates: dict[str, date] = field(default_factory=dict)
    product_expiry_dates: dict[str, date] = field(default_factory=dict)


def apply_product_expiry_lifecycle(cursor, events: list[Event]) -> ExpiryLifecycle:
    """Close accounts holding products that expire on a PRODUCT_EXPIRY event date.

    For each event with type=PRODUCT_EXPIRY and a non-empty affected_dimensions.product_id,
    sets dim_account.close_date = event.date (and status = 'CLOSED') for every account
    holding one of the expired products — but only when the existing close_date is NULL
    or strictly later than the event date (we never push a pre-existing closure forward).

    Returns an ExpiryLifecycle with two dicts:
      - account_close_dates: account_id -> close_date for every matching account,
        using the account's *current* close_date after the UPDATE. The balance
        generator uses this to stop emitting daily rows past the closure date.
      - product_expiry_dates: product_id -> earliest expiry date across events.
        The balance generator uses this to keep its random product_id picker from
        producing post-expiry references on non-anchor rows.
    """
    result = ExpiryLifecycle()
    for event in events:
        if event.type != "PRODUCT_EXPIRY":
            continue
        prod_ids = event.affected_dimensions.get("product_id") or []
        if not prod_ids:
            continue
        cursor.execute(
            "UPDATE dim_account SET close_date = %s, status = 'CLOSED' "
            "WHERE product_id = ANY(%s) AND (close_date IS NULL OR close_date > %s)",
            (event.date, prod_ids, event.date),
        )
        cursor.execute(
            "SELECT account_id, close_date FROM dim_account "
            "WHERE product_id = ANY(%s) AND close_date IS NOT NULL",
            (prod_ids,),
        )
        for acct_id, close_dt in cursor.fetchall():
            # If an account is affected by multiple expiry events, keep the earliest closure.
            if (
                acct_id not in result.account_close_dates
                or close_dt < result.account_close_dates[acct_id]
            ):
                result.account_close_dates[acct_id] = close_dt
        for pid in prod_ids:
            prev = result.product_expiry_dates.get(pid)
            if prev is None or event.date < prev:
                result.product_expiry_dates[pid] = event.date
    return result


def apply_event_propagations(
    row: dict, events: list, engine: PropagationEngine, current_date: date
) -> None:
    """对单行应用事件传导规则。"""
    for event in events:
        # 跳过时间不符的事件。上界放宽到 400 天以容纳 fct_holding 月末快照场景下
        # sustained 效应（如 anxin_90_expire 之后所有未来月末持仓都需要带效应）。
        # 引擎内部的 should_apply_rule 仍会按 effect_type/transient 自行裁剪。
        if (current_date - event.date).days < -20 or (current_date - event.date).days > 400:
            continue

        for prop_dict in event.propagation:
            # 维度过滤：rule 级显式提供则覆盖 event 级（含显式 [] 表示"无过滤"）。
            # 缺省（key 不在 dict 里）才回退到 event.affected_dimensions。
            # 用例：fct_holding 的续作效应需要跨全行/全 tier 看到，不能被 event 级
            # 的 BR_CITY_0006 + HNW 锁死。
            def _resolve_scope(key: str, event_default):
                if key in prop_dict:
                    return prop_dict[key] or None  # 显式 [] → None (no filter)
                return event_default or None

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
                branch_ids=_resolve_scope("branch_ids", event.affected_dimensions.get("branch_id")),
                customer_tiers=_resolve_scope(
                    "customer_tiers", event.affected_dimensions.get("customer_tier")
                ),
                branch_levels=_resolve_scope(
                    "branch_levels", event.affected_dimensions.get("branch_level")
                ),
                product_ids=prop_dict.get("product_ids") or None,
                product_subcategories=prop_dict.get("product_subcategories"),
                effect_type=prop_dict.get("effect_type", "transient"),
            )
            engine.apply_rule_to_row(rule, row, event.date, current_date, event_id=event.id)


def build_dim_indexes(cursor) -> tuple[dict, dict, dict]:
    """After dimensions seeded, build in-memory indexes used by PropagationEngine."""
    cursor.execute("SELECT customer_id, customer_tier, branch_id FROM dim_customer")
    customer_index = {
        row[0]: {"customer_tier": row[1], "branch_id": row[2]} for row in cursor.fetchall()
    }
    cursor.execute("SELECT branch_id, branch_level FROM dim_branch")
    branch_index = {row[0]: {"branch_level": row[1]} for row in cursor.fetchall()}
    cursor.execute("SELECT product_id, product_category, product_subcategory FROM dim_product")
    product_index = {
        row[0]: {"product_category": row[1], "product_subcategory": row[2]}
        for row in cursor.fetchall()
    }
    return customer_index, branch_index, product_index


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
                customers=customers,
                products=products,
                count=10000,
            )
        )
        counts["dim_account"] = insert_rows(cursor, "dim_account", accounts)

        account_ids = [a["account_id"] for a in accounts]

        # dim_date
        print("⏳ Seeding dim_date...", file=sys.stderr)
        dates = list(
            generator.generate_dates(start_date=date(2025, 1, 1), end_date=date(2026, 12, 31))
        )
        counts["dim_date"] = insert_rows(cursor, "dim_date", dates)

    return (
        counts,
        branch_ids,
        customer_ids,
        product_ids,
        account_ids,
        customers,
        products,
        accounts,
    )


def seed_facts(
    config: DatabaseConfig,
    generator: TransactionGenerator,
    branches: list[dict] | None = None,
    customers: list[dict] | None = None,
    products: list[dict] | None = None,
    accounts: list[dict] | None = None,
    transaction_rows: int = 100000,
    events: list = None,
    forced_specs: list | None = None,
    dim_indexes: tuple[dict, dict, dict] | None = None,
) -> dict[str, int]:
    """Seed all fact tables. Returns counts."""
    counts = {}
    events = events or []
    if events and dim_indexes:
        customer_index, branch_index, product_index = dim_indexes
        engine = PropagationEngine(
            customer_index=customer_index,
            branch_index=branch_index,
            product_index=product_index,
        )
    elif events:
        engine = PropagationEngine()
    else:
        engine = None

    with get_cursor(config) as cursor:
        # Pull anchor account ids + metadata once for use by generate_balance_daily.
        # Without metadata, anchor balance rows would get random customer/product/branch
        # and verify_events SQL filters would never resolve to the anchored cohort.
        anchor_account_ids: list[str] = []
        anchor_metadata: dict[str, dict] = {}
        if engine is not None:
            cursor.execute(
                "SELECT account_id, customer_id, product_id, branch_id, "
                "open_date, close_date "
                "FROM dim_account WHERE is_event_anchor = TRUE"
            )
            for r in cursor.fetchall():
                anchor_account_ids.append(r[0])
                anchor_metadata[r[0]] = {
                    "customer_id": r[1],
                    "product_id": r[2],
                    "branch_id": r[3],
                    "open_date": r[4],
                    "close_date": r[5],
                }

        # Apply PRODUCT_EXPIRY lifecycle: close accounts holding expired products so
        # balance/holding generators stop emitting rows past the expiry date.
        lifecycle = apply_product_expiry_lifecycle(cursor, events) if events else ExpiryLifecycle()
        # 月末快照覆盖整个 fact 数据区间，保证 P-o-P 对照点（上月末 vs 当月末）可得。
        holding_snapshot_dates = _month_end_snapshots(date(2025, 1, 1), date(2026, 9, 30))

        # fct_transaction
        print(f"⏳ Seeding fct_transaction (~{transaction_rows} rows)...", file=sys.stderr)
        txn_batch = []
        txn_count = 0
        for row in generator.generate_transactions(
            accounts=accounts,
            start_date=date(2025, 1, 1),
            end_date=date(2026, 9, 30),
            transactions_per_account_per_month=transaction_rows / max(1, len(accounts)) / 17,
            force_specs=forced_specs,
            anchor_metadata=anchor_metadata or None,
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
            accounts=accounts,
            start_date=date(2025, 1, 1),
            end_date=date(2026, 9, 30),
            force_account_ids=anchor_account_ids or None,
            anchor_metadata=anchor_metadata or None,
            account_close_dates=lifecycle.account_close_dates or None,
            product_expiry_dates=lifecycle.product_expiry_dates or None,
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

        # fct_holding（月末快照，每个 snapshot_dt 都单独算 excluded_products）
        print(
            f"⏳ Seeding fct_holding ({len(holding_snapshot_dates)} month-end snapshots)...",
            file=sys.stderr,
        )
        holding_count = 0
        for snap_date in holding_snapshot_dates:
            excluded = {
                pid for pid, expiry in lifecycle.product_expiry_dates.items() if expiry <= snap_date
            }
            holdings = list(
                generator.generate_holdings(
                    accounts=accounts,
                    snapshot_date=snap_date,
                    count=3000,
                    excluded_product_ids=excluded or None,
                )
            )
            if engine and events:
                for row in holdings:
                    apply_event_propagations(row, events, engine, snap_date)
            holding_count += insert_rows(cursor, "fct_holding", holdings)
        counts["fct_holding"] = holding_count

        # fct_risk_event
        print("⏳ Seeding fct_risk_event...", file=sys.stderr)
        risk_batch = []
        risk_count = 0
        for row in generator.generate_risk_events(
            customers=customers,
            accounts=accounts,
            start_date=date(2025, 1, 1),
            end_date=date(2026, 9, 30),
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
            customers=customers,
            product_ids=[p["product_id"] for p in products],
            start_date=date(2025, 1, 1),
            end_date=date(2026, 9, 30),
        ):
            if engine and events:
                apply_event_propagations(row, events, engine, row.get("dt"))
            camp_batch.append(row)
            if len(camp_batch) >= 1000:
                camp_count += insert_rows(cursor, "fct_campaign_response", camp_batch)
                camp_batch = []

        if camp_batch:
            camp_count += insert_rows(cursor, "fct_campaign_response", camp_batch)

        counts["fct_campaign_response"] = camp_count

    return counts


def ensure_readonly_grants(config: DatabaseConfig) -> None:
    """Idempotently re-apply chatbi_readonly grants — keeps reseed self-healing.

    Mirrors docker/postgres/init/03_readonly_role.sql; the init script only runs
    when the PG data volume is empty, so without this call a fresh container
    that misses the init (or a manual REVOKE) silently breaks NL2SQL execution
    with a confusing "relation does not exist" error (PG masks denied-permission
    as missing-relation). Safe to run on every reseed.
    """
    print("⏳ Ensuring chatbi_readonly grants...", file=sys.stderr)
    with get_cursor(config) as cursor:
        cursor.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='chatbi_readonly') THEN
                    CREATE ROLE chatbi_readonly WITH LOGIN PASSWORD 'readonly_dev';
                END IF;
            END $$;
            """
        )
        cursor.execute(f"GRANT CONNECT ON DATABASE {config.database} TO chatbi_readonly")
        cursor.execute("GRANT USAGE ON SCHEMA public TO chatbi_readonly")
        cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO chatbi_readonly")
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {config.user} IN SCHEMA public "
            "GRANT SELECT ON TABLES TO chatbi_readonly"
        )
        cursor.execute(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public "
            "FROM chatbi_readonly"
        )
    print("✅ chatbi_readonly grants applied", file=sys.stderr)


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
        _,  # customer_ids (unused — full dicts below)
        _,  # product_ids
        _,  # account_ids
        customers,
        products,
        accounts,
    ) = seed_dimensions(config, dim_gen, branch_count=50)

    # Anchor event populations into dim tables (if needed)
    forced_specs = []
    dim_indexes = None
    if with_events:
        print("📌 Anchoring event populations...", file=sys.stderr)
        with get_cursor(config) as cursor:
            _, branch_index, product_index = build_dim_indexes(cursor)

            report = anchor_event_populations(
                cursor=cursor,
                events=events,
                branch_ids=branch_ids,
                branch_index=branch_index,
                product_index=product_index,
            )
            for entry in report.entries:
                print(
                    f"   {entry.event_id}: deficit={entry.deficit}, anchored={entry.anchored}",
                    file=sys.stderr,
                )
            forced_specs = report.forced_specs

        # Rebuild dim indexes after anchor inserts so engine sees anchor customers.
        with get_cursor(config) as cursor:
            dim_indexes = build_dim_indexes(cursor)

    # Generate facts
    txn_gen = TransactionGenerator(seed=seed, events=events)
    fact_counts = seed_facts(
        config,
        txn_gen,
        customers=customers,
        products=products,
        accounts=accounts,
        transaction_rows=rows,
        events=events,
        forced_specs=forced_specs if with_events else None,
        dim_indexes=dim_indexes,
    )

    # Apply readonly grants every reseed so the NL2SQL agent can read fresh data
    # even if the original init script never took effect on the PG volume.
    ensure_readonly_grants(config)

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
