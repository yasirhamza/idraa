"""F7: the step-6 review page renders the entered SME estimates.

2026-05-28 step-3 split (D6): the wizard is evaluator-style — TEF/Vuln/PL/SL
are persisted into ``state.sme_estimates`` by steps 3+4, and the old
PERT-distribution fields (``state.threat_event_frequency`` etc.) stay EMPTY
until finalize. The review page must therefore render a server-side summary of
``state.sme_estimates`` (per-fieldset Source + low–high range), not the dead
PERT-dist fields that show "—" for everything.

This is a behaviour-PERSISTS-surface-changed test (F0 triage): "review shows the
entered FAIR estimates" survives the redesign; the rendering source changed from
PERT-dist fields to SME-row state.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.sme import SubjectMatterExpert
from idraa.models.user import User
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _persist_fair_rows_via_steps_3_and_4,
)


async def _resolve_analyst_user_id(db: AsyncSession) -> uuid.UUID:
    row = (
        await db.execute(select(User).where(User.email == "analyst@test.local"))
    ).scalar_one_or_none()
    assert row is not None
    return row.id


async def _seed_one_sme(db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
    sme = SubjectMatterExpert(
        organization_id=org_id,
        name="Test SME",
        email="review-sme@example.com",
        created_by=created_by,
        created_via="admin",
    )
    db.add(sme)
    await db.flush()
    await db.commit()
    return sme.id


@pytest.mark.asyncio
async def test_review_renders_entered_sme_rows_not_dashes(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """GET /step/6 for a tx walked through steps 3+4 renders the entered SME
    estimates (Source + range), not "—" for every FAIR param."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    # Walk steps 3+4 so state.sme_estimates has tef/vuln/pl/sl rows. The GET in
    # the helper triggers the eager IRIS seed (a Baseline row), then the POSTs
    # overwrite tef/vuln/pl/sl with these named-SME rows.
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100000.0, 5000000.0)],
        sl=[(str(sme_id), 5000.0, 50000.0)],
    )
    await db_session.close()

    resp = await client.get(f"/scenarios/new/wizard/step/6?tx={tx}")
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "Step 6 of 6" in body
    # FAIR section shows the per-fieldset estimate rows, not the dead PERT block.
    assert "Threat event frequency" in body
    assert "Primary loss" in body
    # A Source label renders: the seeded baseline ("Baseline") or a named SME.
    assert "Baseline" in body or "Test SME" in body or "SME" in body
    # The FAIR section shows a low–high range, not "—". The range separator
    # "–" (en dash) appears between low and high for each rendered row.
    assert " – " in body
    # Edit links point to the split pages + renumbered controls.
    assert 'href="/scenarios/new/wizard/step/3' in body  # likelihood
    assert 'href="/scenarios/new/wizard/step/4' in body  # impact
    assert 'href="/scenarios/new/wizard/step/5' in body  # controls


@pytest.mark.asyncio
async def test_review_surfaces_loss_shape_and_catastrophic_toggle(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """UAT 2026-07-21: the step-4 catastrophic toggle must have a VISIBLE
    consequence on the review page — data-loss-shape shows Capped by default
    and flips to Catastrophic after a step-4 POST with loss_catastrophic=1."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100000.0, 5000000.0)],
        sl=[(str(sme_id), 5000.0, 50000.0)],
    )
    await db_session.close()

    body = (await client.get(f"/scenarios/new/wizard/step/6?tx={tx}")).text
    assert "data-loss-shape" in body
    assert "Capped (bounded PERT)" in body

    # Re-POST step 4 with the toggle ON (checkbox present == checked).
    step4_data = {
        "pl_sme_id_0": str(sme_id),
        "pl_sme_name_0": "",
        "pl_low_0": "100000.0",
        "pl_high_0": "5000000.0",
        # W-4: the real step-4 form always posts BOTH fieldsets — omitting
        # sl_* here would silently wipe the persisted SL rows via the merge.
        "sl_sme_id_0": str(sme_id),
        "sl_sme_name_0": "",
        "sl_low_0": "5000.0",
        "sl_high_0": "50000.0",
        "loss_catastrophic": "1",
    }
    r4 = await csrf_post(client, f"/scenarios/new/wizard/step/4?tx={tx}", data=step4_data)
    assert r4.status_code in (302, 303), r4.text

    body = (await client.get(f"/scenarios/new/wizard/step/6?tx={tx}")).text
    assert "Catastrophic — uncapped lognormal" in body
