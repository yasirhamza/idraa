"""Epic F (#192) control-library re-curation + parent-tag convergence.

Revision ID: f6a2b0d4e8c3
Revises: e5f1a9c3d7b2
Create Date: 2026-09-29

Two independent effects, both driven by ``data/seed_control_library_entries.json``
(the JSON is the single source of truth; DB and JSON converge by construction):

1. **Versioned re-curation (five touched slugs, ``_EPIC_F_SLUGS``).** Each gains ONE
   FAIR-CAM assignment channel, following the ``b8d3f6a1c4e7`` delete-children ->
   bump-parent-version -> insert-children pattern (same ``_seed_path()``,
   ``ControlLibraryEntrySeed`` validation, ``uuid4().hex`` ids, skip a slug absent
   from the DB):

     security-information-event-management (SIEM)  +lec_det_visibility, grounded by
         CIS Safeguards 8.2 and 8.10 (audit-log collection and retention).
     file-integrity-monitoring (FIM)  +lec_det_visibility, grounded by the
         already-carried CIS 3.14/8.5 (integrity baselining).
     saas-security-posture-management (SSPM)  +lec_det_visibility, grounded by
         CIS Safeguard 8.12 (service-provider log collection).
     secure-remote-access (SRA)  +lec_prev_resistance (coverage deliberately low,
         0.2 -- scoped to the interception-of-remote-session-traffic vector),
         grounded by the already-carried NIST CSF PR.AC-3.
     security-awareness-training (SAT)  +dsc_prev_ensure_capability, grounded by
         the already-carried NIST CSF PR.AT-1 / CIS 14.1 / 14.2.

   In the SAME loop as the bump, this migration also UPDATEs each of these five
   parents' three framework-tag columns (``nist_csf_subcategories``,
   ``cis_safeguards``, ``iso_27001_controls``) to the current JSON (the
   ``d4f6a2b9c8e1`` serialiser, ``json.dumps(seed.<field>)``) -- SIEM and SSPM gain
   CIS 8.2/8.10 and 8.12 respectively; FIM/SRA/SAT tags are unchanged by Epic F.

2. **Parent-tag convergence for every OTHER seed entry, IN PLACE, NO version bump.**
   For every seed entry not in ``_EPIC_F_SLUGS``, this migration compares the
   latest-version row's three tag columns against the current JSON and UPDATEs them
   in place where they differ. This repairs the pre-existing #437 tranche-2 drift:
   the REVIEWED crosswalk-seed extensions landed in ``c7e2a9b4f1d6`` (CIS 4.8 ->
   avoidance, CIS 14.2 -> resistance, CIS 16.1 -> resistance) were never propagated
   into the tranche-2 entries' own parent-tag columns by any re-curation migration
   (commit ``4348859c``), so e.g. ``security-conscious-personnel`` can be missing
   CIS 14.2 on its stored row despite carrying it in the seed. Framework tags are
   NOT a scoring input (the FAIR-CAM engine reads only ``control_library_entry_
   assignments``), so this repair deliberately does NOT bump ``version`` -- a bump
   would mark every control already adopted from an otherwise-unaffected entry
   resync-stale (#438) for a tag-only change that cannot move its score.

   Consequence for adopted controls (#438, spec §4.6, M-15): adopting a SIEM, FIM,
   SSPM, SRA or SAT control (version-bumped here) becomes resync-stale; the stored
   run's numbers change only after the user re-syncs, and that re-sync REPLACES
   per-assignment user tuning with the new library set (shown in the diff first --
   never silently). Adopted controls pinned to any OTHER (tag-only-repaired) entry
   are unaffected: they keep their already-copied tags until the next version bump
   plus re-sync (correct under the no-bump rationale above; the dashboard
   framework-coverage panel does not move for them).

Downgrade is a documented no-op, following the ``b8d3f6a1c4e7`` precedent: the JSON
seed is the single source of truth, and pre-curation assignment/tag payloads are
recoverable from git history only -- restoring them inline would dual-source the
data and invert that guarantee.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a2b0d4e8c3"
down_revision: str | Sequence[str] | None = "e5f1a9c3d7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# The five Epic F (#192) slugs that gain ONE new FAIR-CAM assignment channel and
# are version-bumped (module docstring, effect 1).
_EPIC_F_SLUGS = (
    "security-information-event-management",
    "file-integrity-monitoring",
    "saas-security-posture-management",
    "secure-remote-access",
    "security-awareness-training",
)

def _seed_path() -> Path:
    """Resolve data/seed_control_library_entries.json via the package root, with a
    __file__-relative fallback (mirrors d4f6a2b9c8e1._seed / b8d3f6a1c4e7._seed_path)."""
    import idraa

    root = Path(idraa.__file__).resolve().parent.parent.parent
    seed = root / "data" / "seed_control_library_entries.json"
    if not seed.exists():
        seed = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_control_library_entries.json"
        )
    return seed


def upgrade() -> None:
    from idraa.schemas.control_library import ControlLibraryEntrySeed

    payload = json.loads(_seed_path().read_text(encoding="utf-8"))
    by_slug = {
        e["slug"]: ControlLibraryEntrySeed.model_validate(e) for e in payload["entries"]
    }
    missing = set(_EPIC_F_SLUGS) - set(by_slug)
    if missing:  # pragma: no cover - defensive: seed must contain every Epic F slug
        raise RuntimeError(f"Epic F slug(s) absent from seed JSON: {sorted(missing)}")

    bind = op.get_bind()
    now = datetime.now(UTC).isoformat()

    assignment_insert = sa.text(
        """
        INSERT INTO control_library_entry_assignments
          (id, library_entry_id, library_entry_version, sub_function,
           capability_default, coverage_default, reliability_default,
           capability_provenance, capability_citations,
           coverage_provenance, coverage_citations,
           reliability_provenance, reliability_citations,
           created_at, updated_at)
        VALUES
          (:id, :library_entry_id, :library_entry_version, :sub_function,
           :capability_default, :coverage_default, :reliability_default,
           :capability_provenance, :capability_citations,
           :coverage_provenance, :coverage_citations,
           :reliability_provenance, :reliability_citations,
           :now, :now)
        """
    )

    bumped = 0

    # --- Effect 1: the five Epic F slugs -- delete children, bump version, insert
    # children, sync tags. ---
    for slug in _EPIC_F_SLUGS:
        seed = by_slug[slug]
        row = bind.execute(
            sa.text(
                "SELECT id, version FROM control_library_entries "
                "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
            ),
            {"slug": slug},
        ).first()
        if row is None:
            # Slug not present in this DB (partial seed) -- skip silently (pilot policy).
            continue
        entry_id, cur_version = row[0], row[1]
        new_version = cur_version + 1

        # 1. Delete existing children FIRST (safe under FK enforcement).
        bind.execute(
            sa.text(
                "DELETE FROM control_library_entry_assignments "
                "WHERE library_entry_id = :eid AND library_entry_version = :v"
            ),
            {"eid": entry_id, "v": cur_version},
        )
        # 2. Bump the parent version in place (the field #438 keys on) and sync its
        # framework-tag columns to the current JSON in the same statement.
        bind.execute(
            sa.text(
                "UPDATE control_library_entries "
                "SET version = :nv, updated_at = :now, "
                "    nist_csf_subcategories = :nist, "
                "    cis_safeguards = :cis, "
                "    iso_27001_controls = :iso "
                "WHERE id = :eid AND version = :v"
            ),
            {
                "nv": new_version,
                "eid": entry_id,
                "v": cur_version,
                "now": now,
                "nist": json.dumps(seed.nist_csf_subcategories),
                "cis": json.dumps(seed.cis_safeguards),
                "iso": json.dumps(seed.iso_27001_controls),
            },
        )
        bumped += 1
        # 3. Insert the re-curated children at the new version.
        for a in seed.assignments:
            bind.execute(
                assignment_insert,
                {
                    "id": uuid.uuid4().hex,  # 32-char no-hyphen
                    "library_entry_id": entry_id,
                    "library_entry_version": new_version,
                    "sub_function": a.sub_function.value,
                    "capability_default": a.capability_default,
                    "coverage_default": a.coverage_default,
                    "reliability_default": a.reliability_default,
                    "capability_provenance": a.capability_provenance,
                    "capability_citations": json.dumps(a.capability_citations),
                    "coverage_provenance": a.coverage_provenance,
                    "coverage_citations": json.dumps(a.coverage_citations),
                    "reliability_provenance": a.reliability_provenance,
                    "reliability_citations": json.dumps(a.reliability_citations),
                    "now": now,
                },
            )

    # --- Effect 2: every OTHER seed entry -- repair drifted parent tags in place,
    # no version bump (module docstring, effect 2 / #437 T2 drift repair). ---
    tag_synced = 0
    for slug, seed in by_slug.items():
        if slug in _EPIC_F_SLUGS:
            continue
        row = bind.execute(
            sa.text(
                "SELECT id, version, nist_csf_subcategories, cis_safeguards, "
                "       iso_27001_controls "
                "FROM control_library_entries WHERE slug = :slug "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"slug": slug},
        ).first()
        if row is None:
            # Slug not present in this DB (partial seed) -- skip silently.
            continue
        entry_id, cur_version, raw_nist, raw_cis, raw_iso = row
        stored = (json.loads(raw_nist), json.loads(raw_cis), json.loads(raw_iso))
        current = (seed.nist_csf_subcategories, seed.cis_safeguards, seed.iso_27001_controls)
        if stored == current:
            continue
        bind.execute(
            sa.text(
                "UPDATE control_library_entries "
                "SET nist_csf_subcategories = :nist, "
                "    cis_safeguards = :cis, "
                "    iso_27001_controls = :iso, "
                "    updated_at = :now "
                "WHERE id = :eid AND version = :v"
            ),
            {
                "eid": entry_id,
                "v": cur_version,
                "now": now,
                "nist": json.dumps(current[0]),
                "cis": json.dumps(current[1]),
                "iso": json.dumps(current[2]),
            },
        )
        tag_synced += 1

    logger.info("epic-f control re-curation: bumped=%d tag_synced=%d", bumped, tag_synced)


def downgrade() -> None:
    """No-op -- policy choice (mirrors a7c3f9b21e60 / d4fc657eb424 / b8d3f6a1c4e7):
    pre-curation assignment and tag payloads are recoverable from git history only;
    restoring them inline would dual-source the data and invert the
    JSON-single-source-of-truth guarantee."""
