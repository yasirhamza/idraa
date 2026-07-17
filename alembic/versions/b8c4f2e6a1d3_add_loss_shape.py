"""Milestone B (#loss-pert-overhaul): add scenario_library_entries.loss_shape.

server_default='capped' covers both existing rows (backfill on upgrade) and
fresh-DB seed inserts (older insert migrations don't name the column — the
'column-omission trap' resolves to the default here, which is correct). The
follow-up UPDATE flips the 10 owner-approved catastrophic shortlist slugs
(spec 2026-07-09 §3). Content conversion of PL/SL is the SEPARATE migration
d9e5a3c7f2b4 — this one is schema + shortlist flip only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c4f2e6a1d3"
down_revision = "c206f115c610"
branch_labels = None
depends_on = None

_CATASTROPHIC_SLUGS = (
    "chemical-process-safety-attack",
    "safety-system-bypass",
    "unauthorized-plc-modification",
    "field-instrument-spoofing",
    "grid-protective-relay-manipulation",
    "denial-of-control",
    "pipeline-scada-integrity",
    "nation-state-ics-supply-chain",
    "solarwinds-class-supply-chain",
    "telecom-lawful-intercept-nationstate-compromise",
)


def upgrade() -> None:
    op.add_column(
        "scenario_library_entries",
        sa.Column(
            "loss_shape",
            sa.Enum(
                "capped",
                "catastrophic",
                native_enum=False,
                name="library_entry_loss_shape",
            ),
            server_default="capped",
            nullable=False,
        ),
    )
    bind = op.get_bind()
    for slug in _CATASTROPHIC_SLUGS:
        bind.execute(
            sa.text(
                "UPDATE scenario_library_entries SET loss_shape = 'catastrophic' "
                "WHERE slug = :slug AND version = 1"
            ),
            {"slug": slug},
        )


def downgrade() -> None:
    op.drop_column("scenario_library_entries", "loss_shape")
