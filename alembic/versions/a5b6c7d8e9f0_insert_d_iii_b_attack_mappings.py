"""Epic D-iii-b: insert-if-absent ATT&CK full-mapping rows for the 8 new entries

Revision ID: a5b6c7d8e9f0
Revises: f4a1c2b3d4e5
Create Date: 2026-07-06

Issue #497: seeds ``data/seed_attack_d_iii_b_full.json`` into
``library_entry_attack_mappings``. Resolves entry_slug -> (id, MAX(version))
and (domain, technique_id) -> attack_techniques.id at migration time; a
missing slug/technique raises (fail-loud -- never silently skip a curated
claim), mirroring ``c51975647c57`` / ``617f5ca862c3``.

**Why a dedicated migration is needed:** prod is already past the historical
mapping migrations (``c51975647c57``, ``617f5ca862c3``), so the 8 new library
rows landed by ``f4a1c2b3d4e5`` would otherwise have ZERO ATT&CK mappings
(design §5 unmet in prod; #484 dashboard blank for these 8 entries).

**Insert-if-absent (deviation from the historical migrations' blanket
insert):** unlike ``c51975647c57``/``617f5ca862c3`` (which run once, at a
revision below which no such rows can exist, and are safe to insert
unconditionally), this migration must be safely re-runnable -- it checks for
an existing (library_entry_id, library_entry_version, technique_id) row
before inserting, so re-running upgrade is a no-op rather than an
IntegrityError against the ``uq_library_entry_attack_mapping`` constraint.

Downgrade is slug-scoped: deletes only mapping rows for the 8 D-iii-b entry
slugs, leaving the exemplar + full-mapping rows for every other entry intact.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a1c2b3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _as_uuid(value: object) -> uuid.UUID:
    """Arch-B1: a raw-text SELECT returns the stored 32-hex STRING on SQLite
    (a uuid.UUID on native-uuid dialects); re-binding a str through a
    sa.Uuid(as_uuid) column crashes with AttributeError ('str' has no 'hex')
    -- empirically verified at plan-gate. Normalize before re-binding."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _seed_path() -> Path:
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_attack_d_iii_b_full.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_attack_d_iii_b_full.json"
        )
    return seed_path


def _load_slugs() -> tuple[str, ...]:
    payload = json.loads(_seed_path().read_text(encoding="utf-8"))
    return tuple(sorted({m["entry_slug"] for m in payload["mappings"]}))


def upgrade() -> None:
    payload = json.loads(_seed_path().read_text(encoding="utf-8"))

    from idraa.schemas.attack_catalog import EntryAttackMappingSeed

    bind = op.get_bind()
    mappings_tbl = sa.table(
        "library_entry_attack_mappings",
        sa.column("id", sa.Uuid()),
        sa.column("library_entry_id", sa.Uuid()),
        sa.column("library_entry_version", sa.Integer()),
        sa.column("technique_id", sa.Uuid()),
        sa.column("rationale", sa.Text()),
        sa.column("provenance", sa.String()),
        sa.column("citations", sa.JSON()),
    )

    for raw in payload["mappings"]:
        seed = EntryAttackMappingSeed.model_validate(raw)
        entry_row = bind.execute(
            sa.text(
                "SELECT id, version FROM scenario_library_entries "
                "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
            ),
            {"slug": seed.entry_slug},
        ).first()
        if entry_row is None:
            raise RuntimeError(f"D-iii-b mapping references unknown entry slug {seed.entry_slug!r}")
        tech_row = bind.execute(
            sa.text(
                "SELECT id FROM attack_techniques WHERE domain = :domain AND technique_id = :tid"
            ),
            {"domain": seed.domain, "tid": seed.technique_id},
        ).first()
        if tech_row is None:
            raise RuntimeError(
                f"D-iii-b mapping references unknown technique {seed.domain}/{seed.technique_id}"
            )
        entry_id = _as_uuid(entry_row[0])
        entry_version = entry_row[1]
        technique_id = _as_uuid(tech_row[0])

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
    """Slug-scoped delete: remove only mapping rows for the 8 D-iii-b entry
    slugs, leaving every other entry's exemplar/full mapping rows intact."""
    bind = op.get_bind()
    for slug in _load_slugs():
        rows = bind.execute(
            sa.text("SELECT id FROM scenario_library_entries WHERE slug = :slug"),
            {"slug": slug},
        ).all()
        for (entry_id,) in rows:
            bind.execute(
                sa.text(
                    "DELETE FROM library_entry_attack_mappings WHERE library_entry_id = :eid"
                ),
                {"eid": entry_id},
            )
