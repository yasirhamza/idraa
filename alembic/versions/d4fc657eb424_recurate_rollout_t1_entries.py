"""Re-curate rollout tranche 1 control-library entries (#437 rollout, T1).

Revision ID: d4fc657eb424
Revises: b4e1f2a09c53
Create Date: 2026-07-01

Tranche-1 re-curation under the #437 function-decomposition methodology gate,
mirroring the pilot (a7c3f9b21e60). SEVEN entries gain faithful, per-value-cited
direct scoring channels; DT is intentionally excluded (see below). New assignment
values are read from ``data/seed_control_library_entries.json`` and validated
through ``ControlLibraryEntrySeed`` before insert, so DB and JSON converge by
construction (single-source-of-truth, mirrors the pilot + Epic-C precedent).

Each channel is grounded in a GENUINELY-APPLICABLE framework tag added to the
entry (the same faithful pattern the pilot used for CSPM — it added PR.AC-5 /
PR.DS-1 / CIS 3.3 to ground avoidance/resistance). The P2a NIST/CIS→FAIR-CAM
crosswalk gate (tests/**/test_control_library_seed.py) validates every added
channel is grounded — a channel with no genuinely-applicable grounding tag is a
graft and is NOT added (that is why DT is excluded and PMGT's grounding is flagged).

  patch-management (PMGT)          +lec_prev_resistance. Grounded by CIS 7.3/7.4 (PMGT's GENUINE
                                   patch-management homes) via the REVIEWED RiskFlow crosswalk-seed
                                   extension mapping 7.3/7.4 -> lec_prev_resistance (rubric §4-I1 Ex3
                                   deliberately supersedes the FAIR-Institute patch=VMC reading;
                                   #437 rollout T1). The prior CIS 4.6 tag-to-score graft is REMOVED.
                                   $0 under-authored -> SCORING.
  mobile-device-management (MDM)   +lec_prev_resistance (device hardening; grounded by existing
                                   PR.AC-3). A dispensable secondary lec_prev_avoidance channel
                                   (borderline CIS 3.12 grounding) was DROPPED and its 3.12 tag
                                   removed — MDM scores via resistance alone. residual -> SCORING.
  security-configuration-          +lec_prev_avoidance (primary) +lec_prev_resistance. CSPM analog
    assessment (SCA)               (user confirmed auto-remediation). Grounding PR.PT-3 (least
                                   functionality) + CIS 3.3 (data ACLs). $0 under-authored -> SCORING.
  saas-security-posture-           REMOVE vmc_prev_reduce_variance_prob (I5 assign-to-score);
    management (SSPM)              +lec_prev_avoidance +lec_prev_resistance (grounding CIS 3.3 +
                                   existing PR.PT-3). SCORE-BASIS FLIP: scored ONLY via vmc_prev
                                   before -> now scores via genuine LEC hardening.
  endpoint-detection-response      +lec_resp_resilience (completes det+resp pair). Grounding
    (EDR)                          RS.MI-2 (Incidents are mitigated). Was already SCORING via
                                   lec_prev_resistance; response is enrichment.
  host-intrusion-detection-        +lec_resp_resilience (completes det+resp pair). Grounding
    prevention (HIDS)              RS.MI-2. Was already SCORING via lec_prev_resistance.
  network-detection-response       +lec_resp_resilience (completes det+resp pair). Grounding
    (NDR)                          RS.MI-2. $0 residual -> SCORING.

The PMGT crosswalk-seed extension (CIS 7.3/7.4 -> lec_prev_resistance) lands in a
SEPARATE migration (f1a2b3c4d5e6) so the P2a crosswalk and the P2b library stay in
distinct migrations; this migration only reshapes control_library_entries.

DT (deception-technology) is deliberately NOT re-curated: rubric §6.6 blesses
deception -> avoidance/deterrence, but the P2a crosswalk has NO code grounding
deception's avoidance/deterrence, and no genuinely-applicable framework tag
exists (decoys are not segmentation / data-leak protection). Grafting an unrelated
tag would be assign-to-score. DT stays non-scoring residual; unlocking it is
DEFERRED to a separate REVIEWED crosswalk-seed extension for deception, pending
methodology sign-off on whether deception genuinely delivers avoidance/deterrence
(a genuine crosswalk-coverage gap in NIST CSF 1.1 / CIS 8.0, not a faithful-channel
rejection).

Version bump (the field #438 keys on): each touched entry's ``version`` is bumped
in place; order is delete-children -> bump-parent -> insert-children so the op is
safe with or without SQLite FK enforcement. No-hyphen ids via ``uuid.uuid4().hex``
(raw-text-seed UUID foot-gun, 5th recurrence). Downgrade is a no-op (policy,
mirrors the pilot): pre-curation payloads are recoverable from git history only;
restoring inline would dual-source the data.
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
revision: str = "d4fc657eb424"
down_revision: str | Sequence[str] | None = "b4e1f2a09c53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tranche-1 slugs that gain a genuine scoring channel (DT is excluded — flagged).
_T1_SLUGS = (
    "patch-management",
    "mobile-device-management",
    "security-configuration-assessment",
    "saas-security-posture-management",
    "endpoint-detection-response",
    "host-intrusion-detection-prevention",
    "network-detection-response",
)


def _seed_path() -> Path:
    """Resolve data/seed_control_library_entries.json via the package root, with a
    __file__-relative fallback (mirrors a7c3f9b21e60._seed_path)."""
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
        if e["slug"] in _T1_SLUGS
    }
    missing = set(_T1_SLUGS) - set(by_slug)
    if missing:  # pragma: no cover - defensive: seed must contain every T1 slug
        raise RuntimeError(f"tranche-1 slug(s) absent from seed JSON: {sorted(missing)}")

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

    for slug in _T1_SLUGS:
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
    """No-op — policy choice (mirrors a7c3f9b21e60 / 3d7b9e357d52). Pre-curation
    payloads are recoverable from git history only; restoring them inline would
    dual-source the data and invert the JSON-single-source-of-truth guarantee."""
    pass
