"""durable monitor queue and replica-safe event stream

Revision ID: 0006_durable_monitor
Revises: 0005_incidents
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_durable_monitor"
down_revision: Union[str, None] = "0005_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("raw_events", sa.Column("claimed_at", postgresql.TIMESTAMP(timezone=True)))
    op.add_column("raw_events", sa.Column("processed_at", postgresql.TIMESTAMP(timezone=True)))
    op.add_column("raw_events", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("raw_events", sa.Column("last_error", sa.Text()))
    op.create_index("ix_raw_events_unprocessed", "raw_events", ["received_at"], postgresql_where=sa.text("processed_at IS NULL"))
    op.create_table(
        "durable_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_durable_events_id", "durable_events", ["id"])
    op.create_index("ix_durable_events_kind", "durable_events", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_durable_events_kind", table_name="durable_events")
    op.drop_index("ix_durable_events_id", table_name="durable_events")
    op.drop_table("durable_events")
    op.drop_index("ix_raw_events_unprocessed", table_name="raw_events")
    op.drop_column("raw_events", "last_error")
    op.drop_column("raw_events", "attempts")
    op.drop_column("raw_events", "processed_at")
    op.drop_column("raw_events", "claimed_at")
