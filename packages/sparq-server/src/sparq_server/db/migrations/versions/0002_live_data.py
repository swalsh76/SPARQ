"""The live feed: ticker (a hypertable), market events, exchange status, rejects, the gap columns.

Additive only, so a machine on 0001 keeps working while this one is applied. ``ticker`` is a
hypertable compressed by market after a day, like ``trade``; it has no unique key because a ticker
frame is a fact about a moment and two can be identical. ``market_lifecycle`` is the history of what
Kalshi said about a market (its rules, close time, result), with the time we saw it. Written out in
full on purpose: a migration is a frozen record of what was run, and must not follow ``schema.py``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TZ = sa.TIMESTAMP(timezone=True)
TICKER_BIGINTS = [
    "price_e6",
    "yes_bid_e6",
    "yes_ask_e6",
    "yes_bid_size_e2",
    "yes_ask_size_e2",
    "volume_e2",
    "open_interest_e2",
    "last_trade_size_e2",
    "dollar_volume_e6",
    "dollar_open_interest_e6",
]


def upgrade() -> None:
    op.add_column("market", sa.Column("venue", sa.Text, nullable=False, server_default="kalshi"))

    op.create_table(
        "ticker",
        sa.Column("ts", TZ, nullable=False),
        sa.Column("received_at", TZ, nullable=False),
        sa.Column("ticker", sa.Text, nullable=False),
        *[sa.Column(name, sa.BigInteger) for name in TICKER_BIGINTS],
        sa.CheckConstraint("price_e6 BETWEEN 0 AND 1000000", name="ticker_price"),
        sa.CheckConstraint("yes_bid_e6 BETWEEN 0 AND 1000000", name="ticker_yes_bid"),
        sa.CheckConstraint("yes_ask_e6 BETWEEN 0 AND 1000000", name="ticker_yes_ask"),
        sa.CheckConstraint(
            "coalesce(yes_bid_size_e2, 0) >= 0 AND coalesce(yes_ask_size_e2, 0) >= 0 "
            "AND coalesce(volume_e2, 0) >= 0 AND coalesce(open_interest_e2, 0) >= 0 "
            "AND coalesce(last_trade_size_e2, 0) >= 0",
            name="ticker_sizes",
        ),
    )
    op.execute(
        "SELECT create_hypertable('ticker', 'ts', chunk_time_interval => INTERVAL '1 day', "
        "create_default_indexes => false)"
    )
    op.create_index("ticker_ticker_ts", "ticker", ["ticker", sa.text("ts DESC")])
    op.execute(
        "ALTER TABLE ticker SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'ticker', "
        "timescaledb.compress_orderby = 'ts DESC')"
    )
    op.execute("SELECT add_compression_policy('ticker', INTERVAL '1 day')")

    op.create_table(
        "market_lifecycle",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("ticker", sa.Text, nullable=False),
        sa.Column("observed_at", TZ, nullable=False),
        sa.Column("sent_at", TZ),
        sa.Column("source", sa.Text, nullable=False, server_default="stream"),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("open_time", TZ),
        sa.Column("close_time", TZ),
        sa.Column("determination_time", TZ),
        sa.Column("settled_time", TZ),
        sa.Column("result", sa.Text),
        sa.Column("settlement_value_e6", sa.BigInteger),
        sa.Column("is_deactivated", sa.Boolean),
        sa.Column("exchange_index", sa.Integer),
        sa.Column("price_level_structure", sa.Text),
        sa.Column("details", postgresql.JSONB),
        sa.CheckConstraint("event_type <> ''", name="market_lifecycle_type"),
    )
    op.create_index("market_lifecycle_ticker", "market_lifecycle", ["ticker", "observed_at"])

    op.create_table(
        "exchange_status",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("observed_at", TZ, nullable=False),
        sa.Column("exchange_index", sa.Integer),
        sa.Column("exchange_active", sa.Boolean, nullable=False),
        sa.Column("trading_active", sa.Boolean, nullable=False),
        sa.Column("raw", postgresql.JSONB),
    )
    op.create_index("exchange_status_at", "exchange_status", ["observed_at"])

    op.create_table(
        "exchange_schedule",
        sa.Column("fetched_at", TZ, primary_key=True),
        sa.Column("schedule", postgresql.JSONB, nullable=False),
    )

    op.create_table(
        "ingest_reject",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB),
    )
    op.create_index("ingest_reject_at", "ingest_reject", ["at"])

    op.add_column("gap_log", sa.Column("reason", sa.Text))
    op.add_column("gap_log", sa.Column("generation", sa.Integer))
    op.add_column("gap_log", sa.Column("status", sa.Text, nullable=False, server_default="open"))
    op.add_column("gap_log", sa.Column("trades_found", sa.Integer))
    op.add_column("gap_log", sa.Column("trades_added", sa.Integer))
    op.add_column("gap_log", sa.Column("trades_duplicate", sa.Integer))
    op.create_check_constraint(
        "gap_status",
        "gap_log",
        "status IN ('open', 'backfilling', 'done', 'failed', 'unrecoverable')",
    )


def downgrade() -> None:
    op.drop_constraint("gap_status", "gap_log", type_="check")
    for column in (
        "trades_duplicate",
        "trades_added",
        "trades_found",
        "status",
        "generation",
        "reason",
    ):
        op.drop_column("gap_log", column)
    for table in (
        "ingest_reject",
        "exchange_schedule",
        "exchange_status",
        "market_lifecycle",
        "ticker",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")  # noqa: S608 (fixed names)
    op.drop_column("market", "venue")
