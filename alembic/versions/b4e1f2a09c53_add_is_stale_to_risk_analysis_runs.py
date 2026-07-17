"""Add is_stale boolean to risk_analysis_runs (#437 T8).

Revision ID: b4e1f2a09c53
Revises: a7c3f9b21e60
Create Date: 2026-06-30

A COMPLETED run is marked ``is_stale=True`` when a library entry it used is
re-curated (version bumped) and the deployed control later re-syncs (#438).
The run stays ``COMPLETED`` and visible to all COMPLETED-gated consumers
(reports, PDF, dashboard).  #438 adds a badge to surface the staleness; this
migration is the schema prerequisite.

Server_default ``"0"`` (SQLite truthy-false) satisfies all existing rows with no
backfill.  Downgrade drops the column (reversible; #438 callers that check
``is_stale`` must be reverted before downgrading).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e1f2a09c53"
down_revision: str | Sequence[str] | None = "a7c3f9b21e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_stale",
                sa.Boolean(),
                nullable=False,
                server_default="0",
                comment=(
                    "re-curation/re-sync (#437/#438) flags a COMPLETED run whose library "
                    "controls changed; run stays COMPLETED + visible"
                ),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.drop_column("is_stale")
