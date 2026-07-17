"""Milestone B (#loss-pert-overhaul): replay the converted seed PL/SL onto
scenario_library_entries.

83 capped entries: lognormal -> bounded PERT (low=p5, mode=low, high=p95).
10 catastrophic entries: byte-identical lognormal (still replayed -- idempotent).

Documented calibration-philosophy change (spec 2026-07-09 §4): capped entries
no longer reproduce the IRIS envelope mean -- the p95 cap removes the tail that
carried it. Expected-loss impact by sector: energy 1.6x, healthcare 1.6x,
manufacturing 1.9x, financial 5.2x, technology_saas/telecom 8.2x. The envelope
citations remain provenance for the (p5, p95) pair and sigma.

Downgrade is a documented no-op (content migration; the pre-conversion
lognormal is recoverable from git history).

Revision ID: d9e5a3c7f2b4
Down revision: b8c4f2e6a1d3
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "d9e5a3c7f2b4"
down_revision = "b8c4f2e6a1d3"
branch_labels = None
depends_on = None

_UPDATE = sa.text(
    "UPDATE scenario_library_entries SET primary_loss = :pl, secondary_loss = :sl "
    "WHERE slug = :slug AND version = 1"
)


def _seed() -> dict[str, dict]:
    def _paths(root: Path) -> list[Path]:
        return [
            root / "data" / n
            for n in ("seed_library_entries.json", "seed_library_entries_extension.json")
        ]

    paths: list[Path] | None = None
    try:
        import idraa

        cand = _paths(Path(idraa.__file__).resolve().parent.parent.parent)
        if all(p.exists() for p in cand):
            paths = cand
    except Exception:  # pragma: no cover - fallback
        paths = None
    if paths is None:
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
            _UPDATE,
            {
                "pl": json.dumps(entry["primary_loss"]),
                "sl": json.dumps(entry["secondary_loss"]) if entry.get("secondary_loss") else None,
                "slug": slug,
            },
        )


def downgrade() -> None:
    # No-op: content migration; pre-conversion values recoverable from git.
    pass
