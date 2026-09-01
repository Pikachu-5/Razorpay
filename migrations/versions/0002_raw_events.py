"""raw_events table

Revision ID: 0002_raw_events
Revises: 0001_baseline
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_raw_events"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_uid", sa.String(length=128), nullable=False, unique=True),
        sa.Column("event_type", sa.String(length=100), nullable=True),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("entity_ids", postgresql.JSONB(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="razorpay"),
        sa.Column(
            "received_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_raw_events_event_uid", "raw_events", ["event_uid"], unique=True)
    op.create_index("ix_raw_events_event_type", "raw_events", ["event_type"])
    op.create_index("ix_raw_events_entity_id", "raw_events", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_raw_events_entity_id", table_name="raw_events")
    op.drop_index("ix_raw_events_event_type", table_name="raw_events")
    op.drop_index("ix_raw_events_event_uid", table_name="raw_events")
    op.drop_table("raw_events")
