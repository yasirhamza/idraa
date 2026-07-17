"""widen_audit_log_action_to_64

Revision ID: 08358cf073b8
Revises: 1297897c44f5
Create Date: 2026-05-17 11:07:06.593060

Issue #129 T6 -- pre-existing String(32) column already exceeded by
multiple action strings in production code (e.g.,
``control_function_assignment.update`` at 34 chars). SQLite silently
accepts the overflow; Postgres would reject. Widen to String(64) before
T6 introduces ``control_function_assignment.clear`` (33 chars).

Sec-I4 plan-gate-round-1 fix: dialect-aware upgrade. Postgres native
``ALTER COLUMN TYPE`` is metadata-only and instant;
``batch_alter_table`` rewrites the table and acquires
AccessExclusiveLock for the duration on populated production
``audit_log``. SQLite uses batch (no in-place ALTER).

Arch-I1 round-1 + Arch-2 round-2: downgrade includes defensive guard
that raises if any action > 32 chars, refusing silent truncation.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "08358cf073b8"
down_revision: Union[str, Sequence[str], None] = "1297897c44f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen audit_log.action from String(32) to String(64)."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Postgres: native ALTER COLUMN TYPE is metadata-only / instant
        # (no table rewrite), avoiding the AccessExclusiveLock that
        # batch_alter_table would hold on a populated audit_log.
        op.execute("ALTER TABLE audit_log ALTER COLUMN action TYPE VARCHAR(64)")
    else:
        # SQLite (and other dialects without native in-place ALTER):
        # batch_alter_table rebuilds the table.
        with op.batch_alter_table("audit_log") as batch_op:
            batch_op.alter_column(
                "action",
                existing_type=sa.String(32),
                type_=sa.String(64),
                existing_nullable=False,
            )


def downgrade() -> None:
    """Conditionally unsafe: refuses to downgrade if any row has
    ``action`` > 32 chars (would silently truncate).

    Operator must manually shorten or remove the offending rows first
    (Arch-I1 round-1 + Arch-2 round-2 plan-gate fix).
    """
    bind = op.get_bind()
    max_len_row = bind.execute(
        sa.text("SELECT COALESCE(MAX(LENGTH(action)), 0) FROM audit_log")
    ).scalar_one()
    if max_len_row > 32:
        raise RuntimeError(
            f"audit_log.action max length is {max_len_row} chars; refusing "
            f"to downgrade to String(32) -- would silently truncate. "
            f"Manually shorten or remove rows with action > 32 chars first."
        )

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE audit_log ALTER COLUMN action TYPE VARCHAR(32)")
    else:
        with op.batch_alter_table("audit_log") as batch_op:
            batch_op.alter_column(
                "action",
                existing_type=sa.String(64),
                type_=sa.String(32),
                existing_nullable=False,
            )
