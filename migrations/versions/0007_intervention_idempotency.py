"""durable intervention idempotency

Revision ID: 0007_intervention_idempotency
Revises: 0006_durable_monitor
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0007_intervention_idempotency"
down_revision: Union[str, None] = "0006_durable_monitor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS keeps this migration compatible with prototype databases
    # that received the column from the former startup-time schema patch.
    op.execute(
        "ALTER TABLE interventions ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)"
    )
    op.execute(
        "UPDATE interventions SET idempotency_key = "
        "opportunity_id::text || ':' || action || ':legacy:' || id::text "
        "WHERE idempotency_key IS NULL"
    )
    op.alter_column("interventions", "idempotency_key", nullable=False)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_interventions_idempotency_key "
        "ON interventions (idempotency_key)"
    )


def downgrade() -> None:
    op.drop_index("uq_interventions_idempotency_key", table_name="interventions")
    op.drop_column("interventions", "idempotency_key")
