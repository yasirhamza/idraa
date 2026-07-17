"""Migration pinning: #437 rollout tranche 1 re-curation (d4fc657eb424).

Pins the faithful, per-value-cited direct scoring channels the tranche-1 migration
writes for the seven touched control-library entries, plus the SSPM score-basis
flip (removal of the vmc_prev_reduce_variance_prob assign-to-score) and the
``version`` bump (#438 keys on it). Runs the full chain through ``head`` so the
test exercises the real migration stack.

Faithfulness anchor (rubric docs/reference/control-function-decomposition-rubric.md):
every added channel is grounded in a genuinely-applicable framework tag whose P2a
crosswalk supports the channel — the crosswalk gate (test_control_library_seed.py)
independently rejects grafts. DT is intentionally excluded (no crosswalk grounding
for deception -> avoidance/deterrence) and is asserted to stay detection-only here.
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

# slug -> sub-functions the tranche must ADD (present after re-curation).
_ADDED = {
    "patch-management": {"lec_prev_resistance"},
    # MDM: dispensable lec_prev_avoidance channel dropped (#437 rollout T1 methodology
    # fix); scores via resistance alone, grounded by the genuine PR.AC-3.
    "mobile-device-management": {"lec_prev_resistance"},
    "security-configuration-assessment": {"lec_prev_avoidance", "lec_prev_resistance"},
    "saas-security-posture-management": {"lec_prev_avoidance", "lec_prev_resistance"},
    "endpoint-detection-response": {"lec_resp_resilience"},
    "host-intrusion-detection-prevention": {"lec_resp_resilience"},
    "network-detection-response": {"lec_resp_resilience"},
}


def _rows(engine: Engine, slug: str) -> list:
    with engine.connect() as conn:
        return conn.execute(sa.text(_LATEST_ASSIGNMENTS), {"slug": slug}).all()


def test_t1_entries_gain_added_channels(alembic_config: Config, alembic_engine: Engine) -> None:
    """Each tranche-1 entry gains its faithful direct scoring channel(s)."""
    command.upgrade(alembic_config, "head")
    for slug, added in _ADDED.items():
        rows = _rows(alembic_engine, slug)
        assert rows, f"{slug}: no assignments joined to latest version — version-join broken"
        subs = {r[0] for r in rows}
        assert added <= subs, f"{slug}: missing added channel(s) {added - subs}"


def test_t1_scoring_channels_have_provenance(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Every added bounded scoring channel carries populated provenance (cited OR
    expert-estimate); bounded expert-estimate scorers <=0.8 are legitimate (§5.3)."""
    command.upgrade(alembic_config, "head")
    for slug, added in _ADDED.items():
        for r in _rows(alembic_engine, slug):
            if r[0] in added:
                assert r[1] in ("cited", "expert-estimate"), (
                    f"{slug}/{r[0]}: capability_provenance must be set, got {r[1]!r}"
                )
                assert r[2] in ("cited", "expert-estimate")
                assert r[3] in ("cited", "expert-estimate")
                # bounded PROBABILITY expert-estimate ceiling (0.8).
                if r[1] == "expert-estimate" and r[4] is not None:
                    assert r[4] <= 0.8, f"{slug}/{r[0]}: expert-estimate {r[4]} exceeds 0.8 ceiling"


def test_sspm_score_basis_flip(alembic_config: Config, alembic_engine: Engine) -> None:
    """SSPM's I5 assign-to-score (vmc_prev_reduce_variance_prob) is REMOVED and the
    entry now scores via genuine LEC hardening instead."""
    command.upgrade(alembic_config, "head")
    subs = {r[0] for r in _rows(alembic_engine, "saas-security-posture-management")}
    assert "vmc_prev_reduce_variance_prob" not in subs, (
        "SSPM must drop the vmc_prev assign-to-score (score-basis flip)"
    )
    assert {"lec_prev_avoidance", "lec_prev_resistance"} <= subs, (
        "SSPM must score via genuine LEC prevention after the flip"
    )
    # the genuine meta pair is kept.
    assert {"vmc_id_control_monitoring", "vmc_corr_implementation"} <= subs


def test_dt_stays_detection_only(alembic_config: Config, alembic_engine: Engine) -> None:
    """Deception Technology is deliberately NOT re-curated: rubric §6.6 blesses
    deception -> avoidance/deterrence, but the P2a crosswalk has no grounding code,
    so grafting one would be assign-to-score. It stays detection-only (flagged)."""
    command.upgrade(alembic_config, "head")
    subs = {r[0] for r in _rows(alembic_engine, "deception-technology")}
    assert not any(s.startswith("lec_prev_") for s in subs), (
        "DT must not gain an ungrounded prevention scorer (crosswalk gap — flagged, not grafted)"
    )


def test_t1_versions_bumped(alembic_config: Config, alembic_engine: Engine) -> None:
    """Each re-curated entry's version is bumped above 1 (the field #438 keys on)."""
    command.upgrade(alembic_config, "head")
    with alembic_engine.connect() as conn:
        for slug in _ADDED:
            ver = conn.execute(
                sa.text("SELECT MAX(version) FROM control_library_entries WHERE slug = :slug"),
                {"slug": slug},
            ).scalar_one()
            assert ver is not None and ver >= 2, f"{slug} version not bumped: {ver!r}"


def test_t1_assignment_ids_no_hyphen(alembic_config: Config, alembic_engine: Engine) -> None:
    """Re-inserted assignment ids are 32-char no-hyphen hex (raw-text-seed foot-gun)."""
    command.upgrade(alembic_config, "head")
    for slug in _ADDED:
        rows = _rows(alembic_engine, slug)
        bad = [r[5] for r in rows if len(r[5]) != 32 or "-" in r[5]]
        assert not bad, f"{slug}: re-curated id(s) not 32-char no-hyphen hex: {bad!r}"
