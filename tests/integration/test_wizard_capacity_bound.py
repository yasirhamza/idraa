"""PR2 D13/D17/D18/D19 capacity-bound epic: wizard producer (Task 4a).

Covers `docs/superpowers/plans/2026-07-25-capacity-bound-pr2.md` Task 4a
acceptance criteria end to end via the real wizard routes:

- D18 step-4 gate: block ONLY a submitted catastrophic choice with revenue
  unset (round-6-fixed decision -- the toggle stays enabled, an unchecked
  submission proceeds as capped).
- D18 finalize backstop (TOCTOU): a stale catastrophic draft finalized after
  revenue was cleared blocks with the same copy, not a 500.
- D13 minting: `max == capacity_k * annual_revenue` on PL AND SL.
- D19: a minted cap at/below the distribution's p95 blocks with the three
  operator remedies wrapped around the Task-3b validator's factual string.
- Preserve-existing on re-estimate: an already-minted cap survives a later
  org-revenue edit untouched (D13 "snapshot-frozen at author time").
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _current_version_token,
)
from tests.integration.test_wizard_reestimate_routes import _tx_from_location

_CAT_TEF_VULN_STEP3 = {
    "tef_sme_id_0": "",
    "tef_sme_name_0": "Analyst A",
    "tef_low_0": "1.0",
    "tef_high_0": "12.0",
    "vuln_sme_id_0": "",
    "vuln_sme_name_0": "Analyst A",
    "vuln_low_0": "0.05",
    "vuln_high_0": "0.5",
}


async def _analyst_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    row = (
        await db.execute(
            select(User).where(User.organization_id == org_id, User.email == "analyst@test.local")
        )
    ).scalar_one()
    return row.id


async def _set_annual_revenue(db: AsyncSession, org_id: uuid.UUID, revenue: str | None) -> None:
    org = await db.get(Organization, org_id)
    assert org is not None
    org.annual_revenue = Decimal(revenue) if revenue is not None else None
    await db.commit()


async def _latest_scenario(db: AsyncSession, org_id: uuid.UUID) -> Scenario:
    scen = (
        (
            await db.execute(
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


async def _draft_loss_shape(db: AsyncSession, tx: uuid.UUID) -> str:
    draft = (await db.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))).scalar_one()
    return str(draft.state_json.get("loss_shape", "capped"))


# ---------------------------------------------------------------------------
# D18 step-4 gate -- the two pinned tests from the plan / brief
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step4_catastrophic_submitted_revenue_unset_blocks_loss_shape_unchanged(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """(1) loss_catastrophic submitted + revenue unset -> blocked (422),
    state.loss_shape UNCHANGED (stays "capped", the WizardState default)."""
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=_CAT_TEF_VULN_STEP3)
    assert r3.status_code in (302, 303), r3.text

    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code == 422, r4.text
    assert "annual revenue" in r4.text
    assert "Organization settings" in r4.text or "organization settings" in r4.text.lower()

    db_session.expire_all()
    assert await _draft_loss_shape(db_session, tx) == "capped"


@pytest.mark.asyncio
async def test_step4_library_seeded_catastrophic_unchecked_revenue_unset_proceeds_as_capped(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """(2) A library-seeded catastrophic draft (state.loss_shape ==
    "catastrophic" from step 1), box UNCHECKED, revenue unset -> proceeds
    (302/303) as "capped" -- NOT blocked. This is the honest analyst
    downgrade the round-6 design explicitly protects."""
    client, org_id = authed_analyst
    entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="cat-d18-unchecked-repro",
        name="D18 unchecked repro",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.NATION_STATE,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.2, "high": 0.5},
        primary_loss={"distribution": "lognormal", "mean": 13.0, "sigma": 2.0},
        secondary_loss=None,
        suggested_control_ids=[],
        loss_shape="catastrophic",
    )
    db_session.add(entry)
    await db_session.commit()

    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id, library_entry=entry)
    db_session.expire_all()
    assert await _draft_loss_shape(db_session, tx) == "catastrophic"  # seeded from the entry

    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=_CAT_TEF_VULN_STEP3)
    assert r3.status_code in (302, 303), r3.text

    # NO "loss_catastrophic" key at all -- the unchecked-box POST shape.
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
        },
    )
    assert r4.status_code in (302, 303), r4.text
    db_session.expire_all()
    assert await _draft_loss_shape(db_session, tx) == "capped"


# ---------------------------------------------------------------------------
# D13 minting + D18 finalize backstop (TOCTOU) + D19 floor wrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_mints_capacity_max_equal_k_times_revenue_on_pl_and_sl(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """D13: catastrophic finalize with revenue set mints
    max == capacity_k * annual_revenue on BOTH pl and sl (capacity_k default
    1.0, so max == annual_revenue exactly here)."""
    from idraa.config import get_settings

    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=_CAT_TEF_VULN_STEP3)
    assert r3.status_code in (302, 303), r3.text
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
            "sl_sme_id_0": "",
            "sl_sme_name_0": "Analyst A",
            "sl_low_0": "5000.0",
            "sl_high_0": "50000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text
    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", data={"version_token": str(vt)}
    )
    assert resp.status_code == 303, resp.text
    db_session.expire_all()
    scen = await _latest_scenario(db_session, org_id)
    expected_max = get_settings().capacity_k * 500_000_000.0
    assert scen.primary_loss["distribution"] == "lognormal"
    assert scen.primary_loss["max"] == pytest.approx(expected_max)
    assert scen.secondary_loss is not None
    assert scen.secondary_loss["max"] == pytest.approx(expected_max)


@pytest.mark.asyncio
async def test_finalize_backstop_stale_catastrophic_draft_after_revenue_cleared_blocks(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """D18 TOCTOU backstop: revenue is set when the step-4 toggle is
    submitted (passes the gate), but is CLEARED before finalize (e.g. a
    concurrent org-settings edit) -- finalize must block with the same D18
    copy: 422, not a 500, and no scenario is created (not silently
    uncapped)."""
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=_CAT_TEF_VULN_STEP3)
    assert r3.status_code in (302, 303), r3.text
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text

    # Concurrent org-settings edit clears revenue before finalize.
    await _set_annual_revenue(db_session, org_id, None)

    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", data={"version_token": str(vt)}
    )
    assert resp.status_code == 422, resp.text
    assert "Internal Server Error" not in resp.text
    assert "annual revenue" in resp.text

    db_session.expire_all()
    created = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org_id)))
        .scalars()
        .all()
    )
    assert created == [], "a revenue-less catastrophic draft must not silently create a scenario"


@pytest.mark.asyncio
async def test_finalize_d19_floor_conflict_blocks_with_three_remedies(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """D19: a minted cap (k * revenue) at/below the distribution's own p95
    blocks at finalize with the validator's factual string wrapped in the
    three operator remedies (lower the estimates / correct revenue / use the
    expert form with an explicit max)."""
    client, org_id = authed_analyst
    # PL's p5/p95 anchors are (100_000, 5_000_000) below -- the closed-form
    # fit's p95 equals the entered high almost exactly. A $4M revenue (cap
    # $4M at k=1.0) sits BELOW that $5M p95 -> D19 fires.
    await _set_annual_revenue(db_session, org_id, "4000000")
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=_CAT_TEF_VULN_STEP3)
    assert r3.status_code in (302, 303), r3.text
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text

    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", data={"version_token": str(vt)}
    )
    assert resp.status_code == 422, resp.text
    body = resp.text
    assert "p95" in body or "95th percentile" in body.lower()
    assert "lower the loss estimates" in body.lower()
    assert "annual revenue" in body.lower()
    assert "expert form" in body.lower()

    db_session.expire_all()
    created = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org_id)))
        .scalars()
        .all()
    )
    assert created == [], "a D19-floor-violating scenario must not be created"


@pytest.mark.asyncio
async def test_reestimate_preserves_existing_capacity_max_after_revenue_change(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """D13 "snapshot-frozen at author time": a wizard re-estimate must not
    silently replace an analyst's already-minted cap with k * CURRENT
    revenue -- editing org revenue between create and re-estimate leaves the
    existing scenario's `max` unchanged."""
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=_CAT_TEF_VULN_STEP3)
    assert r3.status_code in (302, 303), r3.text
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text
    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", data={"version_token": str(vt)}
    )
    assert resp.status_code == 303, resp.text
    db_session.expire_all()
    scen = await _latest_scenario(db_session, org_id)
    # Capture the plain UUID now -- `scen` is an ORM instance whose
    # attributes get expired by every subsequent commit()/expire_all() call
    # below; re-reading `scen.id`/`scen.primary_loss` later risks a
    # MissingGreenlet (sync attribute access outside an awaited ORM call).
    scenario_id = scen.id
    original_max = scen.primary_loss["max"]
    assert original_max == pytest.approx(500_000_000.0)

    # Org revenue changes AFTER the scenario was authored.
    await _set_annual_revenue(db_session, org_id, "900000000")

    # Re-estimate the SAME scenario. GET-only through steps 3/4 (no step-4
    # POST) -- state.loss_shape stays "catastrophic" as rehydrated from the
    # target's stored distribution (seed_wizard_state_from_scenario), and
    # finalize must preserve the ORIGINAL cap, not re-mint from the new
    # revenue.
    r_re = await csrf_post(
        client, f"/scenarios/{scenario_id}/re-estimate", {}, follow_redirects=False
    )
    assert r_re.status_code == 303, r_re.text
    tx2 = uuid.UUID(_tx_from_location(r_re.headers["location"]))
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx2}")
    await client.get(f"/scenarios/new/wizard/step/4?tx={tx2}")
    db_session.expire_all()
    assert await _draft_loss_shape(db_session, tx2) == "catastrophic"

    vt2 = await _current_version_token(db_session, tx2)
    resp2 = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx2}", data={"version_token": str(vt2)}
    )
    assert resp2.status_code == 303, resp2.text
    db_session.expire_all()
    scen2 = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()
    assert scen2.primary_loss["max"] == pytest.approx(original_max)
    assert scen2.primary_loss["max"] != pytest.approx(900_000_000.0)
