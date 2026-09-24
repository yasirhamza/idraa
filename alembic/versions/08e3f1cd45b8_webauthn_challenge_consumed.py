"""webauthn challenge consumed

Revision ID: 08e3f1cd45b8
Revises: b5e2c7a9d413
Create Date: 2026-09-24 14:00:38.558210

Creates ``webauthn_challenge_consumed`` — the single-use claim table for
advisory GHSA-46jj-823j-mjj9 B5 (a captured (assertion, challenge cookie)
pair could otherwise replay within the challenge TTL for login, step-up, and
registration). Auth-layer table, no ``organization_id`` (like
``login_attempt``). UNIQUE on ``challenge_digest`` alone (login and
registration share one signed cookie/salt, so digest-only uniqueness also
blocks cross-purpose reuse); ``purpose`` is forensic-only. Indexed on
``expires_at`` for the retention sweep
(``services/run_reaper.py::sweep_expired_webauthn_challenges``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "08e3f1cd45b8"
down_revision: str | Sequence[str] | None = "b5e2c7a9d413"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "webauthn_challenge_consumed",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("challenge_digest", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replay_audited", sa.Boolean(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_digest"),
    )
    op.create_index(
        op.f("ix_webauthn_challenge_consumed_expires_at"),
        "webauthn_challenge_consumed",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_webauthn_challenge_consumed_expires_at"),
        table_name="webauthn_challenge_consumed",
    )
    op.drop_table("webauthn_challenge_consumed")
