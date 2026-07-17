"""C-iii-a re-curation: in-place UPDATE of all 44 seed library entries.

Revision ID: 3d7b9e357d52
Revises: b7d2e8a1c5f3
Create Date: 2026-06-11 06:43:54.633473

**Data-source ruling (plan A-B2 / A2-I2 — explicit):**
Values are read from ``data/seed_library_entries*.json`` so DB rows and JSON
converge by construction (single source of truth; no 44-entry literal blob
dual-sourcing the data).  The JSON is mutable — C-iii-b will edit it again —
but this is safe by convergence: C-iii-b ships its own UPDATE migration over
the same rows, so a fresh DB running both migrations lands on the final state
regardless of intermediate values; a production DB upgraded at C-iii-a sees
C-iii-a JSON.

Both files are read (plan A2-I2): ``data/seed_library_entries.json`` (31 base
entries) **and** ``data/seed_library_entries_extension.json`` (13 entries).  A
zero-row UPDATE is silent — reading only the base file would skip 13 extension
entries without an error.  The same ``Path(idraa.__file__).resolve().parent
.parent.parent`` root-resolution used in ``de2080181a9c`` is applied to both
paths so the migration is portable across Windows / Linux paths.

**Fields updated (full list, documenting the scope explicitly):**
For every slug the migration UPDATEs the following columns to match the current
committed seed JSON, covering all changes made by Task 3:

  ``primary_loss``           — distribution, mean, sigma (lognormal) or PERT
                               values for converted entries; PERT values
                               unchanged for none-anchored anecdotal entries.
  ``secondary_loss``         — lognormal sigma/mean (inherited from primary)
                               for converted entries; unchanged for anecdotal.
  ``loss_tier``              — explicit 'paginated' | 'vendor' | 'anecdotal'
                               on every row (was 'anecdotal' server-default
                               on all rows before C-iii-a).
  ``source_citations``       — IRIS 2025 Figure A3 citation strings for
                               converted entries; unchanged for anecdotal.
  ``calibration_anchor``     — all four curated keys now present:
                               industry, revenue_tier, vuln_posture
                               (new — rule 6 #338), loss_anchor (new —
                               records the anchor or 'none' rationale).
  ``vulnerability``          — raised for bec-fraud-financial (rule 6:
                               old controlled-posture mode 0.08 → inherent
                               mode 0.20) and credential-stuffing (rule 6
                               inherent posture confirmed); unchanged for
                               all other entries.
  ``threat_event_frequency`` — reinterpreted for credential-stuffing (rule 7
                               M-B1: per-attempt → per-campaign; low=1,
                               mode=5, high=20 campaigns/year); unchanged
                               for all other entries.
  ``canonical_fair_gap``     — rewritten for credential-stuffing (rule 7:
                               describes the campaign-level model, removing
                               the per-attempt "successful stuffing rate"
                               phrasing); unchanged for all other entries.

**Downgrade (no-op — policy choice, plan A-I3/SC-I1 ruling):**
The downgrade is deliberately a no-op.  Prior content migrations downgraded
meaningfully (d4f8a91c2e30 NULL-reset; de2080181a9c cleared arrays) because
NULL / empty was a valid prior state for those columns.  After C-iii-a the
pre-curation PERT payloads are deliberately superseded and recoverable from
git history only — restoring them inline would dual-source 44 payloads and
invert the "JSON is the single source of truth" convergence guarantee.  The
migration is one-way; rollback requires a git checkout of the prior seed JSON
and a manual re-run.
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3d7b9e357d52"
down_revision: str = "b7d2e8a1c5f3"  # re-parented after rebase onto #346 (F1/F2 audit migrations)
branch_labels = None
depends_on = None


def _seed_paths() -> tuple[Path, Path]:
    """Return (base_path, extension_path) for both seed files.

    Uses the same root-resolution strategy as ``de2080181a9c``'s ``_seed_path()``:
    ``Path(idraa.__file__).resolve().parent.parent.parent`` (i.e., the repo
    root, three levels above the ``idraa`` package init).  Falls back to a
    ``__file__``-relative path for edge cases where the package root is not
    importable (e.g., bare ``alembic upgrade`` without the package installed).
    """
    import idraa

    root = Path(idraa.__file__).resolve().parent.parent.parent
    base = root / "data" / "seed_library_entries.json"
    ext = root / "data" / "seed_library_entries_extension.json"

    if not base.exists():
        # Fallback: three levels above this file (alembic/versions/<file>)
        fallback_root = Path(__file__).resolve().parent.parent.parent
        base = fallback_root / "data" / "seed_library_entries.json"
        ext = fallback_root / "data" / "seed_library_entries_extension.json"

    return base, ext


def upgrade() -> None:
    """In-place UPDATE: sync all 44 version=1 seed rows to the committed JSON.

    Reads BOTH seed files (base 31 + extension 13) and issues a parameterised
    UPDATE per slug.  A zero-row UPDATE is silent by design — the slug is simply
    not in this DB (e.g., an org that has not yet run the extension-seed
    migration).  Fields updated: see module docstring for the full list.
    """
    base_path, ext_path = _seed_paths()
    entries = json.loads(base_path.read_text(encoding="utf-8")) + json.loads(
        ext_path.read_text(encoding="utf-8")
    )

    bind = op.get_bind()
    for entry in entries:
        bind.execute(
            sa.text(
                "UPDATE scenario_library_entries "
                "SET primary_loss = :primary_loss, "
                "    secondary_loss = :secondary_loss, "
                "    loss_tier = :loss_tier, "
                "    source_citations = :source_citations, "
                "    calibration_anchor = :calibration_anchor, "
                "    vulnerability = :vulnerability, "
                "    threat_event_frequency = :threat_event_frequency, "
                "    canonical_fair_gap = :canonical_fair_gap "
                "WHERE slug = :slug AND version = 1"
            ),
            {
                "primary_loss": json.dumps(entry["primary_loss"]),
                "secondary_loss": json.dumps(entry.get("secondary_loss")),
                "loss_tier": entry.get("loss_tier", "anecdotal"),
                "source_citations": json.dumps(entry.get("source_citations", [])),
                "calibration_anchor": json.dumps(entry["calibration_anchor"]),
                "vulnerability": json.dumps(entry["vulnerability"]),
                "threat_event_frequency": json.dumps(entry["threat_event_frequency"]),
                "canonical_fair_gap": entry["canonical_fair_gap"],
                "slug": entry["slug"],
            },
        )


def downgrade() -> None:
    """No-op — policy choice, not a precedent gap (plan ruling A-I3/SC-I1).

    Prior content migrations downgraded meaningfully (d4f8a91c2e30 NULL-reset;
    de2080181a9c cleared arrays) because NULL / empty was a valid prior state.
    After C-iii-a the pre-curation PERT payloads are deliberately superseded
    and are recoverable from git history only.  Restoring them inline would
    dual-source 44 payloads and break the "JSON is single source of truth"
    convergence guarantee.  This is a one-way content migration; rollback
    requires a git checkout of the prior seed JSON commit and a manual re-run.
    """
    # Intentional no-op. See module docstring for the full policy rationale.
    pass
