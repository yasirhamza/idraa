"""pr_xi_aggregate_runs

Revision ID: 8db26570b7a2
Revises: a1b2c3d4e5f6
Create Date: 2026-05-04 11:46:44.343093

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8db26570b7a2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """SINGLE keeps scenario_id set; AGGREGATE leaves it NULL.

    SQLite-aware: uses batch_alter_table because SQLite doesn't support
    direct ALTER COLUMN nullability changes.
    """
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.alter_column(
            "scenario_id",
            existing_type=sa.Uuid(as_uuid=True),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    """Reverse: re-NOT-NULL the column.

    Pre-condition: any existing AGGREGATE rows (scenario_id IS NULL) must
    be deleted or backfilled before downgrade. The migration aborts loudly
    if it finds any.
    """
    bind = op.get_bind()
    null_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM risk_analysis_runs WHERE scenario_id IS NULL")
    ).scalar()
    if null_count and null_count > 0:
        raise RuntimeError(
            f"Cannot downgrade: {null_count} AGGREGATE rows have scenario_id=NULL. "
            "Delete or backfill them first."
        )
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.alter_column(
            "scenario_id",
            existing_type=sa.Uuid(as_uuid=True),
            existing_nullable=True,
            nullable=False,
        )
