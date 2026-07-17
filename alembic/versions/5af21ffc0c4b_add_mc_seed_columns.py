"""add mc seed columns

Phase 1 MC-seed reproducibility: add nullable seed columns to the two run
tables so future runs can record what RNG seed was used.

- ``risk_analysis_runs.random_seed`` (Integer, nullable) — the user-supplied
  base seed for the RNG.  NULL for old runs that pre-date seed tracking.
- ``run_samples.derived_seed_keys`` (JSON, nullable) — map of
  {scenario_id: spawn_index} used to derive per-scenario child seeds from
  ``random_seed``.  NULL for old run_samples rows that pre-date seed tracking.

Both columns are nullable so existing rows remain valid without any backfill.

**Downgrade:** drops both columns.  No data guard needed — the columns are
nullable and carry no data that cannot be regenerated.

Revision ID: 5af21ffc0c4b
Revises: c9f1d3a7b2e0
Create Date: 2026-06-14 14:55:29.276017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5af21ffc0c4b"
down_revision: str = "c9f1d3a7b2e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable random_seed to risk_analysis_runs and derived_seed_keys to run_samples."""
    op.add_column("risk_analysis_runs", sa.Column("random_seed", sa.Integer(), nullable=True))
    op.add_column("run_samples", sa.Column("derived_seed_keys", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop the MC-seed columns added in this revision."""
    op.drop_column("run_samples", "derived_seed_keys")
    op.drop_column("risk_analysis_runs", "random_seed")
