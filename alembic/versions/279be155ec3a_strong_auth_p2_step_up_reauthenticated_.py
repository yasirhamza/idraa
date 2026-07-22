"""strong auth p2 step-up reauthenticated_at

Revision ID: 279be155ec3a
Revises: 2fa98364de58
Create Date: 2026-07-22 22:59:03.007396
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "279be155ec3a"
down_revision = "2fa98364de58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no server default, no backfill: None reads as "stale" so
    # pre-P2 sessions simply re-verify once (fail-closed by design).
    op.add_column(
        "auth_sessions",
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_sessions", "reauthenticated_at")
