"""Control.adopted_snapshot for #438 re-sync diffs.

Revision ID: c9e4f7a2b8d1
Revises: e7d8e05ede6b
Create Date: 2026-07-10

Adds ``controls.adopted_snapshot`` (JSON, nullable): a verbatim copy of the
library-entry values cloned at adopt time, captured by ``adopt_from_library``
from this revision on and never touched by user edits. Enables the clean
3-way re-sync diff (library re-curation vs analyst edit). No backfill is
possible for existing adoptions — the as-adopted state was cloned into the
same mutable columns user edits landed in (see #438 scout findings) — so
legacy rows stay NULL and get the coarse, explicitly-labeled diff.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e4f7a2b8d1"
down_revision: str | Sequence[str] | None = "e7d8e05ede6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("controls", sa.Column("adopted_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("controls", "adopted_snapshot")
