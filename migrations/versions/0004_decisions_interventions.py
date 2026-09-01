"""decision audit + interventions tables

Revision ID: 0004_decisions_interventions
Revises: 0003_domain_tables
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004_decisions_interventions"
down_revision: Union[str, None] = "0003_domain_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "interventions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="executed"),
        sa.Column("razorpay_reference", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_interventions_opportunity_id", "interventions", ["opportunity_id"])
    op.create_index("ix_interventions_razorpay_reference", "interventions", ["razorpay_reference"])

    op.create_table(
        "decision_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=60), nullable=False),
        sa.Column("diagnosis", postgresql.JSONB(), nullable=True),
        sa.Column("model_version", sa.String(length=40), nullable=True),
        sa.Column("predictions", postgresql.JSONB(), nullable=True),
        sa.Column("recommended_action", sa.String(length=40), nullable=True),
        sa.Column("expected_recovery_minor", sa.BigInteger(), nullable=True),
        sa.Column("policy_decision", postgresql.JSONB(), nullable=True),
        sa.Column("executed_action", sa.String(length=40), nullable=True),
        sa.Column("execution_result", postgresql.JSONB(), nullable=True),
        sa.Column("verified_outcome", sa.String(length=40), nullable=True),
        sa.Column("recovered_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_decision_audit_opportunity_id", "decision_audit", ["opportunity_id"])


def downgrade() -> None:
    op.drop_index("ix_decision_audit_opportunity_id", table_name="decision_audit")
    op.drop_table("decision_audit")
    op.drop_index("ix_interventions_razorpay_reference", table_name="interventions")
    op.drop_index("ix_interventions_opportunity_id", table_name="interventions")
    op.drop_table("interventions")
