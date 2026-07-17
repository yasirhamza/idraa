"""add library_entry source provenance column

Adds ``scenario_library_entries.source`` ('seed' | 'imported') for provenance:
'seed' = shipped in code / migration-seeded; 'imported' = uploaded at runtime
via P3 bundle import. ``native_enum=False`` with NO ``create_constraint`` mirrors
``scenarios.source`` (1a3794c327d4) — no CHECK is emitted, avoiding the #303
CHECK-widening foot-gun. ``server_default='seed'`` backfills the 44 existing
migration-seeded entries (31 base + 13 extension) on upgrade.

Revision ID: c5a2f17b9e34
Down revision: b3e9c1a47d52
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5a2f17b9e34"
down_revision = "b3e9c1a47d52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scenario_library_entries",
        sa.Column(
            "source",
            sa.Enum("seed", "imported", native_enum=False, name="library_entry_source"),
            server_default="seed",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("scenario_library_entries", "source")
