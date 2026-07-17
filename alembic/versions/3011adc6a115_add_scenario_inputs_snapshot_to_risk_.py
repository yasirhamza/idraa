"""add_scenario_inputs_snapshot_to_risk_analysis_runs

Revision ID: 3011adc6a115
Revises: 60ff242180f6
Create Date: 2026-06-11 16:30:41.384459

Additive migration: adds ``scenario_inputs_snapshot`` JSON column to
``risk_analysis_runs``. server_default=NULL.

T2 (#351 enterprise-pdf-reports): the executor populates this column AT
EXECUTION TIME from the scenario objects it actually loads, written BEFORE
the engine call so that the stored snapshot reflects the as-executed FAIR
distribution parameters (TEF, Vulnerability, PL, SL). Runs predating this
column carry NULL; the report builder falls back to live scenario values
with the honest label "Current scenario values (run predates input
snapshots — values may differ from as-executed)".

down_revision confirmed against `uv run alembic heads` output = '60ff242180f6'
(single head, 2026-06-11).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3011adc6a115"
down_revision: Union[str, Sequence[str], None] = "60ff242180f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add scenario_inputs_snapshot JSON column (server_default=NULL)."""
    op.add_column(
        "risk_analysis_runs",
        sa.Column(
            "scenario_inputs_snapshot",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop scenario_inputs_snapshot column."""
    op.drop_column("risk_analysis_runs", "scenario_inputs_snapshot")
