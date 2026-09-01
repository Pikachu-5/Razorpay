"""real data provenance and Razorpay lifecycle state

Revision ID: 0008_realism_foundation
Revises: 0007_intervention_idempotency
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008_realism_foundation"
down_revision: Union[str, None] = "0007_intervention_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"))
    op.add_column("payments", sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("payments", sa.Column("simulation_run_id", sa.String(40)))
    op.create_index("ix_payments_source", "payments", ["source"])
    op.create_index("ix_payments_is_synthetic", "payments", ["is_synthetic"])
    op.create_index("ix_payments_simulation_run_id", "payments", ["simulation_run_id"])

    op.add_column("opportunities", sa.Column("assignment_key", sa.String(320)))
    op.add_column("opportunities", sa.Column("assignment_probability", sa.Float(), nullable=False, server_default="0.8"))
    op.add_column("opportunities", sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"))
    op.add_column("opportunities", sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("opportunities", sa.Column("simulation_run_id", sa.String(40)))
    for name in ("assignment_key", "source", "is_synthetic", "simulation_run_id"):
        op.create_index(f"ix_opportunities_{name}", "opportunities", [name])

    op.create_table("razorpay_orders",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("amount_paid_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("amount_due_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("receipt", sa.String(64)),
        sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("payload", postgresql.JSONB()),
        sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_razorpay_orders_status", "razorpay_orders", ["status"])
    op.create_index("ix_razorpay_orders_source", "razorpay_orders", ["source"])
    op.create_index("ix_razorpay_orders_is_synthetic", "razorpay_orders", ["is_synthetic"])

    op.create_table("payment_link_states",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("opportunities.id")),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("amount_paid_minor", sa.BigInteger(), nullable=False, server_default="0"), sa.Column("reference_id", sa.String(64)),
        sa.Column("short_url", sa.Text()), sa.Column("reminder_status", sa.String(24)),
        sa.Column("expire_by", postgresql.TIMESTAMP(timezone=True)), sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"),
        sa.Column("payload", postgresql.JSONB()), sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()))
    for name in ("opportunity_id", "status", "reference_id", "source"):
        op.create_index(f"ix_payment_link_states_{name}", "payment_link_states", [name])

    op.create_table("payment_downtimes",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("method", sa.String(30)), sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("instrument", postgresql.JSONB()),
        sa.Column("begin_at", postgresql.TIMESTAMP(timezone=True)), sa.Column("end_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"), sa.Column("payload", postgresql.JSONB()),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_payment_downtimes_status", "payment_downtimes", ["status"])
    op.create_index("ix_payment_downtimes_method", "payment_downtimes", ["method"])

    op.create_table("subscription_states",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("plan_id", sa.String(64)), sa.Column("customer_id", sa.String(64)),
        sa.Column("paid_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("remaining_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_start", postgresql.TIMESTAMP(timezone=True)), sa.Column("current_end", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"), sa.Column("payload", postgresql.JSONB()),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_subscription_states_status", "subscription_states", ["status"])
    op.create_index("ix_subscription_states_source", "subscription_states", ["source"])

    op.create_table("revenue_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("external_id", sa.String(64), nullable=False, unique=True),
        sa.Column("razorpay_payment_id", sa.String(64)), sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"), sa.Column("payload", postgresql.JSONB()),
        sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()))
    for name in ("external_id", "razorpay_payment_id", "kind", "status", "source"):
        op.create_index(f"ix_revenue_adjustments_{name}", "revenue_adjustments", [name], unique=(name == "external_id"))


def downgrade() -> None:
    op.drop_table("revenue_adjustments")
    op.drop_table("subscription_states")
    op.drop_table("payment_downtimes")
    op.drop_table("payment_link_states")
    op.drop_table("razorpay_orders")
    for name in ("simulation_run_id", "is_synthetic", "source", "assignment_key"):
        op.drop_index(f"ix_opportunities_{name}", table_name="opportunities")
    for name in ("simulation_run_id", "is_synthetic", "source", "assignment_probability", "assignment_key"):
        op.drop_column("opportunities", name)
    for name in ("simulation_run_id", "is_synthetic", "source"):
        op.drop_index(f"ix_payments_{name}", table_name="payments")
        op.drop_column("payments", name)
