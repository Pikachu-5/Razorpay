"""decision-time feature evidence

Revision ID: 0009_evidence_controls
Revises: 0008_realism_foundation
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_evidence_controls"
down_revision: Union[str, None] = "0008_realism_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("decision_audit", sa.Column("feature_snapshot", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("decision_audit", "feature_snapshot")
