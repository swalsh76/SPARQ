"""The tables as Python objects: the current shape of the schema.

The migrations are the history and this module is the present: a database test compares the two, so
they cannot drift. The columns follow the downloader's Parquet files (``sparq_analytics.history``),
so K2's import is a copy: same names, and integers stay integers (``int`` for ``int32``, ``bigint``
for ``int64``).
"""

import sqlalchemy as sa
from sparq_core.markets import LABELS, MARKET_INTS, MARKET_TEXT, MARKET_TIMES
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

TIMESTAMPTZ = sa.TIMESTAMP(timezone=True)

series = sa.Table(
    "series",
    metadata,
    sa.Column("ticker", sa.Text, primary_key=True),
    sa.Column("title", sa.Text),
    sa.Column("category", sa.Text),
    sa.Column("frequency", sa.Text),
    sa.Column("tags", postgresql.ARRAY(sa.Text)),
    sa.Column("exchange_index", sa.Integer),
    sa.Column("fee_type", sa.Text),
    sa.Column("fee_multiplier_e6", sa.BigInteger),
    sa.Column("fetched_at", TIMESTAMPTZ),
)

fee_change = sa.Table(
    "fee_change",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("series_ticker", sa.Text, nullable=False),
    sa.Column("fee_type", sa.Text),
    sa.Column("fee_multiplier_e6", sa.BigInteger),
    sa.Column("scheduled_ts", TIMESTAMPTZ),
    sa.Column("fetched_at", TIMESTAMPTZ),
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# the downloader's market file and this table are one row: the columns come from sparq_core
market = sa.Table(
    "market",
    metadata,
    sa.Column("ticker", sa.Text, primary_key=True),
    *[sa.Column(name, sa.Text) for name in MARKET_TEXT if name != "ticker"],
    sa.Column("exchange_index", sa.Integer),
    sa.Column("can_close_early", sa.Boolean),
    *[sa.Column(name, TIMESTAMPTZ) for name in MARKET_TIMES],
    *[sa.Column(name, sa.BigInteger) for name in MARKET_INTS],
    sa.Column("raw_json", postgresql.JSONB),
    sa.Column("venue", sa.Text, nullable=False, server_default="kalshi"),
    sa.CheckConstraint(_one_of("label", LABELS), name="market_label"),
)

trade = sa.Table(
    "trade",
    metadata,
    sa.Column("trade_id", sa.Text, nullable=False),
    sa.Column("ticker", sa.Text, nullable=False),
    sa.Column("created_time", TIMESTAMPTZ, nullable=False),
    sa.Column("yes_price_e6", sa.Integer, nullable=False),
    sa.Column("count_e2", sa.BigInteger, nullable=False),
    sa.Column("taker_side", sa.Text, nullable=False),
    sa.Column("is_block_trade", sa.Boolean, nullable=False, server_default=sa.false()),
    # a hypertable's primary key must include its time column
    sa.PrimaryKeyConstraint("ticker", "trade_id", "created_time", name="trade_pkey"),
    sa.CheckConstraint("yes_price_e6 BETWEEN 0 AND 1000000", name="trade_price"),
    sa.CheckConstraint("count_e2 >= 0", name="trade_count"),
    sa.CheckConstraint("taker_side IN ('yes', 'no')", name="trade_side"),
)

gap_log = sa.Table(
    "gap_log",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("started_at", TIMESTAMPTZ, nullable=False),
    sa.Column("ended_at", TIMESTAMPTZ),
    sa.Column("note", sa.Text),
    sa.Column("reason", sa.Text),
    sa.Column("generation", sa.Integer),
    sa.Column("status", sa.Text, nullable=False, server_default="open"),
    sa.Column("trades_found", sa.Integer),
    sa.Column("trades_added", sa.Integer),
    sa.Column("trades_duplicate", sa.Integer),
    sa.CheckConstraint(
        "kind IN ('ticker', 'orderbook', 'trades', 'lifecycle', 'all')", name="gap_kind"
    ),
    sa.CheckConstraint(
        "status IN ('open', 'backfilling', 'done', 'failed', 'unrecoverable')", name="gap_status"
    ),
)

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

ticker = sa.Table(
    "ticker",
    metadata,
    sa.Column("ts", TIMESTAMPTZ, nullable=False),
    sa.Column("received_at", TIMESTAMPTZ, nullable=False),
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

market_lifecycle = sa.Table(
    "market_lifecycle",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("ticker", sa.Text, nullable=False),
    sa.Column("observed_at", TIMESTAMPTZ, nullable=False),
    sa.Column("sent_at", TIMESTAMPTZ),
    sa.Column("source", sa.Text, nullable=False, server_default="stream"),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("open_time", TIMESTAMPTZ),
    sa.Column("close_time", TIMESTAMPTZ),
    sa.Column("determination_time", TIMESTAMPTZ),
    sa.Column("settled_time", TIMESTAMPTZ),
    sa.Column("result", sa.Text),
    sa.Column("settlement_value_e6", sa.BigInteger),
    sa.Column("is_deactivated", sa.Boolean),
    sa.Column("exchange_index", sa.Integer),
    sa.Column("price_level_structure", sa.Text),
    sa.Column("details", postgresql.JSONB),
    sa.CheckConstraint("event_type <> ''", name="market_lifecycle_type"),
)

exchange_status = sa.Table(
    "exchange_status",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("observed_at", TIMESTAMPTZ, nullable=False),
    sa.Column("exchange_index", sa.Integer),
    sa.Column("exchange_active", sa.Boolean, nullable=False),
    sa.Column("trading_active", sa.Boolean, nullable=False),
    sa.Column("raw", postgresql.JSONB),
)

exchange_schedule = sa.Table(
    "exchange_schedule",
    metadata,
    sa.Column("fetched_at", TIMESTAMPTZ, primary_key=True),
    sa.Column("schedule", postgresql.JSONB, nullable=False),
)

ingest_reject = sa.Table(
    "ingest_reject",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("payload", postgresql.JSONB),
)

ingest_state = sa.Table(
    "ingest_state",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
)
