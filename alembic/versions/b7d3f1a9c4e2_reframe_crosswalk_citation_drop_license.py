"""Reframe framework-crosswalk citation: drop copyright/license over-claim.

Revision ID: b7d3f1a9c4e2
Revises: c9e4f7a2b8d1
Create Date: 2026-07-14

The framework->FAIR-CAM crosswalk seed (``data/seed_framework_crosswalk.json``)
stamped every ``framework_controls.citation`` with ``copyright = "© 2023 FAIR
Institute"`` and ``license = "CC-BY-NC-ND-4.0"``. The stored entries are factual
mapping RELATIONSHIPS (framework code -> FAIR-CAM function) plus public-domain /
CIS framework text — not reproductions of the source documents' prose/expression.
Recording our own metadata that characterizes these facts as a third party's
copyrighted work under a restrictive (NonCommercial/NoDerivatives) license is a
self-inflicted mis-characterization. This migration strips those two keys from
existing DB rows so the deployed data matches the reframed seed + NOTICE (honest
reference-attribution, no license claim over facts). The ``source`` /
``document`` attribution fields are preserved.

Partial-DB safe + idempotent: rows without the keys are skipped; a second run is a
no-op. Downgrade restores the prior copyright/license stamp purely for
reversibility (it re-introduces the over-claim — do not rely on it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d3f1a9c4e2"
down_revision: str | Sequence[str] | None = "c9e4f7a2b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# sa.JSON() lets SQLAlchemy (de)serialize the citation column on both SQLite (dev)
# and Postgres (later) without hand-rolled casts.
_controls = sa.table(
    "framework_controls",
    sa.column("id", sa.Uuid()),
    sa.column("citation", sa.JSON()),
)

_DROP_KEYS = ("copyright", "license")
_LEGACY_COPYRIGHT = "© 2023 FAIR Institute"
_LEGACY_LICENSE = "CC-BY-NC-ND-4.0"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(_controls.c.id, _controls.c.citation)).fetchall()
    for row_id, citation in rows:
        if not isinstance(citation, dict):
            continue
        if not any(k in citation for k in _DROP_KEYS):
            continue
        new_citation = {k: v for k, v in citation.items() if k not in _DROP_KEYS}
        bind.execute(
            _controls.update().where(_controls.c.id == row_id).values(citation=new_citation)
        )


def downgrade() -> None:
    # Reversibility only: re-stamps the prior over-claiming copyright/license onto
    # FAIR-Institute-sourced rows. Not something to rely on.
    bind = op.get_bind()
    rows = bind.execute(sa.select(_controls.c.id, _controls.c.citation)).fetchall()
    for row_id, citation in rows:
        if not isinstance(citation, dict) or citation.get("source") != "FAIR Institute":
            continue
        restored = {**citation, "copyright": _LEGACY_COPYRIGHT, "license": _LEGACY_LICENSE}
        bind.execute(_controls.update().where(_controls.c.id == row_id).values(citation=restored))
