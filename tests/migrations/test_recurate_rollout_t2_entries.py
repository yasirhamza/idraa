"""Migration pinning: #437 rollout tranche 2 re-curation (b8d3f6a1c4e7) + the
tranche-2 crosswalk-seed extensions (c7e2a9b4f1d6).

Pins the faithful, per-value-cited avoidance/resistance (and DDOS detection+response)
channels the tranche-2 migration writes for the eight touched control-library entries,
the SCP vmc_corr_implementation mis-channel removal, the three REVIEWED crosswalk-seed
extensions that ground the added channels, and the ``version`` bump (#438 keys on it).
Runs the full chain through ``head`` so the test exercises the real migration stack.

Faithfulness anchor (rubric docs/reference/control-function-decomposition-rubric.md):
every added channel is grounded in a genuinely-applicable framework tag whose P2a
crosswalk supports the channel — the crosswalk gate (test_control_library_seed.py)
independently rejects grafts. HAOS/PEPL/SCP are grounded via REVIEWED crosswalk-seed
extensions (CIS 4.8 -> avoidance, CIS 14.2 -> resistance, CIS 16.1 -> resistance);
HAC/HASS/WAE/DDOS via genuine tags that already crosswalk; PWD via existing tags.
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
    "hardened-operating-system-services": {"lec_prev_avoidance"},
    "hardened-cloud": {"lec_prev_avoidance"},
    "hardened-saas-application": {"lec_prev_avoidance"},
    "wireless-access-authentication-encryption": {"lec_prev_resistance"},
    # DDOS gains a detection member (null-capability ELAPSED_TIME sentinel) + a
    # response member that together complete a det∧resp pair.
    "ddos-protection": {"lec_det_monitoring", "lec_resp_resilience"},
    "security-conscious-personnel": {"lec_prev_resistance"},
    "password-management-policies": {"lec_prev_resistance"},
    "secure-coding-practices": {"lec_prev_resistance"},
}

# Added channels whose capability is a bounded PROBABILITY scorer that MUST carry
# populated provenance <=0.8 (excludes DDOS's null-capability lec_det_monitoring).
_BOUNDED_SCORERS = {
    "hardened-operating-system-services": {"lec_prev_avoidance"},
    "hardened-cloud": {"lec_prev_avoidance"},
    "hardened-saas-application": {"lec_prev_avoidance"},
    "wireless-access-authentication-encryption": {"lec_prev_resistance"},
    "ddos-protection": {"lec_resp_resilience"},
    "security-conscious-personnel": {"lec_prev_resistance"},
    "password-management-policies": {"lec_prev_resistance"},
    "secure-coding-practices": {"lec_prev_resistance"},
}


def _rows(engine: Engine, slug: str) -> list:
    with engine.connect() as conn:
        return conn.execute(sa.text(_LATEST_ASSIGNMENTS), {"slug": slug}).all()


# Pre-existing channels that must SURVIVE the T2 enrichment (guards against accidental
# deletion during the DELETE-all/re-insert cycle the migration uses).
_ORIGINAL = {
    "hardened-operating-system-services": {"lec_prev_resistance"},
    "hardened-cloud": {"lec_prev_resistance"},
    "hardened-saas-application": {"lec_prev_resistance"},
    "wireless-access-authentication-encryption": {"lec_prev_avoidance"},
    "ddos-protection": {"lec_prev_resistance", "lec_det_visibility", "lec_det_recognition"},
}


def test_t2_entries_gain_added_channels(alembic_config: Config, alembic_engine: Engine) -> None:
    """Each tranche-2 entry gains its faithful direct scoring channel(s) AND retains
    its pre-existing channels (guards against accidental deletion by the migration)."""
    command.upgrade(alembic_config, "head")
    for slug, added in _ADDED.items():
        rows = _rows(alembic_engine, slug)
        assert rows, f"{slug}: no assignments joined to latest version — version-join broken"
        subs = {r[0] for r in rows}
        assert added <= subs, f"{slug}: missing added channel(s) {added - subs}"
    # Pre-existing channels must not have been accidentally deleted.
    for slug, orig in _ORIGINAL.items():
        rows = _rows(alembic_engine, slug)
        subs = {r[0] for r in rows}
        assert orig <= subs, f"{slug}: pre-existing channel(s) {orig - subs} accidentally deleted"


def test_t2_scoring_channels_have_provenance(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Every added BOUNDED scoring channel carries populated provenance (cited OR
    expert-estimate); bounded expert-estimate scorers <=0.8 are legitimate (§5.3).
    DDOS's lec_det_monitoring is a null-capability ELAPSED_TIME sentinel and is
    excluded from the provenance-required set (its capability_provenance is None)."""
    command.upgrade(alembic_config, "head")
    for slug, scorers in _BOUNDED_SCORERS.items():
        for r in _rows(alembic_engine, slug):
            if r[0] in scorers:
                assert r[1] in ("cited", "expert-estimate"), (
                    f"{slug}/{r[0]}: capability_provenance must be set, got {r[1]!r}"
                )
                assert r[2] in ("cited", "expert-estimate")
                assert r[3] in ("cited", "expert-estimate")
                if r[1] == "expert-estimate" and r[4] is not None:
                    assert r[4] <= 0.8, f"{slug}/{r[0]}: expert-estimate {r[4]} exceeds 0.8 ceiling"


def test_ddos_monitoring_is_null_sentinel(alembic_config: Config, alembic_engine: Engine) -> None:
    """DDOS's added lec_det_monitoring keeps a null capability (ELAPSED_TIME neutral
    sentinel, §5.3.1 — no expert-estimate permitted for natural-unit capability)."""
    command.upgrade(alembic_config, "head")
    mon = [r for r in _rows(alembic_engine, "ddos-protection") if r[0] == "lec_det_monitoring"]
    assert mon, "DDOS must carry lec_det_monitoring"
    assert mon[0][4] is None, "lec_det_monitoring capability must be the null sentinel"
    assert mon[0][1] is None, "null-capability channel must have no capability_provenance"


def test_scp_drops_vmc_corr_and_scores_via_resistance(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """SCP's mis-channeled vmc_corr_implementation is REMOVED (audit finding); it now
    scores via genuine LEC resistance and keeps dsc_prev_defined_expectations."""
    command.upgrade(alembic_config, "head")
    subs = {r[0] for r in _rows(alembic_engine, "secure-coding-practices")}
    assert "vmc_corr_implementation" not in subs, (
        "SCP must drop the mis-channeled vmc_corr_implementation"
    )
    assert "lec_prev_resistance" in subs, "SCP must score via lec_prev_resistance"
    assert "dsc_prev_defined_expectations" in subs, "SCP must keep the label-only DSC channel"


def test_t2_crosswalk_extensions_present(alembic_config: Config, alembic_engine: Engine) -> None:
    """The three REVIEWED crosswalk-seed extensions grounding the T2 channels exist in
    framework_control_faircam after the crosswalk-ext migration (c7e2a9b4f1d6)."""
    command.upgrade(alembic_config, "head")
    q = sa.text(
        "SELECT 1 FROM framework_control_faircam f "
        "JOIN framework_controls c ON c.id = f.framework_control_id "
        "WHERE c.framework = 'cis' AND c.framework_version = '8.0' "
        "  AND c.code = :code AND f.fair_cam_function = :fn"
    )
    with alembic_engine.connect() as conn:
        for code, fn in (
            ("4.8", "lec_prev_avoidance"),
            ("14.2", "lec_prev_resistance"),
            ("16.1", "lec_prev_resistance"),
        ):
            hit = conn.execute(q, {"code": code, "fn": fn}).scalar()
            assert hit == 1, f"missing crosswalk extension CIS {code} -> {fn}"


def test_t2_versions_bumped(alembic_config: Config, alembic_engine: Engine) -> None:
    """Each re-curated entry's version is bumped above 1 (the field #438 keys on)."""
    command.upgrade(alembic_config, "head")
    with alembic_engine.connect() as conn:
        for slug in _ADDED:
            ver = conn.execute(
                sa.text("SELECT MAX(version) FROM control_library_entries WHERE slug = :slug"),
                {"slug": slug},
            ).scalar_one()
            assert ver is not None and ver >= 2, f"{slug} version not bumped: {ver!r}"


def test_t2_assignment_ids_no_hyphen(alembic_config: Config, alembic_engine: Engine) -> None:
    """Re-inserted assignment ids are 32-char no-hyphen hex (raw-text-seed foot-gun)."""
    command.upgrade(alembic_config, "head")
    for slug in _ADDED:
        rows = _rows(alembic_engine, slug)
        bad = [r[5] for r in rows if len(r[5]) != 32 or "-" in r[5]]
        assert not bad, f"{slug}: re-curated id(s) not 32-char no-hyphen hex: {bad!r}"
