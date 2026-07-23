"""strong auth p3 login_attempt

Revision ID: 2c855aa93044
Revises: 279be155ec3a
Create Date: 2026-07-23 12:03:42.410214

Creates ``login_attempt`` — the pre-auth, per-source-key login throttle store
(idraa#81 P3 slice). Keyed on ``source_key`` (e.g. "login:203.0.113.4"),
distinct from the per-user ``users.failed_login_count`` / ``users.locked_until``
columns added in P1 (2fa98364de58): this table throttles unauthenticated
attempts by connection identity (IP/source), not by known-user account.

Also adds ``user_totp.last_used_step`` — the 30s TOTP step counter of the
last accepted code, used to reject same-window replay.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c855aa93044"
down_revision: str | Sequence[str] | None = "279be155ec3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "login_attempt",
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key"),
    )
    op.add_column("user_totp", sa.Column("last_used_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user_totp", "last_used_step")
    op.drop_table("login_attempt")
