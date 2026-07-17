"""seed attack exemplar mappings

Revision ID: c51975647c57
Revises: 6108f3899a78

Issue #475 T6: seeds data/seed_attack_exemplar_mappings.json into
library_entry_attack_mappings. Resolves entry_slug -> (id, MAX(version)) and
(domain, technique_id) -> attack_techniques.id at migration time; a missing
slug/technique raises (fail-loud - never silently skip a curated claim).
"""

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c51975647c57"
down_revision: Union[str, Sequence[str], None] = "6108f3899a78"
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
    seed_path = project_root / "data" / "seed_attack_exemplar_mappings.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_attack_exemplar_mappings.json"
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
            raise RuntimeError(f"exemplar mapping references unknown entry slug {seed.entry_slug!r}")
        tech_row = bind.execute(
            sa.text(
                "SELECT id FROM attack_techniques WHERE domain = :domain AND technique_id = :tid"
            ),
            {"domain": seed.domain, "tid": seed.technique_id},
        ).first()
        if tech_row is None:
            raise RuntimeError(
                f"exemplar mapping references unknown technique {seed.domain}/{seed.technique_id}"
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
    # PR-1-only blanket delete: safe at this revision (only these seed rows
    # can exist below it). SC-N6/Arch-N3: P2 seed migrations MUST scope their
    # downgrades to their own rows (slug-scoped delete), never blanket.
    op.execute(sa.text("DELETE FROM library_entry_attack_mappings"))
