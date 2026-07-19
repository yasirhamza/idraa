"""Wizard finalize end-to-end: state → Scenario + library_pin + audit (T11).

Spec §9.2 + §10.3 (sub_sector_pin co-stamping).

F6 update (2026-05-28 step-3 split): finalize is now STATE-SOURCED. Steps 3
(Likelihood: TEF+Vuln) + 4 (Impact: PL+SL) persist SME rows into
``state.sme_estimates``; the review-page Save form posts ONLY csrf +
version_token. The library_pin + audit assertions below stay identical — only
the bootstrap shape changed (rows go via steps 3+4, not the finalize body).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import ScenarioSource
from idraa.models.scenario import Scenario
from idraa.models.sme import SubjectMatterExpert
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)

_STEP_2_BASE: dict[str, str] = {
    "threat_category": "ransomware",
    "threat_actor_type": "cybercriminals",
    "asset_class": "systems",
}


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
        email="test-sme@example.com",
        created_by=created_by,
        created_via="admin",
    )
    db.add(sme)
    await db.flush()
    await db.commit()
    return sme.id


async def _current_tx(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """Resolve the latest wizard draft tx for the user."""
    draft = (
        await db.execute(
            select(WizardDraft)
            .where(WizardDraft.user_id == user_id)
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    assert draft is not None
    return draft.tx_id


async def _persist_rows_and_finalize(
    client: AsyncClient,
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    sme_id: uuid.UUID,
) -> Any:
    """F6 state-sourced flow: persist one SME row per required fieldset via
    steps 3+4, then POST /finalize with ONLY csrf + the live version_token."""
    tx = await _current_tx(db, user_id)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100000.0, 5000000.0)],
    )
    await db.close()
    vt = await _current_version_token(db, tx)
    return await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )


@pytest.mark.asyncio
async def test_wizard_finalize_with_library_records_library_pin(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """End-to-end wizard run with library; verify library_pin populated."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(seed_library_entry.id)},
    )
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={"name": "Wizard scenario WLP", **_STEP_2_BASE},
    )
    r = await _persist_rows_and_finalize(client, db_session, user_id=user_id, sme_id=sme_id)
    assert r.status_code == 303, r.text

    rows = (
        (await db_session.execute(select(Scenario).where(Scenario.name == "Wizard scenario WLP")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    s = rows[0]
    assert s.source == ScenarioSource.LIBRARY_DERIVED
    assert s.library_pin == {
        "entry_id": str(seed_library_entry.id),
        "version": seed_library_entry.version,
        "override_id": None,
        "override_version": None,
    }


@pytest.mark.asyncio
async def test_wizard_finalize_writes_audit_log(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """r2 BLOCKER 3: AuditLog uses entity_id + changes (not resource_id + details)."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(seed_library_entry.id)},
    )
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={"name": "Audit-tracked", **_STEP_2_BASE},
    )
    await _persist_rows_and_finalize(client, db_session, user_id=user_id, sme_id=sme_id)

    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.create")))
        .scalars()
        .all()
    )
    assert len(rows) >= 1
    last = rows[-1]
    # r2 BLOCKER 3: AuditLog columns are `changes` + `entity_id` (not
    # `details` / `resource_id`). The changes payload preserves library_pin
    # via the create-diff format `[None, value]`.
    assert last.changes["source"] == [None, "library_derived"]
    library_pin_change: list[Any] = last.changes["library_pin"]  # type: ignore[assignment]
    library_pin_dict: dict[str, Any] = library_pin_change[1]
    assert library_pin_dict["entry_id"] == str(seed_library_entry.id)


# ---------------------------------------------------------------------------
# F6: finalize reads SME rows from state (not the POST body)
# ---------------------------------------------------------------------------


async def _bootstrap_past_step2(
    client: AsyncClient, db: AsyncSession
) -> tuple[uuid.UUID, uuid.UUID]:
    """Walk step 1 (skip-library) + step 2; return (tx, user_id).

    Leaves ``state.sme_estimates`` EMPTY — no FAIR page has been GET'd, so the
    eager IRIS seed has not run. Used by the empty-draft 422 tests.
    """
    user_id = await _resolve_analyst_user_id(db)
    await csrf_post(client, "/scenarios/new/wizard/step/1", data={"skip_library": "1"})
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={"name": "F6 finalize state-source test", **_STEP_2_BASE},
    )
    return await _current_tx(db, user_id), user_id


@pytest.mark.asyncio
async def test_finalize_reads_sme_estimates_from_state(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Steps 3+4 persist the SME rows; the review-page Save form posts only
    csrf + version_token (no tef_*/pl_* fields). Finalize must read state."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    tx, _user_id = await _bootstrap_past_step2(client, db_session)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100000.0, 5000000.0)],
    )
    await db_session.close()
    vt = await _current_version_token(db_session, tx)
    # Body carries ONLY version_token (+ _csrf injected by csrf_post) — no rows.
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert r.status_code in (302, 303), r.text
    assert "/scenarios/" in r.headers["location"]


@pytest.mark.asyncio
async def test_finalize_wide_loss_range_saves_not_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Regression for the wizard-finalize 500: a legitimate WIDE primary-loss
    range (p5=$1k, p95=$50M, ~4.7 orders of magnitude) must SAVE (303), not
    500.

    Before the fit-convergence fix the wizard routed native-lognormal storage
    through the truncated scipy fitter, which diverged on wide anchors to
    sdlog~=10.76; the sigma<=10 storage guard then rejected it and
    finalize_wizard (no try/except around create_from_wizard) let the
    FAIRCAMValidationError escape -> Internal Server Error. The native
    closed-form path converges to sdlog~=3.29 (< 10) so the scenario saves.
    """
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    tx, _user_id = await _bootstrap_past_step2(client, db_session)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 1_000.0, 50_000_000.0)],  # wide-but-legitimate
    )
    await db_session.close()
    vt = await _current_version_token(db_session, tx)
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert r.status_code == 303, r.text
    assert "/scenarios/" in r.headers["location"]

    # Milestone B (#loss-pert-overhaul): the default (capped) finalize stores
    # PL as a bounded PERT collapsed from the same converged lognormal fit
    # (mode clamps to low for this wide anchor, sigma~3.29 > 1.645); the sane
    # sub-10 pooled sigma now lives in the provenance sidecar.
    await db_session.close()
    s = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "F6 finalize state-source test")
            )
        )
        .scalars()
        .one()
    )
    assert s.primary_loss["distribution"] == "PERT"
    assert s.primary_loss["low"] <= s.primary_loss["mode"] < s.primary_loss["high"]
    # issue #27 Task 5: the retired scalar pooled_sdlog sidecar key is now a
    # single-element component_sdlogs list (single-SME pooling).
    component_sdlogs = s.primary_loss["distribution_fit_metadata"]["component_sdlogs"]
    assert len(component_sdlogs) == 1
    assert 0 < component_sdlogs[0] < 10.0


@pytest.mark.asyncio
async def test_finalize_absurd_range_flashes_not_500_and_preserves_token(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """L1 route guard: a finalize whose distributions are genuinely
    unstorable (here a >14-order PL range -> closed-form sigma > 10, the
    OOM/DoS storage guard Sec-I2) must surface a readable review-page flash
    (422), NOT a 500.

    Milestone B (#loss-pert-overhaul): the sigma<=10 guard is
    lognormal-specific, and the capped default now stores PL as a BOUNDED
    PERT — on that path this absurd range legitimately saves (bounded by
    construction, no OOM surface). The 422 contract therefore lives on the
    CATASTROPHIC (native-lognormal) path, so this test sets the step-4
    toggle to route the absurd range through it.

    Also asserts the A-I3 CAS contract across the post-advance_step rejection
    path: the catch rolls back, so the version_token is NOT consumed and the
    same token is immediately re-submittable after the operator narrows the
    range.
    """
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    tx, _user_id = await _bootstrap_past_step2(client, db_session)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data={
            "tef_sme_id_0": str(sme_id),
            "tef_sme_name_0": "",
            "tef_low_0": "1.0",
            "tef_high_0": "12.0",
            "vuln_sme_id_0": str(sme_id),
            "vuln_sme_name_0": "",
            "vuln_low_0": "0.05",
            "vuln_high_0": "0.5",
        },
    )
    assert r3.status_code in (302, 303), r3.text
    # ~15 orders of magnitude => closed-form sigma ~= 10.5 (just over the
    # _SIGMA_MAX=10 storage guard) => rejected ON THE LOGNORMAL PATH. If
    # _SIGMA_MAX is ever raised, this range would start saving and the 422
    # assertion below fails loudly.
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": str(sme_id),
            "pl_sme_name_0": "",
            "pl_low_0": "1000.0",
            "pl_high_0": "1000000000000000000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text
    await db_session.close()
    token_before = await _current_version_token(db_session, tx)
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(token_before)},
    )
    assert r.status_code == 422, r.text
    assert "Internal Server Error" not in r.text
    # No scenario row was created.
    await db_session.close()
    created = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "F6 finalize state-source test")
            )
        )
        .scalars()
        .all()
    )
    assert created == []
    # A-I3: the CAS token was rolled back, not consumed.
    token_after = await _current_version_token(db_session, tx)
    assert token_after == token_before


@pytest.mark.asyncio
async def test_finalize_version_token_conflict_returns_409(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """A finalize POST with a stale version_token returns 409 (CAS contract)."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    tx, _user_id = await _bootstrap_past_step2(client, db_session)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100000.0, 5000000.0)],
    )
    await db_session.close()
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": "99999"},  # stale
    )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_finalize_empty_required_fieldset_flashes_not_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Plan-gate S-I1: a draft with no tef/vuln/pl rows must surface a readable
    review-page flash (422), NOT a raw-JSON Pydantic dump or a 500."""
    client, _org_id = authed_analyst
    await db_session.close()

    # fresh_wizard_tx_past_step2: advanced past step 2, sme_estimates EMPTY
    # (no FAIR page GET'd, so the eager IRIS seed never ran).
    tx, _user_id = await _bootstrap_past_step2(client, db_session)
    await db_session.close()
    vt = await _current_version_token(db_session, tx)
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert r.status_code == 422, r.text
    assert "at least one SME estimate" in r.text  # readable flash, rendered HTML
    assert "model_attributes_type" not in r.text  # not the raw Pydantic JSON


@pytest.mark.asyncio
async def test_finalize_validation_flash_leaves_token_resubmittable(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Plan-gate A-I3: a flash-rejected finalize must NOT bump version_token
    (the _assert_finalizable guard runs BEFORE advance_step), so the same
    token is re-submittable after the operator fixes the draft."""
    client, _org_id = authed_analyst
    await db_session.close()

    tx, _user_id = await _bootstrap_past_step2(client, db_session)
    await db_session.close()
    token_before = await _current_version_token(db_session, tx)
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(token_before)},
    )
    assert r.status_code == 422, r.text
    await db_session.close()
    token_after = await _current_version_token(db_session, tx)
    assert token_after == token_before  # CAS token NOT consumed


@pytest.mark.asyncio
async def test_finalize_multi_sme_audit_row_carries_pooling_summary(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """routes/scenarios.py:2311-2314 fix (issue #27 Task 5): the finalize
    route's per_fieldset_pooling_summary used to build ``getattr(r.pooled,
    "meanlog", None)`` etc, which silently returned None for every fieldset
    once r.pooled became a mixture (T1) -- degrading the audit trail exactly
    for the multi-SME case the audit log exists to explain. A 2-SME pl
    fieldset must produce a non-null, component-aware summary in the
    ``scenario.create`` audit row."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    tx, _user_id = await _bootstrap_past_step2(client, db_session)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        # Two experts on pl -- one directory SME, one free-text -- so the
        # audit summary must carry a genuine 2-component pooling result.
        pl=[
            (str(sme_id), 100_000.0, 5_000_000.0),
            ("Second Expert", 1_000_000.0, 50_000_000.0),
        ],
    )
    await db_session.close()
    vt = await _current_version_token(db_session, tx)
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert r.status_code == 303, r.text

    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.create")))
        .scalars()
        .all()
    )
    assert len(rows) >= 1
    last = rows[-1]
    pl_summary = last.changes["per_fieldset_pooling_summary"][1]["pl"]
    print(f"pl pooling summary (multi-SME, audit row): {pl_summary}")
    assert pl_summary is not None
    assert pl_summary["n_smes"] == 2
    assert pl_summary["component_meanlogs"] is not None
    assert len(pl_summary["component_meanlogs"]) == 2
    assert len(pl_summary["component_sdlogs"]) == 2
    assert pl_summary["weights"] == pytest.approx([0.5, 0.5])
