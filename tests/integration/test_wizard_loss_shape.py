"""Milestone B (#loss-pert-overhaul) end-to-end: the step-4 toggle round-trips
into WizardState and finalize stores pl/sl per loss_shape; a catastrophic
library entry pre-checks the toggle."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from idraa.services.calibration import WITHIN_SCENARIO_SIGMA_DEFAULT
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)


async def _analyst_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    row = (
        await db.execute(
            select(User).where(User.organization_id == org_id, User.email == "analyst@test.local")
        )
    ).scalar_one()
    return row.id


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


@pytest.mark.asyncio
async def test_default_capped_finalize_stores_pert_loss(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """No toggle -> capped default -> pl stored as bounded PERT."""
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[("Analyst A", 1.0, 12.0)],
        vuln=[("Analyst A", 0.05, 0.5)],
        pl=[("Analyst A", 100_000.0, 5_000_000.0)],
    )
    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", data={"version_token": str(vt)}
    )
    assert resp.status_code in (200, 303), resp.text
    db_session.expire_all()
    scen = await _latest_scenario(db_session, org_id)
    pl = scen.primary_loss
    assert pl["distribution"] == "PERT"
    assert pl["low"] <= pl["mode"] < pl["high"]
    assert "mean" not in pl and "sigma" not in pl


@pytest.mark.asyncio
async def test_catastrophic_toggle_finalizes_native_lognormal(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """POST step 4 WITH loss_catastrophic=1 -> pl stored as native lognormal."""
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    # Step 3 (tef/vuln) via the shared helper's format, step 4 manually so the
    # checkbox field rides the same POST as the pl rows.
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data={
            "tef_sme_id_0": "",
            "tef_sme_name_0": "Analyst A",
            "tef_low_0": "1.0",
            "tef_high_0": "12.0",
            "vuln_sme_id_0": "",
            "vuln_sme_name_0": "Analyst A",
            "vuln_low_0": "0.05",
            "vuln_high_0": "0.5",
        },
    )
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
    assert resp.status_code in (200, 303), resp.text
    db_session.expire_all()
    scen = await _latest_scenario(db_session, org_id)
    pl = scen.primary_loss
    assert pl["distribution"] == "lognormal"
    assert pl["sigma"] > 0 and "low" not in pl
    # tef is unaffected by the loss toggle (still bounded PERT).
    assert scen.threat_event_frequency["distribution"] == "PERT"


@pytest.mark.asyncio
async def test_catastrophic_library_entry_prechecks_toggle(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """A loss_shape='catastrophic' library entry seeds state -> step-4 toggle
    renders checked."""
    client, org_id = authed_analyst
    entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="cat-toggle-repro",
        name="Catastrophic toggle repro",
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
    r4 = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert r4.status_code == 200, r4.text
    # The toggle input renders checked (server-side, no-JS parity).
    assert 'name="loss_catastrophic"' in r4.text
    import re

    toggle = re.search(r"<input[^>]*name=\"loss_catastrophic\"[^>]*>", r4.text)
    assert toggle is not None
    assert "checked" in toggle.group(0)


# ---------------------------------------------------------------------------
# Task 5 (plan 2026-07-25-sigma-recal-pr1): narrow-only IRIS re-spread on the
# wizard's own seeding path + the finalize advisory query-param flash.
# ---------------------------------------------------------------------------


def test_iris_pl_seed_respread_narrow_only() -> None:
    """A wide IRIS pl/sl pair is re-spread around its OWN geometric midpoint
    at the within-scenario default -- the midpoint (== the prior's median,
    since _quantile_pair returns symmetric log-quantiles) is held exactly."""
    import math

    from idraa.routes.scenarios import _iris_seed_rows

    z = 1.6448536269514722
    med = 1_000_000.0
    wide = {"pl": {"low": med * math.exp(-z * 2.2723), "high": med * math.exp(z * 2.2723)}}
    rows = _iris_seed_rows(wide, "sme-1")
    lo, hi = rows["pl"][0]["low"], rows["pl"][0]["high"]
    assert math.log(hi / lo) / (2 * z) == pytest.approx(WITHIN_SCENARIO_SIGMA_DEFAULT)
    assert math.sqrt(lo * hi) == pytest.approx(med)  # geometric midpoint == lognormal median


def test_iris_pl_seed_narrower_than_default_untouched() -> None:
    """AGRICULTURE/MINING/REAL_ESTATE-class priors: implied sigma < default
    -> no change (this is what auto-excludes them, D10')."""
    from idraa.routes.scenarios import _iris_seed_rows

    narrow = {"pl": {"low": 100_000.0, "high": 1_000_000.0}}  # sigma ~0.70
    rows = _iris_seed_rows(narrow, "sme-1")
    assert rows["pl"][0]["low"] == 100_000.0 and rows["pl"][0]["high"] == 1_000_000.0


def test_tef_and_vuln_seeds_never_respread() -> None:
    """The re-spread applies ONLY to pl/sl -- tef/vuln pass through verbatim
    even when their span would exceed the loss-dispersion default."""
    from idraa.routes.scenarios import _iris_seed_rows

    f = {"tef": {"low": 0.29, "high": 1.05}, "vuln": {"low": 0.1, "high": 0.4}}
    rows = _iris_seed_rows(f, "sme-1")
    assert rows["tef"][0] == {"sme_id": "sme-1", "low": 0.29, "high": 1.05}
    assert rows["vuln"][0] == {"sme_id": "sme-1", "low": 0.1, "high": 0.4}


@pytest.mark.asyncio
async def test_wizard_flow_iris_seed_finalizes_at_narrow_only_sigma(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Real flow: step 3 GET seeds all four fieldsets (the default test
    org is manufacturing / 100m_to_1b -- pre-respread PL sigma 2.2725, wide,
    NOT one of the D10' excluded industries) -> step 4 POST with the
    UNEDITED seeded pl row + loss_catastrophic checked -> finalize stores a
    native single-SME lognormal whose sigma round-trips to the
    within-scenario default (the seed's re-spread and the finalize fit both
    use the SAME canonical z, so the round-trip is exact to float precision)."""
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)

    r3get = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert r3get.status_code == 200, r3get.text

    draft = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one()
    seeded_pl = draft.state_json["sme_estimates"]["pl"][0]

    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": seeded_pl["sme_id"],
            "pl_sme_name_0": "",
            "pl_low_0": str(seeded_pl["low"]),
            "pl_high_0": str(seeded_pl["high"]),
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
    pl = scen.primary_loss
    assert pl["distribution"] == "lognormal"
    assert pl["sigma"] == pytest.approx(WITHIN_SCENARIO_SIGMA_DEFAULT, rel=1e-3)


@pytest.mark.asyncio
async def test_finalize_advisory_flags_wide_stored_dispersion(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Hand-posted step-4 rows (bypassing IRIS auto-seed, same as
    test_catastrophic_toggle_finalizes_native_lognormal) with a span implying
    sigma=2.6 survive UNCHANGED through finalize -- there is no re-spread on
    the analyst-entered path, only on the IRIS auto-seed. The stored sigma
    exceeds the within-scenario default, so the 303 redirect carries
    ?loss_wide=1 and the target page's flash names the dispersion."""
    import math

    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)

    z = 1.6448536269514722
    sigma_wide = 2.6
    median = 1_000_000.0
    low = median * math.exp(-z * sigma_wide)
    high = median * math.exp(z * sigma_wide)

    r3 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data={
            "tef_sme_id_0": "",
            "tef_sme_name_0": "Analyst A",
            "tef_low_0": "1.0",
            "tef_high_0": "12.0",
            "vuln_sme_id_0": "",
            "vuln_sme_name_0": "Analyst A",
            "vuln_low_0": "0.05",
            "vuln_high_0": "0.5",
        },
    )
    assert r3.status_code in (302, 303), r3.text
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": str(low),
            "pl_high_0": str(high),
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
    assert resp.headers["location"].endswith("?loss_wide=1"), resp.headers["location"]

    follow = await client.get(resp.headers["location"])
    assert follow.status_code == 200, follow.text
    assert "dispersion" in follow.text
