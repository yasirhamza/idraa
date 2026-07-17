"""Repro for the wizard library-prefill bug (#wizard-library-prefill).

A scenario created from a library entry finalizes with the threat-BLIND IRIS
industry baseline, NOT the entry's curated distributions: finalize is sourced
from ``state.sme_estimates`` (first-visit-seeded from IRIS), while the curated
values written to ``state.threat_event_frequency`` are dropped (their only
consumer, ``wizard_state.build_create_form``, is dead code).

Unlike ``test_library_clone_reproducibility`` (which bypasses the SME-row path
via ``_form_from_entry``), this drives the REAL wizard endpoints end to end.
"""

from __future__ import annotations

import uuid

import pytest
from fair_cam.quantile_pooling._lognormal_native import lognormal_quantiles
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.models.user import User
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _current_version_token,
)


async def _analyst_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    row = (
        await db.execute(
            select(User).where(
                User.organization_id == org_id,
                User.email == "analyst@test.local",
            )
        )
    ).scalar_one()
    return row.id


@pytest.mark.asyncio
async def test_library_scenario_finalizes_with_iris_not_curated(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    analyst_client, org_id = authed_analyst
    # Published entry with a DISTINCTIVE high-frequency curated TEF (p95≈40),
    # far above any IRIS industry baseline (manufacturing ~<2/yr).
    eid = uuid.uuid4()
    # Snapshot the curated dists as plain locals — `entry` attributes are
    # unreadable after later `expire_all()` (async lazy-load).
    curated_tef = {"distribution": "PERT", "low": 8.0, "mode": 20.0, "high": 40.0}
    curated_vuln = {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50}
    curated_pl = {"distribution": "lognormal", "mean": 13.0, "sigma": 1.0}
    curated_sl = {"distribution": "lognormal", "mean": 11.0, "sigma": 0.8}
    entry = ScenarioLibraryEntry(
        id=eid,
        version=1,
        slug="prefill-repro",
        name="Prefill repro",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        # PERT TEF/vuln (bounded round-trip) + lognormal PL/SL (exact round-trip
        # — the production Epic-D loss shape) so all four fieldsets' seed→refit
        # fidelity is exercised.
        threat_event_frequency=curated_tef,
        vulnerability=curated_vuln,
        primary_loss=curated_pl,
        secondary_loss=curated_sl,
        suggested_control_ids=[],
    )
    db_session.add(entry)
    await db_session.commit()

    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(
        analyst_client, db_session, user_id, library_entry=entry
    )

    # Visit steps 3 + 4 to trigger the first-visit auto-seed of sme_estimates.
    r3 = await analyst_client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert r3.status_code == 200, r3.text
    r4 = await analyst_client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert r4.status_code == 200, r4.text

    # Finalize (state-sourced from sme_estimates).
    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        analyst_client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert resp.status_code in (200, 303), resp.text

    db_session.expire_all()
    scen = (
        (
            await db_session.execute(
                select(Scenario)
                .where(Scenario.organization_id == org_id)
                .order_by(Scenario.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert scen is not None, "scenario was not created"
    assert scen.library_pin is not None, "scenario is not library-pinned"

    tef = scen.threat_event_frequency
    # Finalize collapses the seeded lognormal (fit from the curated p5/p95) to a
    # bounded PERT (#tef-pert-revert). A library-derived scenario MUST carry the
    # entry's curated TEF. For the PERT(8,20,40) entry the extracted p5/p95 are
    # the Beta 5th/95th pctiles (~12.97, ~30.52) — promoted to the finalized PERT
    # bounds — NOT the threat-blind IRIS baseline (p5≈0.05, p95≈1.0).
    assert tef["distribution"] == "PERT", tef
    assert tef["low"] == pytest.approx(12.97, rel=0.15), (
        f"curated TEF was dropped; finalized low={tef['low']:.4f} — the wizard "
        "seeded the threat-blind IRIS baseline instead of the library entry's "
        "curated distribution (#wizard-library-prefill)"
    )
    assert tef["high"] == pytest.approx(30.5, rel=0.15), (
        f"finalized high={tef['high']:.4f} != curated ~30.5"
    )
    assert tef["low"] < tef["mode"] < tef["high"], tef

    # Full seed→refit fidelity across ALL FOUR fieldsets. The seed extracts each
    # curated dist's p5/p95 (`_quantile_pair`); finalize refits to those. The
    # round-trip recovers them within ~0.01% (the hardcoded 1.645 z vs precise
    # ppf), read per finalized type: lognormal (pl/sl) via lognormal_quantiles;
    # PERT (tef/vuln) stores the seeded p5/p95 directly as its [low, high] bounds.
    from idraa.services.run_executor import _dict_to_fair_distribution
    from idraa.services.wizard_helpers import _quantile_pair

    def _curated_pair(dist: dict) -> tuple[float, float]:
        qp = _quantile_pair(_dict_to_fair_distribution(dist))
        return qp["low"], qp["high"]

    def _finalized_pair(dist: dict) -> tuple[float, float]:
        if dist.get("distribution") == "lognormal":
            lo, hi = lognormal_quantiles(dist["mean"], dist["sigma"], [0.05, 0.95])
            return lo, hi
        return dist["low"], dist["high"]  # PERT: seeded p5/p95 became the bounds

    for fs, curated, scen_dist in (
        ("tef", curated_tef, scen.threat_event_frequency),
        ("vuln", curated_vuln, scen.vulnerability),
        ("pl", curated_pl, scen.primary_loss),
        ("sl", curated_sl, scen.secondary_loss),
    ):
        c_lo, c_hi = _curated_pair(curated)
        s_lo, s_hi = _finalized_pair(scen_dist)
        assert s_lo == pytest.approx(c_lo, rel=0.01), f"{fs} p5 {s_lo:.5g} != curated {c_lo:.5g}"
        assert s_hi == pytest.approx(c_hi, rel=0.01), f"{fs} p95 {s_hi:.5g} != curated {c_hi:.5g}"

    # Attribution: the seeded row is a "Library reference" system SME, and the
    # library path SKIPS the IRIS seed (no "Industry baseline" SME materialized).
    from idraa.models.sme import SubjectMatterExpert

    smes = (
        (
            await db_session.execute(
                select(SubjectMatterExpert).where(
                    SubjectMatterExpert.organization_id == org_id,
                    SubjectMatterExpert.is_system_owned.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    names = {s.name for s in smes}
    assert "Library reference" in names, f"expected Library reference SME, got {names}"
    assert "Industry baseline" not in names, (
        f"library path must not materialize the IRIS SME, got {names}"
    )


@pytest.mark.asyncio
async def test_from_scratch_scenario_still_seeds_iris_baseline(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Regression guard: a from-scratch (skip-library) scenario is UNCHANGED —
    it still seeds the threat-blind IRIS industry baseline (#wizard-library-prefill
    branches ONLY library-derived scenarios)."""
    analyst_client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(analyst_client, db_session, user_id)

    r3 = await analyst_client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert r3.status_code == 200, r3.text
    r4 = await analyst_client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert r4.status_code == 200, r4.text

    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        analyst_client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert resp.status_code in (200, 303), resp.text

    db_session.expire_all()
    scen = (
        (
            await db_session.execute(
                select(Scenario)
                .where(Scenario.organization_id == org_id)
                .order_by(Scenario.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert scen is not None
    assert scen.library_pin is None, "from-scratch scenario must not be library-pinned"
    tef = scen.threat_event_frequency
    # Manufacturing IRIS baseline is well below 5/yr — the threat-blind org
    # aggregate, unchanged by the fix. TEF is bounded PERT (#tef-pert-revert), and
    # the whole distribution sits below 5.
    assert tef["distribution"] == "PERT", tef
    assert tef["high"] < 5.0, (
        f"from-scratch TEF should be the IRIS baseline; high={tef['high']:.4f}"
    )


async def _finalize_from_entry(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    org_id: uuid.UUID,
    entry: ScenarioLibraryEntry,
) -> Scenario:
    """Bootstrap → auto-seed steps 3+4 → finalize → return the new scenario."""
    db_session.add(entry)
    await db_session.commit()
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(
        analyst_client, db_session, user_id, library_entry=entry
    )
    assert (await analyst_client.get(f"/scenarios/new/wizard/step/3?tx={tx}")).status_code == 200
    assert (await analyst_client.get(f"/scenarios/new/wizard/step/4?tx={tx}")).status_code == 200
    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        analyst_client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert resp.status_code in (200, 303), resp.text
    db_session.expire_all()
    scen = (
        (
            await db_session.execute(
                select(Scenario)
                .where(Scenario.organization_id == org_id)
                .order_by(Scenario.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert scen is not None
    return scen


def _entry_kwargs(slug: str) -> dict:
    return {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": slug,
        "name": slug,
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "d",
        "canonical_fair_gap": "g",
        "source_citations": [],
        "threat_event_frequency": {"distribution": "PERT", "low": 0.5, "mode": 1.0, "high": 4.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        "suggested_control_ids": [],
    }


@pytest.mark.asyncio
async def test_pert_loss_library_entry_prefill_fidelity(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Milestone B (#loss-pert-overhaul): a capped library entry with PERT loss
    seeds the wizard and finalizes back to PERT with the seeded p5/p95 as the
    stored bounds (same round-trip contract the main test pins for TEF/vuln)."""
    analyst_client, org_id = authed_analyst
    curated_pl = {
        "distribution": "PERT",
        "low": 10_000.0,
        "mode": 10_000.0,
        "high": 5_000_000.0,
    }
    entry = ScenarioLibraryEntry(
        **_entry_kwargs("pert-loss-prefill"),
        primary_loss=curated_pl,
        secondary_loss=None,
        loss_shape="capped",
    )
    scen = await _finalize_from_entry(analyst_client, db_session, org_id, entry)
    pl = scen.primary_loss
    assert pl["distribution"] == "PERT"
    assert pl["low"] <= pl["mode"] < pl["high"]
    # Round-trip: the curated PERT's Beta p5/p95 seed the SME rows; finalize
    # promotes them to the stored PERT bounds (within the 1.645-z tolerance).
    from idraa.services.run_executor import _dict_to_fair_distribution
    from idraa.services.wizard_helpers import _quantile_pair

    qp = _quantile_pair(_dict_to_fair_distribution(curated_pl))
    assert pl["low"] == pytest.approx(qp["low"], rel=0.01)
    assert pl["high"] == pytest.approx(qp["high"], rel=0.01)


@pytest.mark.asyncio
async def test_catastrophic_library_entry_finalizes_lognormal(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Milestone B: a loss_shape='catastrophic' entry seeds state.loss_shape ->
    finalize stores pl as native lognormal, p5/p95 round-trip within 1%."""
    analyst_client, org_id = authed_analyst
    curated_pl = {"distribution": "lognormal", "mean": 13.0, "sigma": 1.0}
    entry = ScenarioLibraryEntry(
        **_entry_kwargs("cat-loss-prefill"),
        primary_loss=curated_pl,
        secondary_loss=None,
        loss_shape="catastrophic",
    )
    scen = await _finalize_from_entry(analyst_client, db_session, org_id, entry)
    pl = scen.primary_loss
    assert pl["distribution"] == "lognormal"
    lo, hi = lognormal_quantiles(pl["mean"], pl["sigma"], [0.05, 0.95])
    exp_lo, exp_hi = lognormal_quantiles(13.0, 1.0, [0.05, 0.95])
    assert lo == pytest.approx(exp_lo, rel=0.01)
    assert hi == pytest.approx(exp_hi, rel=0.01)
