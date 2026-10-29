"""org-wide meta controls (#299)

Adds ``controls.org_wide_meta`` (NOT NULL DEFAULT 0 — every existing control
stays scenario-scoped, so no result changes) and
``risk_analysis_runs.inherited_control_ids_per_scenario`` (JSON NULL — NULL
marks runs that predate the feature). Upgrade writes no data. Downgrade flags
feature-era COMPLETED runs stale before dropping the column (spec R1): the
reverted workbook code would recompute them without the meta uplift.

Revision ID: a7c2e9f14b63
Revises: 3e0c10ecedb3
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "a7c2e9f14b63"
down_revision = "3e0c10ecedb3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("controls") as batch_op:
        batch_op.add_column(
            sa.Column("org_wide_meta", sa.Boolean(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.add_column(
            sa.Column("inherited_control_ids_per_scenario", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    # Spec R1: feature-era runs must be re-run after rollback; say so via is_stale.
    # Bound parameter, not a literal 1: portable across SQLite and Postgres
    # (precedent: alembic/versions/c4d2e8f1a6b9_issue_199_c8_stale_runs.py:166, SC-N9).
    op.get_bind().execute(
        sa.text(
            "UPDATE risk_analysis_runs SET is_stale = :t "
            "WHERE inherited_control_ids_per_scenario IS NOT NULL "
            "AND status = 'completed'"
        ),
        {"t": True},
    )
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.drop_column("inherited_control_ids_per_scenario")
    with op.batch_alter_table("controls") as batch_op:
        batch_op.drop_column("org_wide_meta")
