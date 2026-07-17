"""add scenarios.effect (FAIR taxonomy 4th axis: C/I/A) — indirect-attribution Slice 1

Nullable additive column. NULL = effect unspecified → the recovery gate stays
detection-gated for that scenario (no behaviour change for legacy rows). No seed
rows inserted, so the no-hyphen-uuid convention does not apply here.

Downgrade: drop_column (additive column, data loss intentional).

Revision ID: d054442ed13b
Down revision: a3f7c1e9b2d4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d054442ed13b"
down_revision = "a3f7c1e9b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scenarios",
        sa.Column("effect", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scenarios", "effect")
