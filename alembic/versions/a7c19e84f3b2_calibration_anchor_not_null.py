"""calibration_anchor_not_null

Issue #115 — PR γ-4 of #103 series.

Flips ``scenario_library_entries.calibration_anchor`` from nullable to
NOT NULL. Prerequisite: PR γ-3 (commit ``bc56697``) curated all 31 seed
entries — every row already has a non-NULL value.

After this migration the legacy "no-anchor" code path in
``library_calibrated_pre_fill`` is structurally unreachable from seed
data; the ``_validated_anchor`` malformed-data fallback remains as
defense in depth (runtime-malformed dicts via direct DB manipulation),
but is escalated from log.warning to log.error to reflect the new
"this should never happen" semantics.

Revision ID: a7c19e84f3b2
Revises: d4f8a91c2e30
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c19e84f3b2"
down_revision: str | None = "d4f8a91c2e30"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # batch_alter_table for SQLite-portability: SQLite can't ALTER COLUMN
    # NULL → NOT NULL directly; alembic batch mode rebuilds the table.
    with op.batch_alter_table("scenario_library_entries") as batch:
        batch.alter_column(
            "calibration_anchor",
            existing_type=sa.JSON,
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("scenario_library_entries") as batch:
        batch.alter_column(
            "calibration_anchor",
            existing_type=sa.JSON,
            nullable=True,
        )
