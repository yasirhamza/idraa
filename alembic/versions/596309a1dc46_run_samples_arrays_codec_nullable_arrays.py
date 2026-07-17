"""run_samples arrays_codec + nullable arrays

Revision ID: 596309a1dc46
Revises: d3f1a7c9e5b2
Create Date: 2026-07-06 17:04:52.346475

Adds a nullable ``arrays_codec`` BLOB column (compressed binary MC arrays,
services/sample_codec.py — the preferred store going forward) and makes the
legacy ``arrays`` JSON column nullable so future writers can skip it once
callers migrate to the codec.

SQLite has no native ALTER COLUMN, so making ``arrays`` nullable forces a
batch (copy-and-swap) table recreate. ``run_samples`` carries two
load-bearing, UNNAMED foreign keys (SQLite's own reflection assigns them no
constraint name, so they cannot be targeted by name in ``drop_constraint``/
``create_foreign_key``):

  - ``run_id -> risk_analysis_runs.id`` ON DELETE CASCADE (retention #297)
  - ``organization_id -> organizations.id`` ON DELETE RESTRICT (OrgMixin)

plus the ``ix_run_samples_organization_id`` index. Alembic's batch-recreate
is known to silently drop ``ondelete`` on reflected FKs in some versions, so
this was NOT assumed — it was verified empirically against this exact table
(``sa.inspect(engine).get_foreign_keys("run_samples")`` before/after, and
``PRAGMA foreign_key_list`` / ``.schema run_samples`` post-migration). Both
``ondelete`` actions and the index survive the plain
``batch_alter_table(recreate="always")`` recreate unmodified here, so no
explicit constraint re-declaration was needed. See
tests/migrations/test_run_samples_codec_migration.py for the pinned proof
(DB-level cascade-delete test), which is the regression guard if a future
SQLAlchemy/Alembic version regresses this.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "596309a1dc46"
down_revision = "d3f1a7c9e5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table(
        "run_samples",
        recreate="always",
    ) as batch:
        batch.add_column(sa.Column("arrays_codec", sa.LargeBinary(), nullable=True))
        batch.alter_column(
            "arrays",
            existing_type=sa.JSON(),
            nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema.

    ONE-WAY IN PRACTICE: once any row has arrays_codec populated and arrays
    IS NULL, restoring arrays to NOT NULL will fail (existing NULL rows
    violate the constraint). This downgrade path is best-effort for local
    dev only, not a production rollback guarantee.
    """
    with op.batch_alter_table(
        "run_samples",
        recreate="always",
    ) as batch:
        batch.alter_column(
            "arrays",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch.drop_column("arrays_codec")
