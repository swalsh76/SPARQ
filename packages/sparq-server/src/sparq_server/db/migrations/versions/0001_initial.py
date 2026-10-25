"""The first schema: series, fee changes, markets, trades (a hypertable) and the gap log.

Shaped like the downloader's Parquet files so K2's import is a copy. Written out in full on
purpose: a migration is a frozen record of what was run, and must not follow ``schema.py``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.TIMESTAMP(timezone=True)
TEXT_COLUMNS = [
    "event_ticker",
    "series_ticker",
    "source",
    "label",
    "status",
    "result",
    "market_type",
    "title",
    "yes_sub_title",
    "no_sub_title",
    "rules_primary",
    "rules_secondary",
    "early_close_condition",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "price_level_structure",
]
TIME_COLUMNS = [
    "created_time",
    "open_time",
    "close_time",
    "expected_expiration_time",
    "expiration_time",
    "latest_expiration_time",
    "occurrence_datetime",
    "settlement_ts",
    "updated_time",
]
BIGINT_COLUMNS = [
    "settlement_value_e6",
    "last_price_e6",
    "previous_price_e6",
    "yes_bid_e6",
    "yes_ask_e6",
    "no_bid_e6",
    "no_ask_e6",
    "yes_bid_size_e2",
    "yes_ask_size_e2",
    "volume_e2",
    "volume_24h_e2",
    "open_interest_e2",
    "notional_value_e6",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "series",
        sa.Column("ticker", sa.Text, primary_key=True),
        sa.Column("title", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("frequency", sa.Text),
        sa.Column("tags", postgresql.ARRAY(sa.Text)),
        sa.Column("exchange_index", sa.Integer),
        sa.Column("fee_type", sa.Text),
        sa.Column("fee_multiplier_e6", sa.BigInteger),
        sa.Column("fetched_at", TZ),
    )

    op.create_table(
        "fee_change",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("series_ticker", sa.Text, nullable=False),
        sa.Column("fee_type", sa.Text),
        sa.Column("fee_multiplier_e6", sa.BigInteger),
        sa.Column("scheduled_ts", TZ),
        sa.Column("fetched_at", TZ),
    )
    op.create_index("fee_change_series", "fee_change", ["series_ticker", "scheduled_ts"])

    op.create_table(
        "market",
        sa.Column("ticker", sa.Text, primary_key=True),
        *[sa.Column(name, sa.Text) for name in TEXT_COLUMNS],
        sa.Column("exchange_index", sa.Integer),
        sa.Column("can_close_early", sa.Boolean),
        *[sa.Column(name, TZ) for name in TIME_COLUMNS],
        *[sa.Column(name, sa.BigInteger) for name in BIGINT_COLUMNS],
        sa.Column("raw_json", postgresql.JSONB),
        sa.CheckConstraint(
            "label IN ('yes', 'no', 'scalar', 'other_settled', 'unsettled')", name="market_label"
        ),
    )
    op.create_index("market_event", "market", ["event_ticker"])
    op.create_index("market_series", "market", ["series_ticker", "close_time"])

    op.create_table(
        "trade",
        sa.Column("trade_id", sa.Text, nullable=False),
        sa.Column("ticker", sa.Text, nullable=False),
        sa.Column("created_time", TZ, nullable=False),
        sa.Column("yes_price_e6", sa.Integer, nullable=False),
        sa.Column("count_e2", sa.BigInteger, nullable=False),
        sa.Column("taker_side", sa.Text, nullable=False),
        sa.Column("is_block_trade", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("ticker", "trade_id", "created_time", name="trade_pkey"),
        sa.CheckConstraint("yes_price_e6 BETWEEN 0 AND 1000000", name="trade_price"),
        sa.CheckConstraint("count_e2 >= 0", name="trade_count"),
        sa.CheckConstraint("taker_side IN ('yes', 'no')", name="trade_side"),
    )
    op.execute(
        "SELECT create_hypertable('trade', 'created_time', chunk_time_interval => INTERVAL '1 day')"
    )
    op.execute(
        "ALTER TABLE trade SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'ticker', "
        "timescaledb.compress_orderby = 'created_time DESC')"
    )
    op.execute("SELECT add_compression_policy('trade', INTERVAL '2 days')")

    op.create_table(
        "gap_log",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("ended_at", TZ),
        sa.Column("note", sa.Text),
        sa.CheckConstraint("kind IN ('ticker', 'orderbook', 'trades', 'all')", name="gap_kind"),
    )


def downgrade() -> None:
    # dropping a hypertable also removes its chunks and its compression policy
    for table in ("gap_log", "trade", "market", "fee_change", "series"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")  # noqa: S608 (fixed names)
