"""prune stale wizard drafts (one-time, drafts-surfaced spec §4)

DESTRUCTIVE data migration: deletes wizard_drafts idle > 7 days at upgrade
time. Rationale: 110 invisible drafts accumulated on prod before the
resume UI existed (the TTL sweeper had no caller); without this prune the
new drafts strip debuts as a wall of abandoned test walks. 7 days keeps
anything plausibly wanted. Downgrade is a no-op (rows are gone).

Revision ID: 47c4064a2c1e
Revises: 26444158e537
Create Date: 2026-07-21 16:53:27.272684

"""

from __future__ import annotations

import datetime
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "47c4064a2c1e"
down_revision: Union[str, Sequence[str], None] = "26444158e537"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    # Dialect-neutral bound-param cutoff (DQ-2/DA-6): mirrors the
    # b7d2e8a1c5f3 timestamp-window precedent — NOT SQLite's datetime().
    # UtcDateTime stores "YYYY-MM-DD HH:MM:SS.ffffff" UTC wall-clock
    # (verified at plan-gate), so a same-format string compares correctly.
    cutoff = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
    ).strftime("%Y-%m-%d %H:%M:%S.%f")
    result = op.get_bind().execute(
        sa.text("DELETE FROM wizard_drafts WHERE updated_at < :cutoff"),
        {"cutoff": cutoff},
    )
    logger.warning("pruned %d stale wizard draft(s) (>7 days idle)", result.rowcount)


def downgrade() -> None:
    pass
