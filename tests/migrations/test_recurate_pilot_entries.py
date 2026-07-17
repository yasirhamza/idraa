"""Migration pinning: #437 Task 5 pilot re-curation (a7c3f9b21e60).

Pins the faithful, multi-function, per-value-cited assignment sets the
re-curation migration writes for the six pilot control-library entries, and the
``version`` bump (the field #438 keys on). Runs the full chain through ``head``
so the test exercises the real migration stack, not a hand-built DB.

Methodology anchor (rubric ``docs/reference/control-function-decomposition-rubric.md``):

  - CSPM gains DIRECT asset hardening (``lec_prev_avoidance`` +
    ``lec_prev_resistance``) — it is avoidance-dominant (exposure/reachability
    findings, rubric §7.3) — plus a faithful, non-scoring ``vmc_corr_implementation``
    meta assignment (auto-remediation of drifted controls, rubric §4 Example 2).
    CSPM is the only pilot entry that moves $0 -> SCORING.
  - Security-awareness training stays genuinely META (DSC communication +
    control-monitoring). Its NIST PR.AT / CIS 14 homes ground only DSC/VMC in the
    crosswalk — FAIR-CAM treats awareness as decision-support, not asset hardening —
    so a ``lec_prev_*`` scorer is deliberately NOT grafted (rubric §6.4 / I5 guard).
  - CSPM scoring-channel capabilities are ``expert-estimate`` (MITRE ATT&CK technique
    pages ground the channel/mechanism, but publish only qualitative coverage text,
    not numeric population-reduction percentages). The capability_citations field
    carries the MITRE channel-grounding note for traceability (permitted for
    expert-estimate; only ``cited`` provenance requires a citation).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

_LATEST_ASSIGNMENTS = (
    "SELECT a.sub_function, a.capability_provenance, a.coverage_provenance, "
    "       a.reliability_provenance, a.capability_default, a.id, e.version "
    "FROM control_library_entry_assignments a "
    "JOIN control_library_entries e "
    "  ON e.id = a.library_entry_id AND e.version = a.library_entry_version "
    "WHERE e.slug = :slug"
)


def _rows(engine: Engine, slug: str) -> list:
    with engine.connect() as conn:
        return conn.execute(sa.text(_LATEST_ASSIGNMENTS), {"slug": slug}).all()


def test_cspm_gains_direct_prevention(alembic_config: Config, alembic_engine: Engine) -> None:
    command.upgrade(alembic_config, "head")
    rows = _rows(alembic_engine, "cloud-security-posture-management")
    assert rows, "no assignments joined to the latest CSPM version — version-join broken"

    subs = {r[0] for r in rows}
    # Avoidance-dominant direct asset hardening (rubric §7) — the genuine scorer.
    assert "lec_prev_avoidance" in subs
    assert "lec_prev_resistance" in subs
    # Faithful auto-remediation meta assignment (non-scoring alone — VMC pair 1+1 = $0).
    assert "vmc_corr_implementation" in subs

    # Scoring assignments carry populated provenance (cited OR expert-estimate);
    # a scoring entry does NOT require a cited value — bounded expert-estimate
    # scorers are legitimate (rubric §5.3, ceiling 0.8).
    scoring_subs = {"lec_prev_avoidance", "lec_prev_resistance"}
    for r in rows:
        if r[0] in scoring_subs:
            assert r[1] in ("cited", "expert-estimate"), (
                f"{r[0]}: capability_provenance must be cited or expert-estimate, got {r[1]!r}"
            )
            assert r[2] in ("cited", "expert-estimate"), (
                f"{r[0]}: coverage_provenance must be cited or expert-estimate, got {r[2]!r}"
            )
            assert r[3] in ("cited", "expert-estimate"), (
                f"{r[0]}: reliability_provenance must be cited or expert-estimate, got {r[3]!r}"
            )

    # vmc_corr_implementation is ELAPSED_TIME natural-unit with no cited remediation-time
    # figure -> capability must be null (non-scoring meta), never an uncited estimate.
    corr = next(r for r in rows if r[0] == "vmc_corr_implementation")
    assert corr[4] is None, (
        "vmc_corr_implementation capability must be null (natural-unit, uncited)"
    )
    assert corr[1] is None, "null-capability assignment must not carry a capability_provenance"


def test_sat_stays_meta_non_scoring(alembic_config: Config, alembic_engine: Engine) -> None:
    """SAT is a genuinely-meta control. Its framework homes (NIST PR.AT / CIS 14)
    ground only DSC + control-monitoring functions in the P2a crosswalk — FAIR-CAM
    treats awareness as decision-support, NOT asset hardening. The re-curation must
    NOT graft a direct lec_prev_* scorer onto it (rubric §6.4 / I5 invariant)."""
    command.upgrade(alembic_config, "head")
    rows = _rows(alembic_engine, "security-awareness-training")
    subs = {r[0] for r in rows}
    assert "dsc_prev_communication" in subs
    assert "vmc_id_control_monitoring" in subs  # phishing-sim = human-control monitoring
    assert not any(s.startswith("lec_prev_") for s in subs), (
        "SAT must not graft a direct LEC-prevention scorer — awareness is DSC, "
        "not asset hardening (assign-to-score / I5 guard)."
    )


def test_pilot_versions_bumped(alembic_config: Config, alembic_engine: Engine) -> None:
    """Each pilot entry's version is bumped above 1 (the field #438 keys on)."""
    command.upgrade(alembic_config, "head")
    pilot = (
        "cloud-security-posture-management",
        "security-awareness-training",
        "data-loss-prevention",
        "data-backup-recovery",
        "data-classification-handling",
        "threat-modeling",
    )
    with alembic_engine.connect() as conn:
        for slug in pilot:
            ver = conn.execute(
                sa.text("SELECT MAX(version) FROM control_library_entries WHERE slug = :slug"),
                {"slug": slug},
            ).scalar_one()
            assert ver is not None and ver >= 2, f"{slug} version not bumped: {ver!r}"


def test_recurated_assignment_ids_no_hyphen(alembic_config: Config, alembic_engine: Engine) -> None:
    """Re-inserted assignment ids are 32-char no-hyphen hex (raw-text-seed foot-gun)."""
    command.upgrade(alembic_config, "head")
    rows = _rows(alembic_engine, "cloud-security-posture-management")
    bad = [r[5] for r in rows if len(r[5]) != 32 or "-" in r[5]]
    assert not bad, f"re-curated assignment id(s) not 32-char no-hyphen hex: {bad!r}"
