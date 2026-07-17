"""add library_entry loss_form_profile provenance column

Epic D-i (#497 §6). JSON list of per-form provenance dicts; server_default
'[]' backfills existing rows (populated in D-iii). Additive, no CHECK -- same
shape as the loss_tier / source additive columns.

Revision ID: e1f2a3b4c5d6
Down revision: f9330f3b7208
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "f9330f3b7208"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scenario_library_entries",
        sa.Column(
            "loss_form_profile",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("scenario_library_entries", "loss_form_profile")
