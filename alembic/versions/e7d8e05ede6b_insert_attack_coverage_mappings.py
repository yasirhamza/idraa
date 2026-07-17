"""Attack-coverage gap-fill epic (#529): insert-if-absent ATT&CK
full-mapping rows for the 9 new entries + 3 ICS-twin rows

Revision ID: e7d8e05ede6b
Revises: 63cfe62ef5a7
Create Date: 2026-07-10

Issue #529 Task 2: seeds ``data/seed_attack_avgapfill_full.json`` (25 rows)
into ``library_entry_attack_mappings``. Resolves entry_slug -> (id, MAX(version))
and (domain, technique_id) -> attack_techniques.id at migration time; a
missing slug/technique raises (fail-loud -- never silently skip a curated
claim), mirroring ``c51975647c57`` / ``617f5ca862c3`` / ``a5b6c7d8e9f0``.

``data/seed_attack_avgapfill_full.json`` carries 22 rows for the 9 new
entries authored by ``63cfe62ef5a7`` PLUS 3 "ICS-twin" rows attached to
EXISTING pre-published entries (``watering-hole-industry-targeted``,
``it-ot-bridge-compromise``, ``oem-remote-maintenance-abuse``) per design doc
Sec 6.1 -- those 3 hosts already carry historical mapping rows seeded by
``c51975647c57`` (3 / 4 / 3 respectively).

**Why a dedicated migration is needed:** prod is already past the historical
mapping migrations, so the 9 new library rows landed by ``63cfe62ef5a7``
would otherwise have ZERO ATT&CK mappings (design Sec 5 unmet in prod; #484
dashboard blank for these 9 entries).

**Insert-if-absent (deviation from the historical migrations' blanket
insert):** unlike the historical full/exemplar migrations (which run once, at
a revision below which no such rows can exist, and are safe to insert
unconditionally), this migration must be safely re-runnable -- it checks for
an existing (library_entry_id, library_entry_version, technique_id) row
before inserting, so re-running upgrade is a no-op rather than an
IntegrityError against the ``uq_library_entry_attack_mapping`` constraint.

**[BLOCKER FIX -- downgrade() is TECHNIQUE-scoped, not entry/slug-scoped.]**
The ``a5b6c7d8e9f0`` precedent's downgrade deletes ALL mapping rows for an
entry (``DELETE ... WHERE library_entry_id = :eid``) -- safe there because
every entry in that migration's scope was brand-new (no prior mapping rows to
destroy). That pattern is UNSAFE here: 3 of this migration's 25 rows attach
to PRE-EXISTING entries that carry historical mapping rows (10 total: 3 + 4 +
3). An entry-scoped delete-all on downgrade would wipe those 10 historical
rows along with the 3 ICS-twin rows this migration actually added -- data
loss of curated, cited claims that predate this epic.

The fix: downgrade() re-resolves each of the 25 seed rows to its exact
``(library_entry_id, library_entry_version, technique_id)`` tuple (the same
resolution upgrade() performs) and deletes ONLY that tuple. For the 9 new
entries this removes all of their rows (they have no other mappings, so the
net effect matches the old blanket-delete pattern). For the 3 ICS-twin hosts
this removes only the one row this migration inserted, leaving their
historical mappings fully intact.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7d8e05ede6b"
down_revision: str | Sequence[str] | None = "63cfe62ef5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _as_uuid(value: object) -> uuid.UUID:
    """Arch-B1: a raw-text SELECT returns the stored 32-hex STRING on SQLite
    (a uuid.UUID on native-uuid dialects); re-binding a str through a
    sa.Uuid(as_uuid) column crashes with AttributeError ('str' has no 'hex')
    -- empirically verified at plan-gate. Normalize before re-binding."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _seed_path() -> Path:
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_attack_avgapfill_full.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_attack_avgapfill_full.json"
        )
    return seed_path


def _mappings_table() -> sa.Table:
    return sa.table(
        "library_entry_attack_mappings",
        sa.column("id", sa.Uuid()),
        sa.column("library_entry_id", sa.Uuid()),
        sa.column("library_entry_version", sa.Integer()),
        sa.column("technique_id", sa.Uuid()),
        sa.column("rationale", sa.Text()),
        sa.column("provenance", sa.String()),
        sa.column("citations", sa.JSON()),
    )


def _resolve(bind: sa.Connection, seed: object) -> tuple[uuid.UUID, int, uuid.UUID]:
    """Resolve one validated seed row to (entry_id, entry_version, technique_id).

    Fail-loud on an unknown slug or unknown technique -- a curated ATT&CK
    claim referencing a typo'd slug/technique must surface at
    ``alembic upgrade``, never silently vanish.
    """
    entry_row = bind.execute(
        sa.text(
            "SELECT id, version FROM scenario_library_entries "
            "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
        ),
        {"slug": seed.entry_slug},
    ).first()
    if entry_row is None:
        raise RuntimeError(
            f"attack-coverage mapping references unknown entry slug {seed.entry_slug!r}"
        )
    tech_row = bind.execute(
        sa.text("SELECT id FROM attack_techniques WHERE domain = :domain AND technique_id = :tid"),
        {"domain": seed.domain, "tid": seed.technique_id},
    ).first()
    if tech_row is None:
        raise RuntimeError(
            f"attack-coverage mapping references unknown technique {seed.domain}/{seed.technique_id}"
        )
    return _as_uuid(entry_row[0]), entry_row[1], _as_uuid(tech_row[0])


def upgrade() -> None:
    payload = json.loads(_seed_path().read_text(encoding="utf-8"))

    from idraa.schemas.attack_catalog import EntryAttackMappingSeed

    bind = op.get_bind()
    mappings_tbl = _mappings_table()

    for raw in payload["mappings"]:
        seed = EntryAttackMappingSeed.model_validate(raw)
        entry_id, entry_version, technique_id = _resolve(bind, seed)

        # Insert-if-absent: this migration must be safely re-runnable, unlike
        # the historical exemplar/full migrations which run once below any
        # possible pre-existing row. Use the typed Core table (not raw
        # sa.text with bare UUID params -- the sqlite3 DBAPI cannot adapt a
        # plain uuid.UUID object; mappings_tbl's sa.Uuid() column type
        # handles the bind-param coercion).
        dup = bind.execute(
            sa.select(mappings_tbl.c.id).where(
                mappings_tbl.c.library_entry_id == entry_id,
                mappings_tbl.c.library_entry_version == entry_version,
                mappings_tbl.c.technique_id == technique_id,
            )
        ).first()
        if dup is not None:
            continue

        bind.execute(
            mappings_tbl.insert().values(
                id=uuid.uuid4(),
                library_entry_id=entry_id,
                library_entry_version=entry_version,
                technique_id=technique_id,
                rationale=seed.rationale,
                provenance=seed.provenance,
                citations=seed.citations,
            )
        )


def downgrade() -> None:
    """Technique-scoped delete: remove ONLY the exact
    (library_entry_id, library_entry_version, technique_id) tuples this
    migration inserted -- re-resolving each of the 25 seed rows the same way
    upgrade() does.

    This is deliberately NOT an entry-scoped delete-all (the
    ``a5b6c7d8e9f0`` precedent's pattern): 3 of the 25 rows attach to
    pre-existing entries (the ICS-twin hosts) that carry historical mapping
    rows this migration must not touch. Technique-scoping means:
      - the 9 new entries: every one of their rows was inserted by this
        migration (they have no other mappings), so this removes all of them
        -- same net effect as a blanket per-entry delete would have had.
      - the 3 ICS-twin hosts: only the single row this migration added is
        removed; their pre-existing historical mapping rows are untouched.
    """
    payload = json.loads(_seed_path().read_text(encoding="utf-8"))

    from idraa.schemas.attack_catalog import EntryAttackMappingSeed

    bind = op.get_bind()
    mappings_tbl = _mappings_table()

    for raw in payload["mappings"]:
        seed = EntryAttackMappingSeed.model_validate(raw)

        # Resolve the entry by slug; a slug that no longer exists (e.g. a
        # downgrade run after rev1's downgrade already removed one of the 9
        # new entries, or re-run on an already-downgraded DB) means there is
        # nothing to delete for it -- skip rather than fail-loud, since
        # downgrade() must be safely re-runnable / order-tolerant.
        entry_row = bind.execute(
            sa.text(
                "SELECT id, version FROM scenario_library_entries "
                "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
            ),
            {"slug": seed.entry_slug},
        ).first()
        if entry_row is None:
            continue
        tech_row = bind.execute(
            sa.text(
                "SELECT id FROM attack_techniques WHERE domain = :domain AND technique_id = :tid"
            ),
            {"domain": seed.domain, "tid": seed.technique_id},
        ).first()
        if tech_row is None:
            continue

        entry_id = _as_uuid(entry_row[0])
        entry_version = entry_row[1]
        technique_id = _as_uuid(tech_row[0])

        bind.execute(
            mappings_tbl.delete().where(
                mappings_tbl.c.library_entry_id == entry_id,
                mappings_tbl.c.library_entry_version == entry_version,
                mappings_tbl.c.technique_id == technique_id,
            )
        )
