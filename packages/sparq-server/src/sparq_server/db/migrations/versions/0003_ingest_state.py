"""Where the ingest remembers things across a restart: discovery bookmarks, the feed's heartbeat.

Also adds the gap kind ``lifecycle``, for messages the stream missed (discovery recovers them).

One small table of text values by key. The heartbeat is the time of the last frame the feed
delivered, written every few seconds, so that after a restart the gap is known exactly instead of
guessed from the newest stored trade. Written out in full on purpose: a migration is a frozen
record of what was run, and must not follow ``schema.py``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


GAP_KINDS = "kind IN ('ticker', 'orderbook', 'trades', 'lifecycle', 'all')"
OLD_GAP_KINDS = "kind IN ('ticker', 'orderbook', 'trades', 'all')"


def upgrade() -> None:
    # a missed lifecycle message is recovered by discovery, so it gets a kind of its own
    op.drop_constraint("gap_kind", "gap_log", type_="check")
    op.create_check_constraint("gap_kind", "gap_log", GAP_KINDS)
    op.create_table(
        "ingest_state",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingest_state")
    op.execute("DELETE FROM gap_log WHERE kind = 'lifecycle'")
    op.drop_constraint("gap_kind", "gap_log", type_="check")
    op.create_check_constraint("gap_kind", "gap_log", OLD_GAP_KINDS)
