"""Backfill scenario_attack_mappings from curated library mappings (#475 follow-up).

Pre-#483 scenarios predate wizard clone-copy, so library-pinned scenarios
carry zero technique mappings even where their pinned entry+version has
curated rows. This migration mirrors
services/attack_mappings.py::copy_library_attack_mappings semantics over
EXISTING scenarios, ALL statuses (clone-copy would have populated them
regardless of later lifecycle; coverage filters to ACTIVE at read time):
source='library', rationale copied, provenance/citations deliberately NOT
copied (Meth-N2 — epistemic labels live in the curated layer; an org row
must never render as more certain than the curated claim it derives from).

Idempotent: existing (scenario_id, technique_id) pairs are skipped, so
hand-authored rows are never clobbered and P2's full-curation pass re-runs
this logic in a fresh revision. Unresolvable pins (custom scenarios, JSON
null, malformed entry_id, entry+version with no curated rows — NORMAL
pre-P2) are skipped, never abort.

Audit: no synthetic per-scenario audit rows (there is no acting user; a
migration writing rows as a fake actor is noise, not audit). This
docstring + the printed insert count are the record (PR #346 gated-
migration precedent).

Downgrade: documented NO-OP — backfilled rows are indistinguishable from
clone-copied ones; deleting by source='library' would destroy clone data.

Revision ID: 291038b726fd
Revises: c51975647c57
Create Date: 2026-07-04 23:47:52.786957

"""

from typing import Sequence, Union

import json
import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "291038b726fd"
down_revision: Union[str, Sequence[str], None] = "c51975647c57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _as_uuid(value: object) -> uuid.UUID:
    """Raw-text SELECT returns the stored 32-hex STRING on SQLite (a
    uuid.UUID on native-uuid dialects); normalize before set membership /
    re-binding through sa.Uuid columns (same guard as c51975647c57)."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


_mappings_tbl = sa.table(
    "scenario_attack_mappings",
    sa.column("id", sa.Uuid()),
    sa.column("organization_id", sa.Uuid()),
    sa.column("scenario_id", sa.Uuid()),
    sa.column("technique_id", sa.Uuid()),
    sa.column("source", sa.String()),
    sa.column("rationale", sa.Text()),
)

_curated_tbl = sa.table(
    "library_entry_attack_mappings",
    sa.column("library_entry_id", sa.Uuid()),
    sa.column("library_entry_version", sa.Integer()),
    sa.column("technique_id", sa.Uuid()),
    sa.column("rationale", sa.Text()),
)


def _backfill(bind: sa.Connection) -> int:
    scenarios = bind.execute(
        sa.text("SELECT id, organization_id, library_pin FROM scenarios")
    ).all()
    existing = {
        (_as_uuid(s), _as_uuid(t))
        for s, t in bind.execute(
            sa.text("SELECT scenario_id, technique_id FROM scenario_attack_mappings")
        )
    }
    inserted = 0
    for sid_raw, org_raw, pin_raw in scenarios:
        if not pin_raw:
            continue  # SQL NULL
        try:
            pin = pin_raw if isinstance(pin_raw, dict) else json.loads(pin_raw)
        except (TypeError, ValueError):
            continue
        # JSON column stores Python None as the TEXT 'null' (Arch2-I3 in
        # attack_coverage.py) — json.loads gives None; custom scenarios land here.
        if not isinstance(pin, dict) or not pin.get("entry_id"):
            continue
        try:
            entry_id = uuid.UUID(str(pin["entry_id"]))  # pin stores HYPHENATED str(uuid)
            entry_version = int(pin["version"])
        except (KeyError, TypeError, ValueError):
            continue  # malformed pin: skip, never abort
        curated = bind.execute(
            sa.select(_curated_tbl.c.technique_id, _curated_tbl.c.rationale).where(
                _curated_tbl.c.library_entry_id == entry_id,
                _curated_tbl.c.library_entry_version == entry_version,
            )
        ).all()
        sid = _as_uuid(sid_raw)
        org_id = _as_uuid(org_raw)
        for tech_raw, rationale in curated:
            tech = _as_uuid(tech_raw)
            if (sid, tech) in existing:
                continue
            bind.execute(
                _mappings_tbl.insert().values(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    scenario_id=sid,
                    technique_id=tech,
                    source="library",
                    rationale=rationale,
                )
            )
            existing.add((sid, tech))
            inserted += 1
    return inserted


def upgrade() -> None:
    inserted = _backfill(op.get_bind())
    print(f"scenario_attack_mappings backfill: inserted {inserted} rows")  # noqa: T201


def downgrade() -> None:
    """No-op (see module docstring)."""
