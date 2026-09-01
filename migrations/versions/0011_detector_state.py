"""durable segment detector state

Revision ID: 0011_detector_state
Revises: 0010_invoice_lifecycle
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011_detector_state"
down_revision: Union[str, None] = "0010_invoice_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "detector_states",
        # NULL method/bank are stored as sentinels so they can take part in a
        # composite primary key and in ON CONFLICT.
        sa.Column("method", sa.String(24), primary_key=True),
        sa.Column("bank", sa.String(48), primary_key=True),
        sa.Column("ewma", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cusum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("observations", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_detector_states_updated_at", "detector_states", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_detector_states_updated_at", table_name="detector_states")
    op.drop_table("detector_states")
