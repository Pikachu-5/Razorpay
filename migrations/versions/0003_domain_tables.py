"""domain tables: customers, payments, opportunities

Revision ID: 0003_domain_tables
Revises: 0002_raw_events
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_domain_tables"
down_revision: Union[str, None] = "0002_raw_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_key", sa.String(length=320), nullable=False, unique=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("contact", sa.String(length=20), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id"),
            nullable=True,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=True),
        sa.Column("bank", sa.String(length=50), nullable=True),
        sa.Column("vpa", sa.String(length=120), nullable=True),
        sa.Column("card_id", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("invoice_id", sa.String(length=64), nullable=True),
        sa.Column("subscription_id", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_description", sa.Text(), nullable=True),
        sa.Column("error_source", sa.String(length=40), nullable=True),
        sa.Column("error_step", sa.String(length=60), nullable=True),
        sa.Column("error_reason", sa.String(length=80), nullable=True),
        sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_payments_razorpay_payment_id", "payments", ["razorpay_payment_id"], unique=True)
    op.create_index("ix_payments_status", "payments", ["status"])

    op.create_table(
        "opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("category", sa.String(length=40), nullable=False, server_default="failed_payment"),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("experiment_group", sa.String(length=12), nullable=False),
        sa.Column("contact_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_action", sa.String(length=40), nullable=True),
        sa.Column("expected_recovery_minor", sa.BigInteger(), nullable=True),
        sa.Column("window_ends_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("closed_reason", sa.Text(), nullable=True),
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
    op.create_index("ix_opportunities_payment_id", "opportunities", ["payment_id"])
    op.create_index("ix_opportunities_status", "opportunities", ["status"])


def downgrade() -> None:
    op.drop_index("ix_opportunities_status", table_name="opportunities")
    op.drop_index("ix_opportunities_payment_id", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_razorpay_payment_id", table_name="payments")
    op.drop_table("payments")
    op.drop_table("customers")
