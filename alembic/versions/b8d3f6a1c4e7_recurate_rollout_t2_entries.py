"""Re-curate rollout tranche 2 control-library entries (#437 rollout, T2).

Revision ID: b8d3f6a1c4e7
Revises: c7e2a9b4f1d6
Create Date: 2026-07-01

Tranche-2 re-curation under the #437 function-decomposition methodology gate,
mirroring the pilot (a7c3f9b21e60) and tranche 1 (d4fc657eb424). EIGHT entries gain
faithful, per-value-cited avoidance/resistance (and, for DDOS, detection+response)
channels; SCP additionally DROPS a mis-channeled ``vmc_corr_implementation``. New
assignment values are read from ``data/seed_control_library_entries.json`` and
validated through ``ControlLibraryEntrySeed`` before insert, so DB and JSON converge
by construction (single-source-of-truth).

Each added channel is grounded in a GENUINELY-APPLICABLE framework tag whose P2a
crosswalk supports it — either an existing/added genuine tag that already crosswalks,
or a REVIEWED RiskFlow crosswalk-seed extension (landed in c7e2a9b4f1d6). The P2a
crosswalk gate (tests/**/test_control_library_seed.py) independently rejects grafts.

  hardened-operating-system-services (HAOS)  +lec_prev_avoidance. Surface removal
      (disable unnecessary services) → removing contact (rubric §4-I1). Grounded by
      the genuine, already-tagged CIS 4.8 via the REVIEWED extension 4.8 ->
      lec_prev_avoidance (c7e2a9b4f1d6). $0-adjacent under-authored -> richer SCORING.
  hardened-cloud (HAC)  +lec_prev_avoidance. Removes public cloud exposure (block
      public storage / restrict SG ingress). Grounded by genuine added tags PR.AC-5 +
      CIS 3.3 (already crosswalk to avoidance; no extension). CSPM analog (rubric §7.3).
  hardened-saas-application (HASS)  +lec_prev_avoidance. Restricts external SaaS
      sharing/access. Grounded by genuine added CIS 3.3 (already crosswalks; the CIS
      4.8 -> avoidance extension also grounds it redundantly). Mirrors SSPM/SCA.
  wireless-access-authentication-encryption (WAE)  +lec_prev_resistance. The
      "AND Encryption" half — WPA2/WPA3 reduces on-path read/manipulation
      probability (Vulnerability). Grounded by genuine added PR.DS-2 (data-in-transit).
  ddos-protection (DDOS)  +lec_det_monitoring (null-capability ELAPSED_TIME sentinel,
      grounded by existing DE.CM-1) + lec_resp_resilience (real-time scrubbing
      preserves availability; grounded by added RS.MI-2). Completes a det∧resp pair on
      top of existing resistance. Already SCORING; this is enrichment.
  security-conscious-personnel (PEPL)  +lec_prev_resistance. Trained users resist
      phishing/social-engineering (Vulnerability). Grounded by genuine added CIS 14.2
      via the REVIEWED extension 14.2 -> lec_prev_resistance (c7e2a9b4f1d6). Capped
      CONSERVATIVELY (leaky human vector, cap=0.5). under-authored -> SCORING.
  password-management-policies (PWD)  +lec_prev_resistance. Enforced complexity/
      rotation/hashing reduce credential-attack success (Vulnerability). Grounded by
      existing CIS 5.2 + PR.AC-1 (no extension). under-authored -> SCORING.
  secure-coding-practices (SCP)  +lec_prev_resistance and REMOVE vmc_corr_implementation
      (audit mis-channel: SCP hardens its OWN code = LEC Resistance per rubric §4-I1
      Ex3, not correction of a separate control). Resistance grounded by CIS 16.1 via
      the REVIEWED extension 16.1 -> lec_prev_resistance (c7e2a9b4f1d6);
      dsc_prev_defined_expectations retained label-only.
      non-scoring-residual -> SCORING.

Version bump (the field #438 keys on): each touched entry's ``version`` is bumped in
place; order is delete-children -> bump-parent -> insert-children so the op is safe
with or without SQLite FK enforcement. No-hyphen ids via ``uuid.uuid4().hex``
(raw-text-seed UUID foot-gun). Downgrade is a no-op (policy, mirrors the pilot/T1):
pre-curation payloads are recoverable from git history only; restoring inline would
dual-source the data.
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
revision: str = "b8d3f6a1c4e7"
down_revision: str | Sequence[str] | None = "c7e2a9b4f1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tranche-2 slugs that gain a genuine scoring channel (SCP also drops a mis-channel).
_T2_SLUGS = (
    "hardened-operating-system-services",
    "hardened-cloud",
    "hardened-saas-application",
    "wireless-access-authentication-encryption",
    "ddos-protection",
    "security-conscious-personnel",
    "password-management-policies",
    "secure-coding-practices",
)


def _seed_path() -> Path:
    """Resolve data/seed_control_library_entries.json via the package root, with a
    __file__-relative fallback (mirrors d4fc657eb424._seed_path)."""
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
        if e["slug"] in _T2_SLUGS
    }
    missing = set(_T2_SLUGS) - set(by_slug)
    if missing:  # pragma: no cover - defensive: seed must contain every T2 slug
        raise RuntimeError(f"tranche-2 slug(s) absent from seed JSON: {sorted(missing)}")

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

    for slug in _T2_SLUGS:
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
    """No-op — policy choice (mirrors a7c3f9b21e60 / d4fc657eb424). Pre-curation
    payloads are recoverable from git history only; restoring them inline would
    dual-source the data and invert the JSON-single-source-of-truth guarantee."""
    pass
