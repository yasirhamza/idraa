"""Coverage service tests (issue #475 T13). Reference-driven per the dashboard
spec: references come from the seeded catalog tables, never literals."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import ScenarioAttackMapping
from idraa.models.enums import EntityStatus
from idraa.services.attack_coverage import (
    build_attack_coverage,
    build_attack_coverage_summary,
    coverage,
)
from tests.models.test_attack_models import _tactic, _technique


# Reconciliation note (#475 covenant): this module used to define its own
# CoverageResult/coverage() (ratio=None on empty reference, missing sorted
# internally). The dashboard redesign (#477) merged first and shipped the
# ONE shared primitive at idraa.services.coverage; this module now
# imports it (see attack_coverage.py's module docstring). The shared
# helper's own contract — ratio == 0.0 (never None) on empty reference,
# missing/present preserve REFERENCE order, dedup, out-of-reference items
# in `covered` are ignored — is already pinned by the dashboard's own
# tests/unit/test_coverage.py (test_coverage_ratio_missing_and_present,
# test_coverage_empty_reference_is_zero_not_div0,
# test_coverage_dedups_and_is_order_stable), so the old
# test_coverage_helper_contract / test_coverage_empty_reference tests here
# would just duplicate that coverage — removed rather than re-pinned.
#
# What's still ATT&CK-specific: the shared helper does NOT sort internally
# (it preserves reference first-seen/insertion order), whereas our old local
# coverage() sorted `missing` for us. Every call site in attack_coverage.py
# now passes `sorted(reference)` explicitly to keep `missing`/`present`
# deterministic — verified below.
def test_call_site_must_presort_reference_for_deterministic_missing():
    r_unsorted = coverage(["b", "a"], set())
    assert r_unsorted.missing == ["b", "a"]  # NOT sorted by the shared helper
    r_sorted = coverage(sorted(["b", "a"]), set())
    assert r_sorted.missing == ["a", "b"]  # sorting is the CALLER's job
    assert r_sorted.ratio == 0.0  # shared semantics: never None, even empty covered
    assert r_sorted.present == []


@pytest.mark.asyncio
async def test_build_attack_coverage_rollup(db_session: AsyncSession, scenario_factory):
    # Catalog: 1 enterprise tactic with 2 techniques; 1 ics tactic with 1 technique.
    ta_ent = _tactic()
    ta_ics = _tactic(
        domain="ics",
        tactic_id="TA0108",
        shortname="impair-process-control",
        name="Impair Process Control",
        display_order=0,
    )
    t1 = _technique()  # enterprise, covered
    t2 = _technique(
        technique_id="T1486", name="Data Encrypted for Impact", tactics=["initial-access"]
    )  # enterprise, NOT covered
    t3 = _technique(
        domain="ics",
        technique_id="T0836",
        name="Modify Parameter",
        tactics=["impair-process-control"],
    )  # ics, NOT covered
    dead = _technique(technique_id="T9999", deprecated=True)  # excluded from reference
    db_session.add_all([ta_ent, ta_ics, t1, t2, t3, dead])
    scenario = await scenario_factory()  # must be ACTIVE
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=t1.id,
            source="user",
        )
    )
    await db_session.flush()

    vm = await build_attack_coverage(db_session, organization_id=scenario.organization_id)
    by_domain = {d.domain: d for d in vm.domains}
    assert set(by_domain) == {"enterprise", "ics"}
    ent = by_domain["enterprise"]
    assert ent.overall.reference_count == 2  # deprecated excluded
    assert ent.overall.covered_count == 1
    tactic = ent.tactics[0]
    techs = {t.technique_id: t for t in tactic.techniques}
    assert techs["T1566"].covered and scenario.name in techs["T1566"].scenario_names
    assert not techs["T1486"].covered
    assert by_domain["ics"].overall.covered_count == 0
    assert vm.mapped_scenario_count == 1


@pytest.mark.asyncio
async def test_coverage_is_org_scoped(db_session: AsyncSession, scenario_factory):
    """Another org's mappings don't count. Builds a second org + scenario +
    mapping locally, mirroring existing org-scoping test patterns
    (tests/services/test_controls_maintenance.py's org_a/org_b idiom)."""
    from tests.factories import create_org, create_user

    ta_ent = _tactic()
    tech = _technique()
    db_session.add_all([ta_ent, tech])
    scenario = await scenario_factory()  # org 1 (seed_organization), ACTIVE
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=tech.id,
            source="user",
        )
    )
    await db_session.flush()

    # Second org with its own scenario + mapping to the SAME technique.
    other_org = await create_org(db_session, name="Other Org")
    other_user = await create_user(db_session, other_org, email="other@test.local")
    other_scenario = await scenario_factory(
        name="other-org-scenario",
        organization_id=other_org.id,
        created_by=other_user.id,
    )
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=other_org.id,
            scenario_id=other_scenario.id,
            technique_id=tech.id,
            source="user",
        )
    )
    await db_session.flush()

    vm = await build_attack_coverage(db_session, organization_id=scenario.organization_id)
    ent = next(d for d in vm.domains if d.domain == "enterprise")
    assert ent.overall.covered_count == 1
    assert vm.mapped_scenario_count == 1
    techs = {t.technique_id: t for t in ent.tactics[0].techniques}
    assert techs["T1566"].scenario_names == [scenario.name]
    assert other_scenario.name not in techs["T1566"].scenario_names


@pytest.mark.asyncio
async def test_same_technique_id_in_two_domains_does_not_cross_cover(
    db_session: AsyncSession, scenario_factory
):
    """Meth-I1/Arch-I1: (domain, technique_id) is the identity. Mapping the
    ENTERPRISE T1566 row must not mark a same-ID ICS row covered."""
    db_session.add_all(
        [
            _tactic(),
            _tactic(
                domain="ics",
                tactic_id="TA0108",
                shortname="impair-process-control",
                name="Impair Process Control",
                display_order=0,
            ),
        ]
    )
    ent = _technique()  # enterprise T1566
    ics_same_id = _technique(domain="ics", tactics=["impair-process-control"])  # ics T1566
    db_session.add_all([ent, ics_same_id])
    scenario = await scenario_factory()
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=ent.id,
            source="user",
        )
    )
    await db_session.flush()

    vm = await build_attack_coverage(db_session, organization_id=scenario.organization_id)
    by_domain = {d.domain: d for d in vm.domains}
    assert by_domain["enterprise"].overall.covered_count == 1
    assert by_domain["ics"].overall.covered_count == 0  # no cross-domain bleed
    assert by_domain["ics"].overall.missing == ["T1566"]


@pytest.mark.asyncio
async def test_non_active_scenario_mapping_does_not_count(
    db_session: AsyncSession, scenario_factory
):
    """SC-N2: coverage is scoped to ACTIVE scenarios only. A DRAFT scenario's
    mapping must contribute nothing to covered counts, scenario_names, or
    mapped_scenario_count — draft scenarios are not yet "modeled"."""
    from idraa.models.enums import EntityStatus

    ta_ent = _tactic()
    tech = _technique()
    db_session.add_all([ta_ent, tech])
    draft_scenario = await scenario_factory(status=EntityStatus.DRAFT)
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=draft_scenario.organization_id,
            scenario_id=draft_scenario.id,
            technique_id=tech.id,
            source="user",
        )
    )
    await db_session.flush()

    vm = await build_attack_coverage(db_session, organization_id=draft_scenario.organization_id)
    ent = next(d for d in vm.domains if d.domain == "enterprise")
    assert ent.overall.covered_count == 0
    assert ent.overall.missing == ["T1566"]
    assert vm.mapped_scenario_count == 0
    techs = {t.technique_id: t for t in ent.tactics[0].techniques}
    assert not techs["T1566"].covered
    assert techs["T1566"].scenario_names == []


@pytest.mark.asyncio
async def test_tactic_rollup_ordering_and_name_cap(db_session: AsyncSession, scenario_factory):
    """SC-N4: tactics roll up in display_order (2 per domain); Arch-I6: names cap at 3."""
    db_session.add_all(
        [
            _tactic(tactic_id="TA0040", shortname="impact", name="Impact", display_order=1),
            _tactic(),  # initial-access, display_order=0 — must sort FIRST despite insert order
        ]
    )
    tech = _technique(tactics=["initial-access", "impact"])
    db_session.add(tech)
    names = []
    for _i in range(5):
        s = await scenario_factory()
        names.append(s.name)
        await db_session.flush()
        db_session.add(
            ScenarioAttackMapping(
                organization_id=s.organization_id,
                scenario_id=s.id,
                technique_id=tech.id,
                source="user",
            )
        )
    await db_session.flush()

    vm = await build_attack_coverage(db_session, organization_id=s.organization_id)
    ent = next(d for d in vm.domains if d.domain == "enterprise")
    assert [t.shortname for t in ent.tactics] == ["initial-access", "impact"]
    row = ent.tactics[0].techniques[0]
    assert len(row.scenario_names) == 3
    assert row.scenario_overflow_count == 2
    # NOTE: all 5 scenarios must be in the SAME org for this to hold — make
    # scenario_factory reuse one org per test (or pass the org explicitly).


@pytest.mark.asyncio
async def test_unmapped_pinned_count_ignores_custom_scenarios(
    db_session: AsyncSession, scenario_factory
):
    """Arch2-I3: library_pin=None is stored as JSON text 'null' (not SQL NULL),
    so a SQL is_not(None) filter would count every custom scenario. An unmapped
    EXPERT scenario (no pin) must contribute 0 to the partial-curation banner."""
    scenario = await scenario_factory()  # expert scenario: library_pin=None
    assert scenario.library_pin is None
    await db_session.flush()
    vm = await build_attack_coverage(db_session, organization_id=scenario.organization_id)
    assert vm.unmapped_pinned_scenario_count == 0

    # A pinned-but-unmapped scenario DOES count.
    pinned = await scenario_factory()
    pinned.library_pin = {"entry_id": str(uuid.uuid4()), "version": 1}
    await db_session.flush()
    vm = await build_attack_coverage(db_session, organization_id=pinned.organization_id)
    assert vm.unmapped_pinned_scenario_count == 1


def test_no_hardcoded_reference_literals():
    """Dashboard-spec acceptance rule: no technique/tactic literals in the
    view-model or the coverage/scenario templates.

    SC-N3: this enforces ID literals (T####/TA####) only — a hardcoded tactic
    NAME list ("Initial Access", ...) is not grep-safe and is left to review.
    Paths anchored via idraa.__file__ (CWD-independent), matching the
    sibling seed-test idiom."""
    import idraa

    src_root = Path(idraa.__file__).resolve().parent
    roots = [
        src_root / "services" / "attack_coverage.py",
        src_root / "templates" / "scenarios" / "attack_coverage.html",
        src_root / "templates" / "scenarios" / "_attack_mapping_row.html",
    ]
    pattern = re.compile(r"\bT[A]?\d{4}\b")
    for path in roots:
        assert path.exists(), f"{path} moved/renamed — reference-driven guard must follow it"
        hits = pattern.findall(path.read_text(encoding="utf-8"))
        assert hits == [], f"{path}: hardcoded ATT&CK ids {hits}"


@pytest.mark.asyncio
async def test_summary_tactic_rollup_and_domain_order(db_session: AsyncSession, scenario_factory):
    """Tactic covered = >=1 mapped non-deprecated technique; enterprise first."""
    ta1 = _tactic()  # enterprise TA0001 initial-access, display_order=0
    ta2 = _tactic(tactic_id="TA0002", shortname="execution", name="Execution", display_order=1)
    ta_ics = _tactic(
        domain="ics",
        tactic_id="TA0108",
        shortname="impair-process-control",
        name="Impair Process Control",
        display_order=0,
    )
    t1 = _technique()  # enterprise T1566, tactics=["initial-access"] -> will be mapped
    t2 = _technique(technique_id="T1059", name="Command and Scripting", tactics=["execution"])
    t3 = _technique(
        domain="ics",
        technique_id="T0836",
        name="Modify Parameter",
        tactics=["impair-process-control"],
    )
    db_session.add_all([ta1, ta2, ta_ics, t1, t2, t3])
    scenario = await scenario_factory()
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=t1.id,
            source="user",
        )
    )
    await db_session.flush()

    summaries = await build_attack_coverage_summary(
        db_session, organization_id=scenario.organization_id
    )
    assert [s.domain for s in summaries] == ["enterprise", "ics"]
    ent, ics = summaries
    assert ent.label == "Enterprise"
    assert ent.tactic_result.reference_count == 2
    assert ent.tactic_result.covered_count == 1
    assert ent.tactic_result.present == ["TA0001"]  # display_order preserved
    assert ent.tactic_result.missing == ["TA0002"]
    assert ent.technique_count_mapped == 1
    assert ics.tactic_result.covered_count == 0
    assert ics.tactic_result.reference_count == 1
    assert ics.technique_count_mapped == 0


@pytest.mark.asyncio
async def test_summary_deprecated_and_inactive_do_not_count(
    db_session: AsyncSession, scenario_factory
):
    """A mapping to a deprecated technique, or from a non-ACTIVE scenario,
    never flips a tactic to covered."""
    ta = _tactic()
    dead = _technique(technique_id="T9999", deprecated=True)  # tactics=["initial-access"]
    live = _technique()  # T1566
    db_session.add_all([ta, dead, live])
    s_active = await scenario_factory()
    s_draft = await scenario_factory(status=EntityStatus.DRAFT)
    await db_session.flush()
    db_session.add_all(
        [
            ScenarioAttackMapping(  # deprecated technique on an ACTIVE scenario
                organization_id=s_active.organization_id,
                scenario_id=s_active.id,
                technique_id=dead.id,
                source="user",
            ),
            ScenarioAttackMapping(  # live technique but DRAFT scenario
                organization_id=s_draft.organization_id,
                scenario_id=s_draft.id,
                technique_id=live.id,
                source="user",
            ),
        ]
    )
    await db_session.flush()

    summaries = await build_attack_coverage_summary(
        db_session, organization_id=s_active.organization_id
    )
    (ent,) = summaries
    assert ent.tactic_result.covered_count == 0
    assert ent.technique_count_mapped == 0


@pytest.mark.asyncio
async def test_summary_per_domain_uuid_identity(db_session: AsyncSession, scenario_factory):
    """Same technique_id string in both domains: mapping the ENTERPRISE row
    must not cover the ICS tactic (identity is the catalog-row UUID)."""
    ta_ent = _tactic()
    ta_ics = _tactic(
        domain="ics",
        tactic_id="TA0108",
        shortname="impair-process-control",
        name="Impair Process Control",
        display_order=0,
    )
    t_ent = _technique(technique_id="T0001", tactics=["initial-access"])
    t_ics = _technique(
        domain="ics",
        technique_id="T0001",
        name="Same ID, ICS",
        tactics=["impair-process-control"],
    )
    db_session.add_all([ta_ent, ta_ics, t_ent, t_ics])
    scenario = await scenario_factory()
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=t_ent.id,  # ENTERPRISE catalog row
            source="user",
        )
    )
    await db_session.flush()

    summaries = await build_attack_coverage_summary(
        db_session, organization_id=scenario.organization_id
    )
    by_domain = {s.domain: s for s in summaries}
    assert by_domain["enterprise"].tactic_result.covered_count == 1
    assert by_domain["ics"].tactic_result.covered_count == 0
    assert by_domain["ics"].technique_count_mapped == 0


@pytest.mark.asyncio
async def test_summary_empty_catalog_returns_empty_list(
    db_session: AsyncSession, seed_organization
):
    """No catalog rows (pre-migration DB): [] — the template hides the block."""
    assert (
        await build_attack_coverage_summary(db_session, organization_id=seed_organization.id) == []
    )
