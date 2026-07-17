"""seed attack full mappings

Issue #475 P2: seeds data/seed_attack_full_mappings.json into
library_entry_attack_mappings. Resolves entry_slug -> (id, MAX(version)) and
(domain, technique_id) -> attack_techniques.id at migration time; a missing
slug/technique raises (fail-loud - never silently skip a curated claim).
Mirrors c51975647c57's exemplar loader (disjoint slug set, guard-tested).

Revision ID: 617f5ca862c3
Revises: 291038b726fd
Create Date: 2026-07-05 10:30:46.407241

"""
import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '617f5ca862c3'
down_revision: Union[str, Sequence[str], None] = '291038b726fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _as_uuid(value: object) -> uuid.UUID:
    """Arch-B1: a raw-text SELECT returns the stored 32-hex STRING on SQLite
    (a uuid.UUID on native-uuid dialects); re-binding a str through a
    sa.Uuid(as_uuid) column crashes with AttributeError ('str' has no 'hex')
    - empirically verified at plan-gate. Normalize before re-binding."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def upgrade() -> None:
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_attack_full_mappings.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_attack_full_mappings.json"
        )
    payload = json.loads(seed_path.read_text(encoding="utf-8"))

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
            raise RuntimeError(f"full mapping references unknown entry slug {seed.entry_slug!r}")
        tech_row = bind.execute(
            sa.text(
                "SELECT id FROM attack_techniques WHERE domain = :domain AND technique_id = :tid"
            ),
            {"domain": seed.domain, "tid": seed.technique_id},
        ).first()
        if tech_row is None:
            raise RuntimeError(
                f"full mapping references unknown technique {seed.domain}/{seed.technique_id}"
            )
        bind.execute(
            mappings_tbl.insert().values(
                id=uuid.uuid4(),
                library_entry_id=_as_uuid(entry_row[0]),
                library_entry_version=entry_row[1],
                technique_id=_as_uuid(tech_row[0]),
                rationale=seed.rationale,
                provenance=seed.provenance,
                citations=seed.citations,
            )
        )


def downgrade() -> None:
    """Slug-scoped (Sec2-N3/Arch-N3): delete only rows for entries this file
    curated. Exemplar entries live only in seed_attack_exemplar_mappings.json
    (disjoint slug sets, guard-tested), so their rows survive."""
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_attack_full_mappings.json"
    if not seed_path.exists():
        seed_path = Path(__file__).resolve().parent.parent.parent / "data" / "seed_attack_full_mappings.json"
    slugs = sorted({m["entry_slug"] for m in json.loads(seed_path.read_text())["mappings"]})
    bind = op.get_bind()
    for slug in slugs:
        rows = bind.execute(
            sa.text("SELECT id FROM scenario_library_entries WHERE slug = :slug"),
            {"slug": slug},
        ).all()
        for (entry_id,) in rows:
            bind.execute(
                sa.text("DELETE FROM library_entry_attack_mappings WHERE library_entry_id = :eid"),
                {"eid": entry_id},
            )
