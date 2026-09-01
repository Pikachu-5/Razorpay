"""incidents table

Revision ID: 0005_incidents
Revises: 0004_decisions_interventions
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_incidents"
down_revision: Union[str, None] = "0004_decisions_interventions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="detected"),
        sa.Column("method", sa.String(length=30), nullable=True),
        sa.Column("bank", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("diagnosis", postgresql.JSONB(), nullable=True),
        sa.Column("revenue_at_risk_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("affected_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("interventions_executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("intervention_budget", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="detector"),
        sa.Column("detection_stats", postgresql.JSONB(), nullable=True),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "recovered_during_incident_minor",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index("ix_incidents_status", "incidents", ["status"])


def downgrade() -> None:
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_table("incidents")
