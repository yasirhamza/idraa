"""Epic D data-quality follow-ups: clean 4 forbidden citations from narrative
fields (#510) + land 2 differentiated TEF values (#505). Reads the seed JSON
(source of truth, D-iii-a d3f1a7c9e5b2 convergence pattern) and UPDATEs
description / example_incidents / canonical_fair_gap / threat_event_frequency
for the affected slugs. Parameterized binds. No-op downgrade.

Revision ID: c8e2f1a4b6d3
Down revision: 4616e1b032fe
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "c8e2f1a4b6d3"
down_revision = "4616e1b032fe"
branch_labels = None
depends_on = None

_NARRATIVE_SLUGS = (
    "agri-coop-bec-fraud",
    "crop-science-ip-exfiltration",
    "education-research-ip-exfiltration",
    "energy-billing-system-tamper",
)
_TEF_SLUGS = ("chemical-process-safety-attack", "grid-protective-relay-manipulation")

_UPDATE_NARRATIVE = sa.text(
    "UPDATE scenario_library_entries SET description = :description, "
    "example_incidents = :example_incidents, canonical_fair_gap = :canonical_fair_gap "
    "WHERE slug = :slug AND version = 1"
)
_UPDATE_TEF = sa.text(
    "UPDATE scenario_library_entries SET threat_event_frequency = :tef "
    "WHERE slug = :slug AND version = 1"
)


def _seed() -> dict[str, dict]:
    def _paths(root: Path) -> list[Path]:
        return [
            root / "data" / n
            for n in ("seed_library_entries.json", "seed_library_entries_extension.json")
        ]

    paths: list[Path] | None = None
    try:  # primary: the installed idraa package root (D-iii-a d3f1a7c9e5b2 pattern)
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
    for slug in _NARRATIVE_SLUGS:
        e = seed[slug]
        bind.execute(
            _UPDATE_NARRATIVE,
            {
                "description": e["description"],
                "example_incidents": e.get("example_incidents"),
                "canonical_fair_gap": e["canonical_fair_gap"],
                "slug": slug,
            },
        )
    for slug in _TEF_SLUGS:
        bind.execute(
            _UPDATE_TEF,
            {"tef": json.dumps(seed[slug]["threat_event_frequency"]), "slug": slug},
        )


def downgrade() -> None:
    """No-op -- one-way content migration (D-iii-a d3f1a7c9e5b2 policy, ruling R6).
    Prior narrative/TEF payloads are superseded and recoverable from git only."""
    pass
