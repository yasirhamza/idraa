"""add library_entry loss_tier epistemic-status column

Adds ``scenario_library_entries.loss_tier`` ('paginated' | 'vendor' |
'anecdotal' | 'none') — the epistemic tier of the entry's loss-magnitude anchor
(Epic C-i #335 §6). ``native_enum=False`` with NO ``create_constraint`` mirrors
``scenario_library_entries.source`` (c5a2f17b9e34) — no CHECK is emitted,
avoiding the #303 CHECK-widening foot-gun; the value set is app-enforced.
``server_default='anecdotal'`` backfills the existing migration-seeded entries
(all currently PERT) on upgrade.

Revision ID: d6b8e2f0a719
Down revision: c5a2f17b9e34
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6b8e2f0a719"
down_revision = "c5a2f17b9e34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scenario_library_entries",
        sa.Column(
            "loss_tier",
            sa.Enum(
                "paginated",
                "vendor",
                "anecdotal",
                "none",
                native_enum=False,
                name="library_entry_loss_tier",
            ),
            server_default="anecdotal",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("scenario_library_entries", "loss_tier")
