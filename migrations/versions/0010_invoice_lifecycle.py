"""invoice lifecycle state

Revision ID: 0010_invoice_lifecycle
Revises: 0009_evidence_controls
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010_invoice_lifecycle"
down_revision: Union[str, None] = "0009_evidence_controls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_states",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("subscription_id", sa.String(64)),
        sa.Column("payment_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("amount_paid_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("amount_due_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(24), nullable=False, server_default="razorpay_test"),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for name in ("subscription_id", "payment_id", "status", "source"):
        op.create_index(f"ix_invoice_states_{name}", "invoice_states", [name])


def downgrade() -> None:
    op.drop_table("invoice_states")
