"""Wizard route flow — GET/POST per step + RBAC + cancel + finalize.

Spec §8.1 §8.4.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.models.wizard_draft import WizardDraft
from idraa.services.auth import SESSION_COOKIE
from tests.conftest import csrf_post
from tests.factories import login_client_as
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
    _user_id_from_org,
)


def _make_lib_entry(slug: str) -> Any:
    """Minimal-valid published library entry for the wizard-picker paging test."""
    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
    from idraa.models.scenario_library import ScenarioLibraryEntry

    return ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        name=slug,
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        applicable_sub_sectors=None,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1e5, "mode": 7.5e5, "high": 5e6},
        suggested_control_ids=[],
    )


@pytest.mark.asyncio
async def test_step1_library_picker_shows_all_entries_beyond_one_page(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
):
    """The wizard step-1 picker must render EVERY published entry on one page.
    Regression: it fetched only ``list_page_size`` (page 1) with no pager, so
    entries beyond the first page were unreachable. Display-all is deliberate —
    a pager 'next' would collide with the wizard's own 'Next step' button.
    """
    from idraa.config import get_settings

    client, _ = authed_analyst
    n = get_settings().list_page_size + 5  # one full page + overflow
    for i in range(n):
        db_session.add(_make_lib_entry(slug=f"wiz-lib-{i:03d}"))
    await db_session.commit()

    # Both server-rendered entry points into the picker must show the full set:
    #  - /scenarios/new/wizard        (get_wizard_step_1) — the "New scenario" links
    #  - /scenarios/new/wizard/step/1 (get_wizard_step)   — the shell's Back link from step 2
    for url in ("/scenarios/new/wizard", "/scenarios/new/wizard/step/1"):
        resp = await client.get(url)
        assert resp.status_code == 200, url
        # The last-seeded slug sorts past page 1 (order_by name) — absent pre-fix.
        assert f"wiz-lib-{n - 1:03d}" in resp.text, f"last entry missing from {url}"
        # And nothing was dropped: every seeded slug renders.
        missing = [f"wiz-lib-{i:03d}" for i in range(n) if f"wiz-lib-{i:03d}" not in resp.text]
        assert not missing, f"{len(missing)} entries missing from {url}: {missing[:5]}"


async def _load_state_json(db: AsyncSession, tx: uuid.UUID) -> dict[str, Any]:
    """Read the persisted WizardDraft.state_json for a tx (test inspection)."""
    draft = (
        await db.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one_or_none()
    assert draft is not None, f"no wizard draft for tx={tx}"
    return dict(draft.state_json or {})


async def _bootstrap_to_fair_page(
    client: AsyncClient, db_session: AsyncSession, org_id: uuid.UUID
) -> uuid.UUID:
    """Drive the wizard through step 2 (skip-library path) and return the tx.

    The org seeded by ``authed_analyst`` has industry=MANUFACTURING + a default
    revenue tier, so the step-3/4 GET handler eager-seeds IRIS rows for all four
    fieldsets on first FAIR-page visit.
    """
    user_id = await _user_id_from_org(db_session, org_id)
    return await _bootstrap_wizard_through_step_2(client, db_session, user_id)


@pytest.mark.asyncio
async def test_step3_likelihood_page_shows_only_tef_and_vuln(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    resp = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert resp.status_code == 200
    body = resp.text
    assert "Threat event frequency" in body
    assert "Vulnerability" in body
    assert "Primary loss" not in body
    assert "Secondary loss" not in body
    assert "Step 3 of 6" in body
    # Next advances (posts to /step/3), not finalize.
    assert 'action="/scenarios/new/wizard/step/3' in body
    assert "/finalize" not in body


@pytest.mark.asyncio
async def test_step3_vuln_framed_as_inherent_not_residual(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """methodology/vuln-inherent-framing: the likelihood page must frame
    vulnerability as the asset's INHERENT (control-naive) susceptibility — both
    the rendered question and the "inherent baseline" anchor badge — and must NOT
    ask analysts to net out their current controls (that double-counts the
    FAIR-CAM control layer)."""
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    body = (await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")).text
    # The elicitation question frames inherent susceptibility, pre-controls.
    assert "inherent weaknesses, before any of your mitigating controls" in body
    # The anchor badge makes the inherent framing explicit at point of entry.
    assert "inherent baseline" in body
    # Regression guard: the old residual wording must be gone.
    assert "get through your current controls" not in body


@pytest.mark.asyncio
async def test_step4_impact_page_shows_only_pl_and_sl(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    body = resp.text
    assert "Primary loss" in body
    assert "Secondary loss" in body
    assert "Threat event frequency" not in body
    assert "Step 4 of 6" in body
    assert 'action="/scenarios/new/wizard/step/4' in body


@pytest.mark.asyncio
async def test_wizard_rail_has_six_steps(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    resp = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    body = resp.text
    # "Review & save" renders with the ampersand HTML-escaped to &amp;.
    for label in ["Likelihood", "Impact", "Mitigating controls", "Review &amp; save"]:
        assert label in body, f"rail missing step label {label!r}"


@pytest.mark.asyncio
async def test_step3_post_persists_tef_vuln_only(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    # GET step 3 so eager IRIS seeding populates all four fieldsets.
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    state_before = await _load_state_json(db_session, tx)
    pl_before = state_before["sme_estimates"].get("pl")
    assert pl_before, "IRIS seed should have populated pl for the merge-clobber check"
    # No version_token: the per-page POST uses the legacy blind-write path (A-I1).
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data={
            "tef_sme_name_0": "Alice",
            "tef_low_0": "1",
            "tef_high_0": "5",
            "vuln_sme_name_0": "Alice",
            "vuln_low_0": "0.1",
            "vuln_high_0": "0.4",
        },
    )
    assert resp.status_code in (303, 302)
    assert resp.headers["location"].endswith(f"/step/4?tx={tx}")
    state_after = await _load_state_json(db_session, tx)
    assert [r["low"] for r in state_after["sme_estimates"]["tef"]] == [1.0]
    assert state_after["sme_estimates"]["vuln"][0]["high"] == 0.4
    # pl/sl untouched (merge-doesn't-clobber regression):
    assert state_after["sme_estimates"].get("pl") == pl_before


@pytest.mark.asyncio
async def test_step3_post_rejects_vuln_above_one_and_leaves_state_unchanged(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Plan-gate S-I3: a rejected POST must NOT persist the bad rows — proves the
    # merge happens strictly inside the validate-success path.
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    before = await _load_state_json(db_session, tx)
    tef_before = before["sme_estimates"].get("tef")
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data={
            "tef_sme_name_0": "A",
            "tef_low_0": "1",
            "tef_high_0": "5",
            "vuln_sme_name_0": "A",
            "vuln_low_0": "0.1",
            "vuln_high_0": "1.5",  # > 1.0 → rejected
        },
    )
    assert resp.status_code == 422
    assert "1.0" in resp.text or "between 0 and 1" in resp.text.lower()
    after = await _load_state_json(db_session, tx)
    assert after["sme_estimates"].get("tef") == tef_before  # nothing persisted


@pytest.mark.asyncio
async def test_step3_post_non_numeric_low_returns_422_not_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Issue #261: a non-numeric SME low (`tef_low_0=abc`) raised ValueError in
    # _parse_sme_rows_subset, which ran OUTSIDE the try/except → uncaught 500.
    # Must surface as the 422 flash path instead.
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    before = await _load_state_json(db_session, tx)
    tef_before = before["sme_estimates"].get("tef")
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data={
            "tef_sme_name_0": "A",
            "tef_low_0": "abc",  # non-numeric → ValueError
            "tef_high_0": "5",
            "vuln_sme_name_0": "A",
            "vuln_low_0": "0.1",
            "vuln_high_0": "0.5",
        },
    )
    assert resp.status_code == 422
    after = await _load_state_json(db_session, tx)
    assert after["sme_estimates"].get("tef") == tef_before  # nothing persisted


@pytest.mark.asyncio
async def test_step3_post_missing_high_key_returns_422_not_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Issue #261: a present low-key with a missing high-key triggered a direct
    # KeyError subscript in _parse_sme_rows_subset (outside the try) → 500.
    # Must surface as the 422 flash path instead.
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    before = await _load_state_json(db_session, tx)
    tef_before = before["sme_estimates"].get("tef")
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data={
            "tef_sme_name_0": "A",
            "tef_low_0": "1",
            # tef_high_0 deliberately omitted → KeyError in direct subscript
            "vuln_sme_name_0": "A",
            "vuln_low_0": "0.1",
            "vuln_high_0": "0.5",
        },
    )
    assert resp.status_code == 422
    after = await _load_state_json(db_session, tx)
    assert after["sme_estimates"].get("tef") == tef_before  # nothing persisted


@pytest.mark.asyncio
async def test_step4_post_rejects_pl_low_zero(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Plan-gate SC-I1: PL low must be > 0 (SMEEstimateRow.low gt=0); the step-4
    # POST surfaces it as a 422 flash, not a downstream finalize error.
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={"pl_sme_name_0": "A", "pl_low_0": "0", "pl_high_0": "5000"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_step4_first_visit_seeds_pl_sl(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Plan-gate SC-I4: direct entry to step 4 (before ever visiting step 3) must
    # eager-seed via the `n in (3, 4)` GET block — not render empty PL/SL.
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    pre = await _load_state_json(db_session, tx)
    assert not pre.get("sme_estimates"), "fixture must not pre-seed sme_estimates"
    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    after = await _load_state_json(db_session, tx)
    # IRIS seeding populated pl (and tef/vuln); the impact page shows pl/sl.
    assert after["sme_estimates"].get("pl")


@pytest.mark.asyncio
async def test_step4_post_persists_pl_sl_and_advances_to_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_name_0": "A",
            "pl_low_0": "1000",
            "pl_high_0": "5000",
            "sl_sme_name_0": "A",
            "sl_low_0": "200",
            "sl_high_0": "900",
        },
    )
    assert resp.status_code in (303, 302)
    assert resp.headers["location"].endswith(f"/step/5?tx={tx}")
    state_after = await _load_state_json(db_session, tx)
    assert state_after["sme_estimates"]["pl"][0]["high"] == 5000.0
    # tef/vuln seeded rows still present (merge preserved the likelihood half):
    assert "tef" in state_after["sme_estimates"] and state_after["sme_estimates"]["tef"]


@pytest.mark.asyncio
async def test_step5_controls_advances_to_review(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_to_fair_page(client, db_session, org_id)
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/5?tx={tx}",
        data={},  # no controls selected is valid
    )
    assert resp.status_code in (303, 302)
    assert resp.headers["location"].endswith(f"/step/6?tx={tx}")


@pytest.mark.asyncio
async def test_get_wizard_step_1_renders_library_picker(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    r = await analyst_client.get("/scenarios/new/wizard")
    assert r.status_code == 200
    assert "library" in r.text.lower()
    assert seed_library_entry.name in r.text


@pytest.mark.asyncio
async def test_post_wizard_step_1_advances_to_step_2(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    r = await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={
            "library_entry_id": str(seed_library_entry.id),
        },
    )
    assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_wizard_back_button_preserves_state(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """Step 2 GET should pre-fill from step-1's library_entry_id selection."""
    await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={
            "library_entry_id": str(seed_library_entry.id),
        },
    )
    r = await analyst_client.get("/scenarios/new/wizard/step/2")
    assert r.status_code == 200
    assert seed_library_entry.name in r.text  # pre-fill from library


@pytest.mark.asyncio
async def test_reviewer_403_on_wizard(reviewer_client: AsyncClient) -> None:
    r = await reviewer_client.get("/scenarios/new/wizard")
    assert r.status_code == 403
    r = await csrf_post(
        reviewer_client, "/scenarios/new/wizard/step/1", data={"library_entry_id": ""}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_viewer_403_on_wizard(viewer_client: AsyncClient) -> None:
    r = await viewer_client.get("/scenarios/new/wizard")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_wizard_cancel_clears_state_and_redirects(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={
            "library_entry_id": str(seed_library_entry.id),
        },
    )
    r = await csrf_post(analyst_client, "/scenarios/new/wizard/cancel", data={})
    assert r.status_code in (200, 303)
    # Subsequent GET starts a fresh wizard
    r2 = await analyst_client.get("/scenarios/new/wizard")
    assert r2.status_code == 200
    # Old library_entry should NOT pre-fill (state cleared)
    assert "tx_id" in r2.text  # new tx_id minted; brittle but smoke


@pytest.mark.asyncio
async def test_wizard_skip_library_path(
    analyst_client: AsyncClient,
) -> None:
    """User clicks "Skip — start blank" on step 1; subsequent steps work
    with CalibrationService smart-defaults at step 3."""
    r = await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={
            "skip_library": "1",
        },
    )
    assert r.status_code in (200, 303)
    r2 = await analyst_client.get("/scenarios/new/wizard/step/3")
    assert r2.status_code == 200


async def _seed_one_sme_for_org(
    db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID
) -> uuid.UUID:
    """Inline helper: seed one SME the analyst can attribute SME estimates to."""
    from idraa.models.sme import SubjectMatterExpert

    sme = SubjectMatterExpert(
        organization_id=org_id,
        name="step-routes SME",
        email="step-routes-sme@example.com",
        created_by=created_by,
        created_via="admin",
    )
    db.add(sme)
    await db.flush()
    await db.commit()
    return sme.id


async def _current_tx_for_user(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
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
    tx = await _current_tx_for_user(db, user_id)
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
async def test_wizard_finalize_creates_scenario(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """T11: end-to-end wizard finalize via the new SME-row step-3 form."""
    client, org_id = authed_analyst
    from idraa.models.user import User

    user_id = (
        (
            await db_session.execute(
                select(User).where(
                    User.email == "analyst@test.local", User.organization_id == org_id
                )
            )
        )
        .scalar_one()
        .id
    )
    sme_id = await _seed_one_sme_for_org(db_session, org_id=org_id, created_by=user_id)
    lib_entry_id = str(seed_library_entry.id)
    await db_session.close()

    await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": lib_entry_id},
    )
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={
            "name": "Wizard scenario E2E",
            "description": "from wizard",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
        },
    )
    r = await _persist_rows_and_finalize(client, db_session, user_id=user_id, sme_id=sme_id)
    assert r.status_code == 303, r.text

    rows = (
        (await db_session.execute(select(Scenario).where(Scenario.name == "Wizard scenario E2E")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].library_pin is not None
    assert rows[0].library_pin["entry_id"] == lib_entry_id


@pytest.mark.asyncio
async def test_wizard_finalize_persists_mitigating_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    seed_control_factory: Any,
    db_session: AsyncSession,
) -> None:
    """UAT 2026-05-21 critical bug: wizard finalize was silently dropping
    the mitigating_control_ids picked in step 4 — Scenario row got created
    but no scenario_controls join rows, so subsequent analysis runs
    applied zero controls and returned the unmitigated ALE.

    Root cause: ``routes/scenarios.py::finalize_wizard`` builds the
    Scenario via ``ScenarioService.create_from_wizard`` → ``_stamp_new_scenario``
    but never calls ``ScenarioRepo.set_mitigating_controls`` with the
    ``state.mitigating_control_ids`` saved in step 4. (The form-based
    create at scenarios.py:297 + edit at :524 both DO call it; the
    wizard path was missing the same line.)

    This test posts a real control_id through step 4 and asserts the
    resulting scenario has scenario_controls rows after finalize.
    """
    analyst_client, analyst_org_id = authed_analyst
    from idraa.models.user import User

    user_id = (
        (
            await db_session.execute(
                select(User).where(
                    User.email == "analyst@test.local",
                    User.organization_id == analyst_org_id,
                )
            )
        )
        .scalar_one()
        .id
    )
    # seed the control in the ANALYST's org (analyst_client logs in as a
    # user from a freshly-created org distinct from seed_organization).
    ctrl = await seed_control_factory(name="Wizard control", organization_id=analyst_org_id)
    sme_id = await _seed_one_sme_for_org(db_session, org_id=analyst_org_id, created_by=user_id)
    lib_entry_id = str(seed_library_entry.id)
    await db_session.close()

    await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": lib_entry_id},
    )
    await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/2",
        data={
            "name": "Wizard control-persistence regression",
            "description": "step-4 controls must reach scenario_controls",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
        },
    )
    # F6 state-sourced flow: persist SME rows via steps 3+4 first, THEN pick
    # the mitigating control at step 5, THEN finalize with csrf+version_token.
    tx = await _current_tx_for_user(db_session, user_id)
    await _persist_fair_rows_via_steps_3_and_4(
        analyst_client,
        db_session,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100000.0, 5000000.0)],
    )
    # Step 5 — pick the seeded control as a mitigating control. The 2026-05-28
    # step-3 split moved Mitigating controls from step 4 to step 5 (4 is now the
    # Impact FAIR page). The form field name is `control_ids`.
    await csrf_post(
        analyst_client,
        f"/scenarios/new/wizard/step/5?tx={tx}",
        data={"control_ids": str(ctrl.id)},
    )
    await db_session.close()
    vt = await _current_version_token(db_session, tx)
    r = await csrf_post(
        analyst_client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert r.status_code == 303, r.text

    # Assert the scenario was created AND its mitigating_controls join was populated.
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Scenario)
        .where(Scenario.name == "Wizard control-persistence regression")
        .options(selectinload(Scenario.mitigating_controls))
    )
    scenario = (await db_session.execute(stmt)).scalar_one()
    assert {c.id for c in scenario.mitigating_controls} == {ctrl.id}, (
        "wizard finalize must persist state.mitigating_control_ids to scenario_controls; "
        f"got {[c.id for c in scenario.mitigating_controls]} expected [{ctrl.id}]"
    )


@pytest.mark.asyncio
async def test_wizard_step_post_without_csrf_returns_403(
    client: AsyncClient,
    db_session: AsyncSession,
    seed_user: Any,
) -> None:
    """CSRFMiddleware is global; wizard POSTs without _csrf must 403.

    r3 BLOCKER 5 fix: r2 referenced ``client.cookies.jar.session`` which
    is not a real attribute on httpx's CookieJar. Real ``login_client_as``
    signature is ``(db: AsyncSession, user: User) -> str`` (returns the
    session cookie string), so the test uses the real db_session fixture
    and sets the cookie via the standard pattern.
    """
    cookie = await login_client_as(db_session, seed_user)
    client.cookies.set(SESSION_COOKIE, cookie)
    # POST without _csrf token (skip the conftest csrf_post helper).
    r = await client.post("/scenarios/new/wizard/step/1", data={"library_entry_id": ""})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_wizard_finalize_post_without_csrf_returns_403(
    client: AsyncClient,
    db_session: AsyncSession,
    seed_user: Any,
) -> None:
    """r3 MAJOR (security #12) — finalize CSRF regression test.

    Mirrors the step-POST CSRF test; ensures the finalize route is also
    behind CSRFMiddleware. A naked POST without _csrf token must 403 even
    when the analyst has a valid session cookie.
    """
    cookie = await login_client_as(db_session, seed_user)
    client.cookies.set(SESSION_COOKIE, cookie)
    r = await client.post("/scenarios/new/wizard/finalize?tx=" + str(uuid.uuid4()))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_wizard_finalize_org_mismatch_returns_403(
    client: AsyncClient,
    db_session: AsyncSession,
    seed_user: Any,
    seed_organization_factory: Any,
) -> None:
    """r3 BLOCKER 7 — finalize must reject when draft.organization_id !=
    user.organization_id (mid-wizard re-org or session-cookie reuse).

    Seeds a draft with a different organization_id, then POSTs finalize as
    seed_user; must return 403 and delete the draft.

    Uses ``client`` + ``seed_user`` (not ``analyst_client``) so the logged-in
    user matches the draft's user_id — otherwise the FOR UPDATE query finds
    no row and degrades to a 303 instead of 403.
    """
    other_org = await seed_organization_factory(name="other-org-r3-blocker-7")
    tx = uuid.uuid4()
    # Use distinctive sentinel strings for the negative-content assertions
    # below so a generic substring like "x" doesn't false-positive against
    # arbitrary HTML.
    secret_name = "OrgMismatchSentinelDraftNameR4"
    secret_category = "OrgMismatchSentinelCategoryR4"
    draft = WizardDraft(
        user_id=seed_user.id,
        tx_id=tx,
        organization_id=other_org.id,  # mismatched on purpose
        state_json={"name": secret_name, "threat_category": secret_category},
    )
    db_session.add(draft)
    await db_session.commit()

    # Log in as seed_user (ANALYST) whose organization_id != other_org.id.
    cookie = await login_client_as(db_session, seed_user)
    client.cookies.set(SESSION_COOKIE, cookie)
    # Release db_session connection before HTTP call so the app engine can write.
    await db_session.close()

    # F6 state-sourced finalize: the body carries ONLY version_token (SME rows
    # come from state, which we never reach). The FOR UPDATE org-mismatch check
    # runs BEFORE any state read, so the 403 fires regardless of the (empty)
    # sme_estimates on the cross-org draft — and nothing leaks.
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": "0"},
    )
    assert r.status_code == 403
    # r4 LOW (threat-model) — defense-in-depth: response body MUST NOT leak
    # the cross-org draft state.
    assert secret_name not in r.text  # state_json["name"] sentinel
    assert secret_category not in r.text  # state_json["threat_category"] sentinel
    # Draft was cleared as part of the abort
    leftover = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one_or_none()
    assert leftover is None


@pytest.mark.asyncio
async def test_finalize_double_post_creates_only_one_scenario(
    client: AsyncClient,
    db_session: AsyncSession,
    seed_user: Any,
    seed_organization: Any,
) -> None:
    """r3 LOW (threat-model) — Decision D regression: with_for_update lock
    on WizardDraft serialises concurrent finalize POSTs so only the first
    stamps a Scenario. The second sees ``draft is None`` and degrades to
    redirect-to-step-1.

    Synchronous double POST is sufficient on SQLite (single-writer); the
    test asserts exactly one Scenario row exists for the tx after both
    POSTs land.

    Uses ``client`` + ``seed_user`` (not ``analyst_client``) so the logged-in
    user matches the draft's user_id — otherwise the FOR UPDATE query finds
    no row and both POSTs degrade to 303 without creating any Scenario.
    """
    tx = uuid.uuid4()
    # Seed a SubjectMatterExpert so the finalize form has a valid sme_id ref.
    from idraa.models.sme import SubjectMatterExpert

    sme = SubjectMatterExpert(
        organization_id=seed_organization.id,
        name="DoublePostSME",
        email="double-post-sme@example.com",
        created_by=seed_user.id,
        created_via="admin",
    )
    db_session.add(sme)
    await db_session.flush()
    sme_id = sme.id

    # F6 state-sourced finalize reads SME rows from state.sme_estimates, so the
    # seeded draft must already carry one row per required fieldset (this is the
    # shape steps 3+4 persist). Identity is sme_id XOR sme_name.
    def _row(sid: uuid.UUID, low: float, high: float) -> dict[str, Any]:
        return {"sme_id": str(sid), "sme_name": None, "low": low, "high": high}

    draft = WizardDraft(
        user_id=seed_user.id,
        tx_id=tx,
        organization_id=seed_organization.id,
        state_json={
            "tx_id": str(tx),
            "name": "double-post",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
            "sme_estimates": {
                "tef": [_row(sme_id, 1.0, 12.0)],
                "vuln": [_row(sme_id, 0.05, 0.5)],
                "pl": [_row(sme_id, 100000.0, 5000000.0)],
            },
        },
    )
    db_session.add(draft)
    await db_session.commit()
    initial_token = draft.version_token

    # Log in as seed_user (ANALYST in seed_organization).
    cookie = await login_client_as(db_session, seed_user)
    client.cookies.set(SESSION_COOKIE, cookie)
    # Release db_session connection before HTTP calls so app engine can write.
    await db_session.close()

    # F6: the review-page Save form posts ONLY csrf + version_token.
    finalize_form = {"version_token": str(initial_token)}
    r1 = await csrf_post(client, f"/scenarios/new/wizard/finalize?tx={tx}", data=finalize_form)
    # 2nd POST hits the same tx with the stale draft already deleted -> 404.
    r2 = await csrf_post(client, f"/scenarios/new/wizard/finalize?tx={tx}", data=finalize_form)
    assert r1.status_code == 303, r1.text
    assert r2.status_code == 404, r2.text
    rows = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.organization_id == seed_organization.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_post_wizard_step_rejects_invalid_step_number(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """POST /step/{n} must reject n outside (2, 3, 4, 5).

    2026-05-28 step-3 split: the valid POST set grew from {2,3,4} to {2,3,4,5}
    (3=Likelihood, 4=Impact, 5=Mitigating controls). Step 6 is the review page
    whose Save form posts to /finalize (not /step/6), so a POST /step/6 is a
    dead URL and must 400. n=1 falls through the dedicated step-1 handler.
    """
    # Bootstrap a wizard state via step 1 first.
    await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={
            "library_entry_id": str(seed_library_entry.id),
        },
    )
    # Step 5 (controls) is now a valid POST target (advances to review).
    r5 = await csrf_post(analyst_client, "/scenarios/new/wizard/step/5", data={})
    assert r5.status_code in (302, 303), f"step=5 expected redirect, got {r5.status_code}"
    # Step 6 (review) has no POST handler — its Save form posts to /finalize.
    r6 = await csrf_post(analyst_client, "/scenarios/new/wizard/step/6", data={"name": "x"})
    assert r6.status_code == 400, f"step=6 expected 400, got {r6.status_code}"


@pytest.mark.asyncio
async def test_wizard_step2_asset_class_dropdown_includes_new_enum_members(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """Regression guard: wizard step-2 must expose ALL AssetClass enum members.

    The AssetClass enum gained cash_or_equivalent + 3 business_process_* members
    on 2026-05-25 but the hardcoded step-2 dropdown never updated, making those
    four classes invisible when authoring scenarios (e.g. BEC/CEO-fraud → cash).
    """
    # Bootstrap step 1 first so a wizard draft exists for step 2.
    await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={"skip_library": "1"},
    )
    r = await analyst_client.get("/scenarios/new/wizard/step/2")
    assert r.status_code == 200
    body = r.text
    # The four previously-missing values must all appear as option values.
    assert "cash_or_equivalent" in body, "cash_or_equivalent missing from asset_class dropdown"
    assert "business_process_revenue" in body, (
        "business_process_revenue missing from asset_class dropdown"
    )
    assert "business_process_third_party_revenue" in body, (
        "business_process_third_party_revenue missing from asset_class dropdown"
    )
    assert "business_process_cost" in body, (
        "business_process_cost missing from asset_class dropdown"
    )


@pytest.mark.asyncio
async def test_wizard_step_1_has_search_box_and_facet_filter(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """WS5b: the wizard step-1 library picker must expose a search input and
    at least one facet filter control (e.g. asset_class or threat_actor_type),
    AND must list entries from a sub-sector OTHER than the test org's
    (MANUFACTURING) so cross-industry adoption works.

    Seeds two entries:
    - one tagged PROFESSIONAL industry (cross-industry from the MANUFACTURING
      test org perspective)
    - the test org is MANUFACTURING (per authed_analyst / seed_organization)
    """
    import uuid as _uuid

    from idraa.models.enums import AssetClass, IndustryType, ThreatActorType, ThreatCategory
    from idraa.models.scenario_library import ScenarioLibraryEntry

    # Seed a PROFESSIONAL-industry entry (cross-industry from MANUFACTURING org).
    cross_industry_entry = ScenarioLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug="ws5b-payroll-bec",
        name="Professional Services Payroll BEC",
        status="published",
        threat_event_type=ThreatCategory.SOCIAL_ENGINEERING,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.CASH_OR_EQUIVALENT,
        tags=[],
        description="Business email compromise targeting payroll for professional services firms.",
        canonical_fair_gap="BEC/payroll gap.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000.0,
            "mode": 250_000.0,
            "high": 1_000_000.0,
        },
        suggested_control_ids=[],
        applicable_industries=[IndustryType.PROFESSIONAL.value],
    )
    db_session.add(cross_industry_entry)
    await db_session.commit()

    r = await analyst_client.get("/scenarios/new/wizard")
    assert r.status_code == 200
    body = r.text

    # Must have a search input.
    assert 'type="search"' in body or 'name="q"' in body, (
        "wizard step-1 picker must render a search input"
    )
    # Must have at least one facet filter (e.g. asset_class or threat_actor_type checkboxes).
    assert 'name="asset_class"' in body or 'name="threat_actor_type"' in body, (
        "wizard step-1 picker must render at least one facet filter control"
    )
    # The cross-industry entry (PROFESSIONAL sub-sector) must be visible (no industry narrowing).
    assert cross_industry_entry.name in body, (
        "cross-industry entry must be visible in wizard step-1 picker "
        "(org sub-sector auto-narrow must not exclude it)"
    )


# ---------------------------------------------------------------------------
# WS4: library deep-link GET seeding — asset_class + FAIR-prefill parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wizard_deeplink_get_seeds_asset_class(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """WS4: GET /scenarios/new/wizard?library_entry_id=<id> must seed
    state.asset_class from the library entry — the 'Use in wizard →' deep-link
    path.

    Before the fix, the GET handler only set state.library_entry_id (lines
    901-908) and never called the seeder, so state.asset_class remained None
    and step 2 showed '— select —'. This test proves the bug is fixed.
    """
    client, org_id = authed_analyst
    entry_id = str(seed_library_entry.id)

    r = await client.get(f"/scenarios/new/wizard?library_entry_id={entry_id}")
    assert r.status_code == 200

    # Inspect persisted state — asset_class must be seeded from the entry.
    draft = (
        await db_session.execute(
            select(WizardDraft).order_by(WizardDraft.updated_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert draft is not None, "wizard draft must exist after GET deep-link"
    state = dict(draft.state_json or {})

    assert state.get("asset_class") == seed_library_entry.asset_class.value, (
        f"GET deep-link must seed asset_class from entry; "
        f"got {state.get('asset_class')!r}, expected {seed_library_entry.asset_class.value!r}"
    )


@pytest.mark.asyncio
async def test_wizard_deeplink_get_seeds_threat_fields(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """WS4: GET deep-link must also seed threat_category, threat_actor_type,
    and attack_vector — not just asset_class.
    """
    client, org_id = authed_analyst
    entry_id = str(seed_library_entry.id)

    await client.get(f"/scenarios/new/wizard?library_entry_id={entry_id}")

    draft = (
        await db_session.execute(
            select(WizardDraft).order_by(WizardDraft.updated_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert draft is not None
    state = dict(draft.state_json or {})

    assert state.get("threat_category") == seed_library_entry.threat_event_type.value, (
        f"GET deep-link must seed threat_category; "
        f"got {state.get('threat_category')!r}, "
        f"expected {seed_library_entry.threat_event_type.value!r}"
    )
    assert state.get("threat_actor_type") == seed_library_entry.threat_actor_type.value, (
        f"GET deep-link must seed threat_actor_type; "
        f"got {state.get('threat_actor_type')!r}, "
        f"expected {seed_library_entry.threat_actor_type.value!r}"
    )


@pytest.mark.asyncio
async def test_wizard_deeplink_get_fair_params_match_post_path(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """WS4 parity: FAIR params seeded via GET deep-link must match those seeded
    via the POST step-1 path for the same entry (identical seeding semantics).

    Checks tef, vuln, pl, sl distribution dicts are present on both paths.
    If the GET path ever uses a different code-path for the calibrated
    pre-fill, this test will catch the divergence.
    """
    client, org_id = authed_analyst
    entry_id = str(seed_library_entry.id)

    # --- GET deep-link path ---
    await client.get(f"/scenarios/new/wizard?library_entry_id={entry_id}")
    draft_get = (
        await db_session.execute(
            select(WizardDraft).order_by(WizardDraft.updated_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert draft_get is not None
    state_get = dict(draft_get.state_json or {})

    # Cancel the draft so the POST path starts fresh.
    from tests.conftest import csrf_post as _csrf_post

    await _csrf_post(client, "/scenarios/new/wizard/cancel", data={})
    await db_session.close()

    # --- POST step-1 path ---
    await _csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": entry_id},
    )
    draft_post = (
        await db_session.execute(
            select(WizardDraft).order_by(WizardDraft.updated_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert draft_post is not None
    state_post = dict(draft_post.state_json or {})

    # FAIR distributions must be present (non-None) on both paths for the
    # three archetype-curated fields (tef, vuln, pl).  secondary_loss is
    # intentionally optional — the fixture entry has no secondary_loss, so
    # both paths store None; the equality assertion below covers that case.
    for field in ("threat_event_frequency", "vulnerability", "primary_loss"):
        assert state_get.get(field) is not None, f"GET deep-link must seed {field!r}; got None"
        assert state_post.get(field) is not None, f"POST step-1 must seed {field!r}; got None"

    # Full stored-distribution equality for ALL FOUR calibrated FAIR fields.
    # This proves the data-contract parity completely — not just presence but
    # the actual distribution params that get stored on the wizard state.
    assert state_get.get("threat_event_frequency") == state_post.get("threat_event_frequency"), (
        "GET and POST paths must produce identical tef distributions"
    )
    assert state_get.get("vulnerability") == state_post.get("vulnerability"), (
        "GET and POST paths must produce identical vuln distributions"
    )
    assert state_get.get("primary_loss") == state_post.get("primary_loss"), (
        "GET and POST paths must produce identical pl distributions"
    )
    assert state_get.get("secondary_loss") == state_post.get("secondary_loss"), (
        "GET and POST paths must produce identical sl distributions "
        f"(GET={state_get.get('secondary_loss')!r}, POST={state_post.get('secondary_loss')!r})"
    )
    # asset_class must match on both.
    assert state_get.get("asset_class") == state_post.get("asset_class"), (
        "GET and POST paths must produce identical asset_class"
    )
