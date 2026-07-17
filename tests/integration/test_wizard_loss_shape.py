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
