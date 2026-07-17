"""Re-curate rollout tranche 3 control-library entries (#437 rollout, T3).

Revision ID: a3f7c1e9b2d4
Revises: b8d3f6a1c4e7
Create Date: 2026-07-01

Tranche-3 is the INVERSE of tranches 1-2: instead of ADDING faithful scoring
channels, it REMOVES mis-authored / non-faithful channels (assign-to-score
boilerplate) under the #437 function-decomposition methodology gate. Five entries
shed channels that are currently $0 or double-score a kept channel; NO entry loses
a legitimate score (each keeps its genuine scoring channel, confirmed empirically via
services/control_library_scoring.classify_entry — see the T3 report). The reduced
assignment sets are read from ``data/seed_control_library_entries.json`` and validated
through ``ControlLibraryEntrySeed`` before insert, so DB and JSON converge by
construction (single-source-of-truth).

Removals (why non-faithful — rubric docs/reference/control-function-decomposition-rubric.md):

  data-at-rest-encryption (DRE)  -vmc_id_control_monitoring, -vmc_corr_implementation,
      -dsc_prev_defined_expectations. VMC pair = non-faithful VMC-on-self boilerplate:
      encryption "monitoring/auditing" is self-operation, not identification/correction
      of a SEPARATE degraded control (§4-I1); partial pair (corr null-capability) = $0.
      DSC = 1-of-9 label-only, encryption is not decision-support (§2.7). KEEPS the
      genuine lec_prev_resistance (scores) AND lec_resp_loss_reduction (genuine
      safe-harbor currency channel, null-capability $0 pending a T4 cited figure — NOT
      removed, NOT valued). DRE still SCORES via resistance.
  data-in-transit-encryption (DTE)  -vmc_id_control_monitoring, -vmc_corr_implementation.
      Same non-faithful VMC-on-self boilerplate (partial pair = $0). KEEPS lec_prev_
      resistance. DTE still SCORES via resistance.
  network-access-control (NAC)  -vmc_prev_reduce_variance_prob. Double-scorer: NAC
      posture-gates ACCESS (that behavior is lec_prev_avoidance, already assigned +
      scoring); it does not reduce a SEPARATE control's change/variance frequency. No
      distinct change-frequency home in the crosswalk. KEEPS lec_prev_avoidance. NAC
      still SCORES via avoidance.
  user-access-control (UAC)  -vmc_prev_reduce_variance_prob. Double-scorer (borderline
      I5): the "entitlement drift" is UAC's OWN entitlements, not a separate control's
      variance; double-counts the access-restriction the kept lec_prev_resistance
      captures. KEEPS lec_prev_resistance (scores) + vmc_id_control_monitoring +
      vmc_corr_implementation (genuine partial meta pair, $0, faithful) +
      dsc_prev_defined_expectations (harmless label-only). UAC still SCORES via resistance.
  cyber-risk-quantification-management (CRQM)  -vmc_corr_implementation. Mis-channel:
      CRQM SELECTS/PRIORITIZES treatments (decision-support = DSC), it does not IMPLEMENT
      corrections (vmc_corr_implementation = ELAPSED_TIME time-to-restore-a-control).
      KEEPS the genuine DSC triad (reporting/analysis/defined_expectations). CRQM stays
      NON-SCORING (genuinely-meta residual, disposition B, pending #439) — a faithfulness
      cleanup, not a scoring change.

Each removal is recorded in ``_meta.claim_drops`` (slug + dropped list + reason), with
no duplicate (slug, item) pair (the seed validator rejects those). The crosswalk gate
(tests/**/test_control_library_seed.py) only tightens under removals (fewer claims
cannot over-claim), so it stays green by construction.

Version bump (the field #438 keys on): each touched entry's ``version`` is bumped in
place; order is delete-children -> bump-parent -> insert-children so the op is safe with
or without SQLite FK enforcement. No-hyphen ids via ``uuid.uuid4().hex`` (raw-text-seed
UUID foot-gun). Downgrade is a no-op (policy, mirrors the pilot/T1/T2): pre-curation
payloads are recoverable from git history only; restoring inline would dual-source the
data.
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
revision: str = "a3f7c1e9b2d4"
down_revision: str | Sequence[str] | None = "b8d3f6a1c4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tranche-3 slugs that SHED a non-faithful / double-scoring channel (all still keep a
# genuine scoring channel except CRQM, which is a genuinely-meta residual by design).
_T3_SLUGS = (
    "data-at-rest-encryption",
    "data-in-transit-encryption",
    "network-access-control",
    "user-access-control",
    "cyber-risk-quantification-management",
)


def _seed_path() -> Path:
    """Resolve data/seed_control_library_entries.json via the package root, with a
    __file__-relative fallback (mirrors b8d3f6a1c4e7._seed_path)."""
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
        if e["slug"] in _T3_SLUGS
    }
    missing = set(_T3_SLUGS) - set(by_slug)
    if missing:  # pragma: no cover - defensive: seed must contain every T3 slug
        raise RuntimeError(f"tranche-3 slug(s) absent from seed JSON: {sorted(missing)}")

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

    for slug in _T3_SLUGS:
        seed = by_slug[slug]
        row = bind.execute(
            sa.text(
                "SELECT id, version FROM control_library_entries "
                "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
            ),
            {"slug": slug},
        ).first()
        if row is None:
            # Slug not present in this DB (partial seed) — skip silently (pilot policy).
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
        # 3. Insert the CLEANED-UP children (non-faithful channels absent) at new version.
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
    """No-op — policy choice (mirrors a7c3f9b21e60 / d4fc657eb424 / b8d3f6a1c4e7).
    Pre-curation payloads are recoverable from git history only; restoring them inline
    would dual-source the data and invert the JSON-single-source-of-truth guarantee."""
    pass
