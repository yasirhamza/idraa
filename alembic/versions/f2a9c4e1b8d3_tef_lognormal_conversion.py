"""#tef-lognormal: TEF PERT->lognormal + full cross-sector de-templating content
migration. Reads the seed JSON (source of truth, d3f1a7c9e5b2/d4918202a23a
pattern) and UPDATEs threat_event_frequency for every entry to the converted
lognormal node. Parameterized binds. No-op downgrade (representation change, no
lossy inverse).

Revision ID: f2a9c4e1b8d3
Down revision: b7c3e9d15a24
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "f2a9c4e1b8d3"
down_revision = "b7c3e9d15a24"
branch_labels = None
depends_on = None

_UPDATE_TEF = sa.text(
    "UPDATE scenario_library_entries SET threat_event_frequency = :v "
    "WHERE slug = :slug AND version = 1"
)


def _seed() -> dict[str, dict]:
    def _paths(root: Path) -> list[Path]:
        return [
            root / "data" / n
            for n in ("seed_library_entries.json", "seed_library_entries_extension.json")
        ]

    paths: list[Path] | None = None
    try:  # primary: the installed idraa package root
        import idraa

        cand = _paths(Path(idraa.__file__).resolve().parent.parent.parent)
        if all(p.exists() for p in cand):
            paths = cand
    except Exception:  # pragma: no cover - fallback
        paths = None
    if paths is None:  # fallback: migration-file-relative repo root
        paths = _paths(Path(__file__).resolve().parent.parent.parent)
    rows: list[dict] = []
    for p in paths:
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    return {r["slug"]: r for r in rows}


def upgrade() -> None:
    seed = _seed()
    bind = op.get_bind()
    for slug, entry in seed.items():
        bind.execute(
            _UPDATE_TEF,
            {"v": json.dumps(entry["threat_event_frequency"]), "slug": slug},
        )


def downgrade() -> None:
    # No-op: representation change; the pre-conversion PERT triples are recoverable
    # from git history / the prior migration chain, not worth a lossy inverse.
    pass
