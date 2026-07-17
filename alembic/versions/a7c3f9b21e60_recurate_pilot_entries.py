"""Re-curate the #437 pilot control-library entries (Task 5).

Revision ID: a7c3f9b21e60
Revises: e14c75d22129
Create Date: 2026-06-30

Pilot re-curation under the #437 function-decomposition methodology gate. Six
entries get faithful, multi-function, per-value-cited assignment sets that
capture what each product actually does (rubric
``docs/reference/control-function-decomposition-rubric.md``):

  cloud-security-posture-management  — +lec_prev_avoidance (expert-estimate, MITRE grounds
                                       channel) +lec_prev_resistance (expert-estimate, MITRE
                                       grounds channel) +vmc_corr_implementation (meta, null
                                       cap) +dsc_prev_sa_analysis; keeps vmc_id_control_monitoring.
                                       $0 -> SCORING (direct asset hardening).
  security-awareness-training        — dsc_prev_communication (enriched, expert-estimate)
                                       + vmc_id_control_monitoring (phishing-sim = human-control
                                       monitoring, expert-estimate). Stays NON-SCORING residual
                                       (#439): NIST PR.AT / CIS 14 homes ground only DSC/VMC;
                                       FAIR-CAM treats awareness as decision-support, not asset
                                       hardening — no lec_prev_* scorer grafted (rubric §6.4/I5).
  data-loss-prevention               — verify + per-value provenance (lec_prev_resistance
                                       grounded by MITRE M1057). Already SCORING.
  data-backup-recovery               — faithful enrichment; genuinely meta (resilience +
                                       control-monitoring) -> NON-SCORING residual (#439).
  data-classification-handling       — DSC-only (classification/handling) -> NON-SCORING
                                       residual (#439).
  threat-modeling                    — DSC-only (analysis) -> NON-SCORING residual (#439).

**Version bump (the field #438 keys on).** Each pilot entry's ``version`` is
bumped (1 -> 2) in place. The browse service reads ``func.max(version)`` and the
assignment join is on ``library_entry_version == version``, so the re-curated set
becomes the live one. Order is delete-children -> bump-parent -> insert-children
so the operation is safe whether or not SQLite FK enforcement is on.

**Single source of truth (mirrors the Epic-C precedent
``3d7b9e357d52_recurate_seed_entries_ciii_a``).** New assignment values are read
from ``data/seed_control_library_entries.json`` and validated through
``ControlLibraryEntrySeed`` before insert, so DB and JSON converge by construction.

**No-hyphen ids (raw-text-seed UUID foot-gun, 4th recurrence).** New assignment
ids use ``uuid.uuid4().hex`` (32-char no-hyphen) to match the ``Uuid`` column
binding — see ``tests/migrations/test_control_library_uuid_format_fix.py``.

**Downgrade — no-op (policy, mirrors the Epic-C precedent).** The pre-curation
single-assignment payloads are recoverable from git history only; restoring them
inline would dual-source the data and invert the JSON-single-source guarantee.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3f9b21e60"
down_revision: str | Sequence[str] | None = "e14c75d22129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Pilot slugs (Task 5). Order is irrelevant — each is keyed by slug.
_PILOT_SLUGS = (
    "cloud-security-posture-management",
    "security-awareness-training",
    "data-loss-prevention",
    "data-backup-recovery",
    "data-classification-handling",
    "threat-modeling",
)


def _seed_path() -> Path:
    """Resolve data/seed_control_library_entries.json via the package root, with
    a __file__-relative fallback (mirrors d4f6a2b9c8e1._seed)."""
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
        e["slug"]: ControlLibraryEntrySeed.model_validate(e)
        for e in payload["entries"]
        if e["slug"] in _PILOT_SLUGS
    }
    missing = set(_PILOT_SLUGS) - set(by_slug)
    if missing:  # pragma: no cover - defensive: seed must contain every pilot slug
        raise RuntimeError(f"pilot slug(s) absent from seed JSON: {sorted(missing)}")

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

    for slug in _PILOT_SLUGS:
        seed = by_slug[slug]
        # Resolve the entry's current id + version (only ever 1 pre-recuration,
        # but read it rather than assume so a re-run lands cleanly).
        row = bind.execute(
            sa.text(
                "SELECT id, version FROM control_library_entries "
                "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
            ),
            {"slug": slug},
        ).first()
        if row is None:
            # Slug not present in this DB (e.g. a partial seed) — skip silently,
            # mirroring the Epic-C zero-row-UPDATE-is-silent policy.
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
        # 2. Bump the parent version in place (the field #438 keys on).
        bind.execute(
            sa.text(
                "UPDATE control_library_entries SET version = :nv, updated_at = :now "
                "WHERE id = :eid AND version = :v"
            ),
            {"nv": new_version, "eid": entry_id, "v": cur_version, "now": now},
        )
        # 3. Insert the enriched children at the new version.
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


def downgrade() -> None:
    """No-op — policy choice (mirrors 3d7b9e357d52). The pre-curation payloads are
    recoverable from git history only; restoring them inline would dual-source the
    data and invert the JSON-single-source-of-truth guarantee."""
    pass
