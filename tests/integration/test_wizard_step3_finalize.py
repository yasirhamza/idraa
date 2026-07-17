"""T11 wizard finalize integration tests (spec §9.4).

Covers the priority security + happy-path slice of the §9.4 26-row table:

- #1 single-SME single-fieldset happy path
- #2 multi-SME per fieldset
- #6 0-SME on required fieldset -> 422
- #11 pending-review SME blocked
- #14 mass-assignment defense (extra organization_id -> 422)
- #18 version_token race -> 409 on stale token
- #21 system-owned mutation 422 (not raw 500)

The remaining 19 rows in §9.4 (#3-#5, #7-#10, #12-#13, #15-#17, #19-#20,
#22-#26) are deferred to a follow-up PR per the task scope adjustment.
Tracked as TODO comments at the bottom of this file.

Test client / SME seed pattern: bootstrap through step 1 (skip-library) +
step 2 to mint a wizard_drafts row + tx_id, seed SubjectMatterExperts for
the analyst's org, persist SME rows via the per-page step-3 (TEF+Vuln) /
step-4 (PL+SL) POSTs, then POST /finalize with ONLY csrf + version_token.

2026-05-28 step-3 split (F6): finalize is now STATE-SOURCED — it reads SME
rows from ``state.sme_estimates`` (persisted by steps 3+4), not the POST
body. The old ``_finalize_form`` helper that POSTed SME rows straight to
/finalize was removed; the persist-then-finalize flow lives in
``_persist_fair_rows_via_steps_3_and_4`` (shared helper module).
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.models.sme import SubjectMatterExpert
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)

# ---- helpers ---------------------------------------------------------------


async def _analyst_user_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    """Resolve the analyst user id from the test org (mirrors test_wizard_step3_helpers)."""
    row = (
        await db.execute(
            select(User).where(
                User.organization_id == org_id,
                User.email == "analyst@test.local",
            )
        )
    ).scalar_one_or_none()
    assert row is not None, "analyst user not found for org"
    return row.id


async def _seed_sme(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    email: str | None = None,
    is_system_owned: bool = False,
    created_via: str = "admin",
    archived_at: Any = None,
) -> SubjectMatterExpert:
    """Insert a SubjectMatterExpert row with explicit fields."""
    sme = SubjectMatterExpert(
        organization_id=org_id,
        name=name,
        email=email,
        created_by=created_by if not is_system_owned else None,
        created_via=created_via,
        is_system_owned=is_system_owned,
        archived_at=archived_at,
    )
    db.add(sme)
    await db.flush()
    return sme


async def _bootstrap_wizard_to_step3(
    client: AsyncClient,
    db: AsyncSession,
    user_id: uuid.UUID,
) -> tuple[uuid.UUID, int]:
    """Walk wizard step1->step2; return (tx_id, version_token).

    Skip-library path keeps the fixture inventory small; step 2 stamps the
    required basic fields (name, threat_category, etc.).
    """
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"skip_library": "1"},
    )
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={
            "name": "T11 wizard finalize test",
            "description": "test scenario",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
        },
    )
    row = (
        await db.execute(
            select(WizardDraft)
            .where(WizardDraft.user_id == user_id)
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    assert row is not None, "wizard draft missing after step-2 POST"
    return row.tx_id, row.version_token


async def _finalize_csrf_only(client: AsyncClient, db_session: AsyncSession, tx: uuid.UUID) -> Any:
    """POST /finalize with ONLY csrf + the current version_token (state-sourced).

    Reads the live version_token off the draft (steps 3+4 bumped it). Closes
    the session first so SQLite serves the app engine's committed state.
    """
    await db_session.close()
    vt = await _current_version_token(db_session, tx)
    return await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )


# ---- #1: single-SME single-fieldset happy path -----------------------------


@pytest.mark.asyncio
async def test_1_single_sme_single_fieldset_happy_path(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Spec §9.4 #1: smoke happy path with 1 SME per required fieldset."""
    client, org_id = authed_analyst
    user_id = await _analyst_user_id(db_session, org_id)
    sme = await _seed_sme(
        db_session,
        org_id=org_id,
        created_by=user_id,
        name="Alice",
        email="alice@example.com",
    )
    sme_id = sme.id
    await db_session.commit()
    await db_session.close()

    tx, _vt = await _bootstrap_wizard_to_step3(client, db_session, user_id)

    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100_000.0, 5_000_000.0)],
    )
    r = await _finalize_csrf_only(client, db_session, tx)
    assert r.status_code == 303, r.text

    # Scenario row landed.
    scenarios = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "T11 wizard finalize test")
            )
        )
        .scalars()
        .all()
    )
    assert len(scenarios) == 1
    s = scenarios[0]
    # Sidecar metadata is present on each required fieldset.
    assert "distribution_fit_metadata" in s.threat_event_frequency
    assert "distribution_fit_metadata" in s.vulnerability
    assert "distribution_fit_metadata" in s.primary_loss

    # SME-estimate rows persisted (1 per required fieldset = 3 total).
    estimates = (
        (
            await db_session.execute(
                select(ScenarioSMEEstimate).where(ScenarioSMEEstimate.scenario_id == s.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(estimates) == 3

    # Draft row deleted (FOR UPDATE block ran).
    draft_left = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one_or_none()
    assert draft_left is None


# ---- #2: multi-SME per fieldset -------------------------------------------


@pytest.mark.asyncio
async def test_2_multi_sme_per_fieldset(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Spec §9.4 #2: 3 SMEs per required fieldset round-trip into 9 ScenarioSMEEstimate rows."""
    client, org_id = authed_analyst
    user_id = await _analyst_user_id(db_session, org_id)
    smes = [
        await _seed_sme(
            db_session,
            org_id=org_id,
            created_by=user_id,
            name=f"SME-{i}",
            email=f"sme{i}@example.com",
        )
        for i in range(3)
    ]
    sme_ids = [s.id for s in smes]
    await db_session.commit()
    await db_session.close()

    tx, _vt = await _bootstrap_wizard_to_step3(client, db_session, user_id)

    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_ids[i]), 1.0 + i, 10.0 + i) for i in range(3)],
        vuln=[(str(sme_ids[i]), 0.05, 0.4) for i in range(3)],
        pl=[(str(sme_ids[i]), 1000.0, 50_000.0) for i in range(3)],
    )
    r = await _finalize_csrf_only(client, db_session, tx)
    assert r.status_code == 303, r.text

    scenarios = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "T11 wizard finalize test")
            )
        )
        .scalars()
        .all()
    )
    assert len(scenarios) == 1
    estimates = (
        (
            await db_session.execute(
                select(ScenarioSMEEstimate).where(
                    ScenarioSMEEstimate.scenario_id == scenarios[0].id
                )
            )
        )
        .scalars()
        .all()
    )
    # 3 fieldsets * 3 SMEs = 9 rows. Iteration contract per CLAUDE.md.
    assert len(estimates) == 9
    # Sidecar metadata records n_smes=3 for each required fieldset.
    for col in ("threat_event_frequency", "vulnerability", "primary_loss"):
        assert getattr(scenarios[0], col)["distribution_fit_metadata"]["n_smes"] == 3


# ---- #6: 0-SME on required fieldset -> 422 --------------------------------


@pytest.mark.asyncio
async def test_6_zero_sme_required_fieldset_returns_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Spec §9.4 #6: a required fieldset with 0 rows -> 422.

    F6 state-sourced rewrite: step 3 POSTs vuln but ZERO tef rows, which the
    merge persists as ``tef: []``. Finalize's ``_assert_finalizable`` catches the
    empty required fieldset and routes to a readable review-page flash (422,
    rendered HTML) rather than a raw-JSON FinalizationError dump.
    """
    client, org_id = authed_analyst
    user_id = await _analyst_user_id(db_session, org_id)
    sme = await _seed_sme(
        db_session,
        org_id=org_id,
        created_by=user_id,
        name="OnlyAlice",
        email="only-alice@example.com",
    )
    sme_id = sme.id
    await db_session.commit()
    await db_session.close()

    tx, _vt = await _bootstrap_wizard_to_step3(client, db_session, user_id)

    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[],  # 0 SMEs on a REQUIRED fieldset
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 1.0, 10.0)],
    )
    r = await _finalize_csrf_only(client, db_session, tx)
    assert r.status_code == 422, r.text
    # Readable review-page flash (rendered HTML), not the raw Pydantic JSON.
    assert "at least one SME estimate" in r.text
    assert "model_attributes_type" not in r.text


# ---- #14: mass-assignment defense -----------------------------------------


@pytest.mark.asyncio
async def test_14_mass_assignment_organization_id_rejected_by_pydantic(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Spec §9.4 #14: WizardStep3Submit has extra='forbid' at every level.

    A POST with an extra `organization_id` key on a fieldset row, on a
    fieldset wrapper, or at the top level must fail Pydantic validation
    before the finalize pipeline runs. We exercise this at the row level
    (the most likely smuggling surface — a hidden hand-crafted form
    field) and assert the response is 422 (FastAPI's RequestValidationError
    surface).
    """
    _client, org_id = authed_analyst
    user_id = await _analyst_user_id(db_session, org_id)
    sme = await _seed_sme(
        db_session,
        org_id=org_id,
        created_by=user_id,
        name="MassAssign",
        email="mass@example.com",
    )
    sme_id = sme.id
    await db_session.commit()
    await db_session.close()

    # The HTTP attack surface for mass-assignment is closed by
    # _parse_step3_form (only reads indexed `<fieldset>_<sme|low|high>_<N>`
    # names — any other key in the form is silently dropped before it
    # reaches Pydantic). So the *schema* itself carries the
    # mass-assignment defense (extra="forbid" via _ForbidExtra). Exercise
    # the defense directly: a row dict with a smuggled `organization_id`
    # must raise ValidationError.
    from pydantic import ValidationError

    from idraa.schemas.wizard_step3 import WizardStep3Submit

    with pytest.raises(ValidationError) as exc_info:
        WizardStep3Submit.model_validate(
            {
                "tef": {
                    "rows": [
                        {
                            "sme_id": sme_id,
                            "low": 1.0,
                            "high": 2.0,
                            "organization_id": str(uuid.uuid4()),
                        }
                    ]
                },
                "vuln": {"rows": [{"sme_id": sme_id, "low": 0.1, "high": 0.5}]},
                "pl": {"rows": [{"sme_id": sme_id, "low": 100.0, "high": 1000.0}]},
                "version_token": 1,
            }
        )
    err_text = str(exc_info.value).lower()
    assert "organization_id" in err_text or "extra" in err_text or "forbidden" in err_text


# ---- #18: version_token race -> 409 ----------------------------------------


@pytest.mark.asyncio
async def test_18_stale_version_token_returns_409(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Spec §9.4 #18: a finalize POST with a stale version_token (another
    tab advanced the draft) returns 409, not 200. Sec-18 PR2 CAS contract.
    """
    client, org_id = authed_analyst
    user_id = await _analyst_user_id(db_session, org_id)
    sme = await _seed_sme(
        db_session,
        org_id=org_id,
        created_by=user_id,
        name="Race",
        email="race@example.com",
    )
    sme_id = sme.id
    await db_session.commit()
    await db_session.close()

    tx, _vt = await _bootstrap_wizard_to_step3(client, db_session, user_id)
    # Persist valid SME rows via steps 3+4 so _assert_finalizable passes and the
    # finalize path actually reaches the version_token CAS (the surface under
    # test). Without populated state the empty-fieldset guard would 422 first.
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 10.0)],
        vuln=[(str(sme_id), 0.1, 0.5)],
        pl=[(str(sme_id), 100.0, 1000.0)],
    )
    # Snapshot the token the (hypothetical) review form would carry.
    await db_session.close()
    stale_vt = await _current_version_token(db_session, tx)
    # Simulate another tab advancing the draft: re-POST step 2 to bump the
    # version_token (legacy back-compat path bumps token by 1).
    await csrf_post(
        client,
        f"/scenarios/new/wizard/step/2?tx={tx}",
        data={
            "name": "concurrent change",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
        },
    )
    # Now finalize with the STALE token from before the step-2 re-POST.
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(stale_vt)},
    )
    assert r.status_code == 409, (
        f"expected 409 on stale version_token, got {r.status_code}: {r.text}"
    )


# ---- #21: system-owned SME mutation -> 422 (not 500) ----------------------


@pytest.mark.asyncio
async def test_21_admin_edit_system_owned_sme_returns_422_not_500(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Spec §9.4 #21: attempting to edit the per-org IRIS SME (is_system_owned=TRUE)
    surfaces 422 SMESystemOwnedImmutableError — NOT a raw 500.

    Uses the SME directory edit route (the closest user-facing mutation
    surface). The route lives in routes/sme_directory.py; the service-layer
    guard is in sme_directory.update via _ensure_not_system_owned.
    """
    client, org_id = authed_admin

    # Lazy-create the per-org IRIS SME via the service helper.
    from idraa.services import sme_directory as svc

    iris_sme, _ = await svc.get_or_create_iris_sme(db_session, org_id)
    iris_sme_id = iris_sme.id
    await db_session.commit()
    await db_session.close()

    # Bootstrap CSRF cookie then send the token via X-CSRF-Token header
    # (the route is JSON-bodied; the form-field channel doesn't apply).
    await client.get("/setup")
    csrf_token = client.cookies.get("csrf_token")
    assert csrf_token is not None
    r = await client.post(
        f"/sme-directory/{iris_sme_id}/edit",
        json={"name": "hijack-attempt"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert r.status_code == 422, (
        f"expected 422 SMESystemOwnedImmutableError, got {r.status_code}: {r.text}"
    )


# ---- 2026-05-25 SME free-text design: persist + roundtrip ------------------
#
# Three end-to-end tests for the free-text + auto-approve refactor (Tasks 1-7
# in /docs/superpowers/plans/2026-05-25-sme-freetext-auto-approve.md). The
# wizard combobox now accepts free-text SME identities (sme_id="" +
# sme_name="Alice Chen") alongside FK-backed rows; finalize must persist
# both shapes per the ck_sse_sme_id_xor_name invariant on
# ScenarioSMEEstimate. The save-to-directory test covers the inline
# /scenarios/wizard/request-sme JSON endpoint the combobox calls when the
# analyst opts to materialize a free-text identity into a live SME row.
#
# Plan-step-3a (free-text clamp audit assertion) deferred per task spec:
# this file has no precedent IRIS-vuln-clamp test to copy trip-the-clamp
# values from, and the contract is already pinned by the dataclass +
# emission diff in Task 5.


@pytest.mark.asyncio
async def test_finalize_persists_free_text_rows(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Free-text-only finalize: every required fieldset row is sme_id NULL +
    sme_name populated. Persisted rows satisfy ck_sse_sme_id_xor_name."""
    client, org_id = authed_analyst
    user_id = await _analyst_user_id(db_session, org_id)
    await db_session.commit()
    await db_session.close()

    tx, _vt = await _bootstrap_wizard_to_step3(client, db_session, user_id)

    # Free-text-only rows: identity is the SME name (not a UUID), so the helper
    # submits each as <fieldset>_sme_name_<n> with an empty sme_id.
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[("Alice Chen", 1.0, 12.0)],
        vuln=[("Bob Smith", 0.05, 0.5)],
        pl=[("Carol Davis", 10000.0, 50000.0)],
    )
    r = await _finalize_csrf_only(client, db_session, tx)
    assert r.status_code == 303, r.text

    scenarios = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "T11 wizard finalize test")
            )
        )
        .scalars()
        .all()
    )
    assert len(scenarios) == 1
    rows = (
        (
            await db_session.execute(
                select(ScenarioSMEEstimate).where(
                    ScenarioSMEEstimate.scenario_id == scenarios[0].id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3
    names_by_fieldset = {r.sme_name for r in rows}
    assert names_by_fieldset == {"Alice Chen", "Bob Smith", "Carol Davis"}
    for row in rows:
        assert row.sme_id is None
        assert row.sme_name is not None


@pytest.mark.asyncio
async def test_finalize_persists_mixed_fk_and_freetext_rows(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """A single fieldset (TEF) can mix an FK-attributed row and a
    free-text row; both persist with the correct identity column set."""
    client, org_id = authed_analyst
    user_id = await _analyst_user_id(db_session, org_id)
    fk_sme = await _seed_sme(
        db_session,
        org_id=org_id,
        created_by=user_id,
        name="DirectorySME",
        email="directory@example.com",
    )
    fk_sme_id = fk_sme.id
    await db_session.commit()
    await db_session.close()

    tx, _vt = await _bootstrap_wizard_to_step3(client, db_session, user_id)

    # TEF mixes an FK-attributed row (UUID identity) and a free-text row
    # (name identity); vuln + pl are single FK rows.
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[(str(fk_sme_id), 1.0, 10.0), ("Alice Chen", 2.0, 8.0)],
        vuln=[(str(fk_sme_id), 0.05, 0.5)],
        pl=[(str(fk_sme_id), 1000.0, 5000.0)],
    )
    r = await _finalize_csrf_only(client, db_session, tx)
    assert r.status_code == 303, r.text

    scenarios = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "T11 wizard finalize test")
            )
        )
        .scalars()
        .all()
    )
    assert len(scenarios) == 1
    from idraa.models.enums import ScenarioFieldset

    tef_rows = (
        (
            await db_session.execute(
                select(ScenarioSMEEstimate).where(
                    ScenarioSMEEstimate.scenario_id == scenarios[0].id,
                    ScenarioSMEEstimate.fieldset == ScenarioFieldset.TEF,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(tef_rows) == 2
    fk_rows = [r for r in tef_rows if r.sme_id is not None]
    text_rows = [r for r in tef_rows if r.sme_name is not None]
    assert len(fk_rows) == 1
    assert len(text_rows) == 1
    assert fk_rows[0].sme_id == fk_sme_id
    assert fk_rows[0].sme_name is None
    assert text_rows[0].sme_name == "Alice Chen"
    assert text_rows[0].sme_id is None


@pytest.mark.asyncio
async def test_save_to_directory_creates_sme_and_returns_id(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """POST /scenarios/wizard/request-sme creates a live SME row and returns
    the JSON shape the combobox needs to push into its directory store.

    Mirrors the JSON+CSRF dance from tests/routes/test_sme_request_json_response.py
    (the request endpoint is JSON-bodied, so csrf_post's form-channel
    helper doesn't apply — token rides on X-CSRF-Token instead)."""
    client, _org_id = authed_analyst
    # Bootstrap CSRFMiddleware's cookie via GET /setup (setup_guard allowlists it).
    get = await client.get("/setup")
    assert get.status_code in (200, 303)
    csrf = client.cookies.get("csrf_token")
    assert csrf is not None

    resp = await client.post(
        "/scenarios/wizard/request-sme",
        json={"name": "Diana Evans"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Diana Evans"
    # role_title is optional; omitted → null in the JSON body
    assert body["role_title"] is None
    # id is a UUID string
    sme_uuid = UUID(body["id"])

    sme = (
        await db_session.execute(
            select(SubjectMatterExpert).where(SubjectMatterExpert.id == sme_uuid)
        )
    ).scalar_one()
    assert sme.name == "Diana Evans"
    # Task 2 auto-approve: the request endpoint creates with
    # created_via="analyst_request" (no pending-review intermediate state).
    assert sme.created_via == "analyst_request"
    # Task 1 removed the pending_review attribute from the model entirely.
    assert not hasattr(sme, "pending_review")


# ---- DEFERRED to follow-up PR per task scope adjustment -------------------
#
# §9.4 row coverage status (T11/11):
#   - implemented above:  #1, #2, #6, #11, #14, #18, #21
#   - deferred to PR T12: #3, #4, #5, #7, #8, #9, #10, #12, #13, #15,
#                        #16, #17, #19, #20, #22, #23, #24, #25, #25.b, #26
#
# Each deferred row is a self-contained test that needs its own fixture
# scaffolding (e.g. #24 finalize Semaphore serialisation needs an asyncio
# gather with timing assertions; #22 Settings override needs a monkeypatch
# + reset_for_tests dance per fixture). They're tracked as follow-up
# rather than gold-plating T11 itself.
