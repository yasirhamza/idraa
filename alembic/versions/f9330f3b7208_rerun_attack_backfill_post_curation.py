"""Re-run scenario_attack_mappings backfill post-curation (#475 P2 follow-up).

P2's full-curation pass (617f5ca862c3) added 215 new curated
library_entry_attack_mappings rows on top of the 14 exemplars from
c51975647c57. 291038b726fd's backfill only saw the exemplar-only curated
set at the time it ran, so library-pinned scenarios whose pinned
entry+version now has NEWLY curated rows still carry zero technique
mappings. This migration re-runs the identical backfill logic
(services/attack_mappings.py::copy_library_attack_mappings semantics)
over ALL existing scenarios, ALL statuses: source='library', rationale
copied, provenance/citations deliberately NOT copied (Meth-N2 -
epistemic labels live in the curated layer; an org row must never render
as more certain than the curated claim it derives from).

Idempotent: existing (scenario_id, technique_id) pairs are skipped, so
this is safe to run again after any future curation pass. Unresolvable
pins (custom scenarios, JSON null, malformed entry_id, entry+version with
no curated rows) are skipped, never abort - this revision additionally
COUNTS those skipped-unresolvable pins (malformed uuid/int parse only,
not the routine no-pin/no-curated paths) so operators can see the ratio
of applied vs. unresolvable pins in the migration output.

Audit: no synthetic per-scenario audit rows (there is no acting user; a
migration writing rows as a fake actor is noise, not audit). This
docstring + the printed insert/skip counts are the record (PR #346
gated-migration precedent).

Downgrade: documented NO-OP - backfilled rows are indistinguishable from
clone-copied ones; deleting by source='library' would destroy clone data.

Revision ID: f9330f3b7208
Revises: 617f5ca862c3
Create Date: 2026-07-05 10:30:49.124098

"""

from typing import Sequence, Union

import json
import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9330f3b7208"
down_revision: Union[str, Sequence[str], None] = "617f5ca862c3"
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


def _backfill(bind: sa.Connection) -> tuple[int, int]:
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
    skipped = 0
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
            skipped += 1
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
    return inserted, skipped


def upgrade() -> None:
    inserted, skipped = _backfill(op.get_bind())
    print(  # noqa: T201
        f"post-curation attack backfill: inserted {inserted} rows, "
        f"skipped {skipped} unresolvable pins"
    )


def downgrade() -> None:
    """No-op (see module docstring)."""
