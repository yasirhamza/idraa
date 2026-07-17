"""issue_89_aggregate_control_ids_per_scenario

Revision ID: 2b8317b19290
Revises: f8b3c19d4a02
Create Date: 2026-05-11 20:59:19.169388

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b8317b19290'
down_revision: Union[str, Sequence[str], None] = 'f8b3c19d4a02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Issue #89: add aggregate_control_ids_per_scenario JSON column.

    NULL for SINGLE runs and legacy (pre-issue-89) AGGREGATE runs; dict
    keyed by scenario_id (str) with list[str(control_id)] values for new
    AGGREGATE runs. fair_cam.calculate_aggregate_enhanced_risk consumes
    this via per_scenario_active_control_ids param.

    Legacy AGGREGATE rows: NULL passes through fair_cam's back-compat
    path (unified active_control_ids applied to all scenarios). Run-detail
    rendering shows a "legacy union semantics" banner for these.
    """
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.add_column(
            sa.Column("aggregate_control_ids_per_scenario", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    """Reverse: drop the column."""
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.drop_column("aggregate_control_ids_per_scenario")
