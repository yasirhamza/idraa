"""add_calibration_anchor_to_library_entries

Issue #103 PR γ-2 F1.

Adds a nullable JSON column ``calibration_anchor`` to
``scenario_library_entries`` to support per-entry org-context calibration
of PL/SL distributions at wizard pre-fill time. Shape: ``{"industry":
"<slug>", "revenue_tier": "<slug>"}``. Nullable during PR γ-2 rollout
(legacy-path tolerated); flipped to NOT NULL in PR γ-4 after the 31 seed
entries are re-curated by PRs γ-3a/b/c.

Spec: internal design doc 2026-05-13-scenario-library-org-context-calibration-design §5.1, §8.1.

Revision ID: 2bb61838bc75
Revises: 992ff8c97e05
Create Date: 2026-05-13 18:59:49.632576
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2bb61838bc75"
down_revision = "992ff8c97e05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive ADD COLUMN — no table rebuild needed on SQLite for nullable
    # columns. (Other migrations in this repo that use batch_alter_table do
    # so for non-additive operations like enum tightening or constraint adds.)
    op.add_column(
        "scenario_library_entries",
        sa.Column("calibration_anchor", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scenario_library_entries", "calibration_anchor")
