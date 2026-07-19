"""Finalize branch (update-in-place) for wizard re-elicitation (#56).

Walks a targeted draft (seeded by ``POST /scenarios/{id}/re-estimate``, Task
3) through steps 2-6 and finalize, reusing the ``_persist_fair_rows_via_steps_3_and_4``
/ ``_current_version_token`` step-walk idiom from
``tests/integration/_wizard_step3_test_helpers.py`` (the same helper
``test_wizard_finalize.py`` uses for the create path) and the
``_resolve_user_id`` / ``_draft_for_tx`` / ``_tx_from_location`` helpers from
Task 3's ``test_wizard_reestimate_routes.py``.

Every walk enters FRESH SME rows on every required fieldset regardless of
what the entry route rehydrated — this exercises the real "replace, don't
merge" contract (Task 4 test 2) uniformly across every test rather than
special-casing it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    EntityStatus,
    ScenarioFieldset,
    ScenarioSource,
    ScenarioType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.models.wizard_draft import WizardDraft
from idraa.repositories.scenario_repo import ScenarioRepo
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)
from tests.integration.test_wizard_reestimate_routes import (
    _draft_for_tx,
    _resolve_user_id,
    _tx_from_location,
)


def _seed_scenario(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    status: EntityStatus = EntityStatus.ACTIVE,
    source: ScenarioSource = ScenarioSource.EXPERT_JUDGMENT,
    library_pin: dict[str, Any] | None = None,
    vuln_framing: str = "inherent",
    conversion_metadata: dict[str, Any] | None = None,
) -> Scenario:
    """Local builder — needs provenance fields the ``test_scenario_routes``
    ``_seed_scenario`` doesn't expose (source / library_pin / vuln_framing /
    conversion_metadata), so this module has its own rather than extending
    that one's kwargs.
    """
    s = Scenario(
        organization_id=org_id,
        name=name,
        description="pre-existing description",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        attack_vector="phishing",
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=status,
        source=source,
        library_pin=library_pin,
        vuln_framing=vuln_framing,
        conversion_metadata=conversion_metadata,
    )
    db.add(s)
    return s


async def _seed_sme_row(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    scenario_id: uuid.UUID,
    fieldset: ScenarioFieldset,
    sme_name: str,
    low: float,
    high: float,
    recorded_at: datetime,
    recorded_by: uuid.UUID,
) -> None:
    db.add(
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=scenario_id,
            fieldset=fieldset,
            sme_id=None,
            sme_name=sme_name,
            low=low,
            high=high,
            recorded_at=recorded_at,
            recorded_by=recorded_by,
        )
    )


async def _post_re_estimate(client: AsyncClient, scenario_id: uuid.UUID) -> uuid.UUID:
    r = await csrf_post(client, f"/scenarios/{scenario_id}/re-estimate", {}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return uuid.UUID(_tx_from_location(r.headers["location"]))


async def _walk_reestimate_to_finalize(
    client: AsyncClient,
    db: AsyncSession,
    *,
    tx: uuid.UUID,
    scenario: Scenario,
    tef: list[tuple[str, float, float]],
    vuln: list[tuple[str, float, float]],
    pl: list[tuple[str, float, float]],
    sl: list[tuple[str, float, float]] | None = None,
    control_id: uuid.UUID | None = None,
) -> Any:
    """Walk a targeted draft from step 2 through finalize.

    Step 2 re-submits the SAME descriptive fields the entry route pre-filled
    (mirrors an analyst who lands on a pre-populated page and clicks
    through). Steps 3+4 always enter FRESH tef/vuln/pl rows via the shared
    helper. Step 5 is only visited when ``control_id`` is given (most tests
    don't touch mitigating controls).
    """
    step2_data = {
        "name": scenario.name,
        "description": scenario.description or "",
        "threat_category": scenario.threat_category.value,
        "threat_actor_type": (
            scenario.threat_actor_type.value if scenario.threat_actor_type else ""
        ),
        "asset_class": (scenario.asset_class.value if scenario.asset_class else ""),
        "attack_vector": scenario.attack_vector or "",
    }
    r2 = await csrf_post(
        client, f"/scenarios/new/wizard/step/2?tx={tx}", data=step2_data, follow_redirects=False
    )
    assert r2.status_code in (302, 303), r2.text

    await _persist_fair_rows_via_steps_3_and_4(client, db, tx, tef=tef, vuln=vuln, pl=pl, sl=sl)

    if control_id is not None:
        r5 = await csrf_post(
            client,
            f"/scenarios/new/wizard/step/5?tx={tx}",
            data={"control_ids": str(control_id)},
            follow_redirects=False,
        )
        assert r5.status_code in (302, 303), r5.text

    await db.close()
    vt = await _current_version_token(db, tx)
    return await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
        follow_redirects=False,
    )


# ---- 1: update in place ----------------------------------------------------


async def test_finalize_updates_target_in_place(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(
        db_session,
        org_id=org_id,
        name="Library-derived target",
        source=ScenarioSource.LIBRARY_DERIVED,
        library_pin={
            "entry_id": str(uuid.uuid4()),
            "version": 1,
            "override_id": None,
            "override_version": None,
        },
    )
    await db_session.commit()
    user_id = await _resolve_user_id(db_session, "analyst@test.local")

    now = datetime.now(UTC)
    await _seed_sme_row(
        db_session,
        org_id=org_id,
        scenario_id=s.id,
        fieldset=ScenarioFieldset.TEF,
        sme_name="Old TEF",
        low=0.1,
        high=2.0,
        recorded_at=now,
        recorded_by=user_id,
    )
    await _seed_sme_row(
        db_session,
        org_id=org_id,
        scenario_id=s.id,
        fieldset=ScenarioFieldset.VULN,
        sme_name="Old Vuln",
        low=0.1,
        high=0.5,
        recorded_at=now,
        recorded_by=user_id,
    )
    await _seed_sme_row(
        db_session,
        org_id=org_id,
        scenario_id=s.id,
        fieldset=ScenarioFieldset.PL,
        sme_name="Old PL",
        low=50_000.0,
        high=500_000.0,
        recorded_at=now,
        recorded_by=user_id,
    )
    await db_session.commit()

    tx = await _post_re_estimate(client, s.id)
    r = await _walk_reestimate_to_finalize(
        client,
        db_session,
        tx=tx,
        scenario=s,
        tef=[("New TEF", 1.0, 12.0)],
        vuln=[("New Vuln", 0.05, 0.5)],
        pl=[("New PL", 100_000.0, 5_000_000.0)],
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/scenarios/{s.id}"

    await db_session.close()
    all_scenarios = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org_id)))
        .scalars()
        .all()
    )
    assert len(all_scenarios) == 1, "re-estimate must not create a new scenario row"
    updated = all_scenarios[0]
    assert updated.id == s.id
    assert updated.row_version == 2
    assert updated.source == ScenarioSource.EXPERT_JUDGMENT
    assert updated.library_pin is None
    assert updated.vuln_framing == "inherent"
    assert updated.status == EntityStatus.ACTIVE  # unchanged
    # Distributions replaced: the fit sidecar only appears on wizard output,
    # never on the hand-seeded literal dict above.
    assert "distribution_fit_metadata" in updated.threat_event_frequency
    assert "distribution_fit_metadata" in updated.primary_loss


# ---- 2: SME rows replaced, not merged --------------------------------------


async def test_finalize_replaces_sme_rows(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Replace-rows target")
    await db_session.commit()
    user_id = await _resolve_user_id(db_session, "analyst@test.local")

    now = datetime.now(UTC)
    await _seed_sme_row(
        db_session,
        org_id=org_id,
        scenario_id=s.id,
        fieldset=ScenarioFieldset.TEF,
        sme_name="Old A",
        low=0.1,
        high=1.0,
        recorded_at=now,
        recorded_by=user_id,
    )
    await _seed_sme_row(
        db_session,
        org_id=org_id,
        scenario_id=s.id,
        fieldset=ScenarioFieldset.TEF,
        sme_name="Old B",
        low=0.2,
        high=1.5,
        recorded_at=now + timedelta(seconds=1),
        recorded_by=user_id,
    )
    await _seed_sme_row(
        db_session,
        org_id=org_id,
        scenario_id=s.id,
        fieldset=ScenarioFieldset.VULN,
        sme_name="Old Vuln",
        low=0.1,
        high=0.5,
        recorded_at=now,
        recorded_by=user_id,
    )
    await _seed_sme_row(
        db_session,
        org_id=org_id,
        scenario_id=s.id,
        fieldset=ScenarioFieldset.PL,
        sme_name="Old PL",
        low=50_000.0,
        high=500_000.0,
        recorded_at=now,
        recorded_by=user_id,
    )
    await db_session.commit()

    tx = await _post_re_estimate(client, s.id)
    r = await _walk_reestimate_to_finalize(
        client,
        db_session,
        tx=tx,
        scenario=s,
        tef=[("New A", 1.0, 5.0), ("New B", 2.0, 6.0), ("New C", 3.0, 7.0)],
        vuln=[("New Vuln", 0.05, 0.4)],
        pl=[("New PL", 100_000.0, 2_000_000.0)],
    )
    assert r.status_code == 303, r.text

    await db_session.close()
    rows = (
        (
            await db_session.execute(
                select(ScenarioSMEEstimate).where(
                    ScenarioSMEEstimate.scenario_id == s.id,
                    ScenarioSMEEstimate.fieldset == ScenarioFieldset.TEF,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3, "old rows must be gone, exactly the 3 new rows remain"
    assert {row.sme_name for row in rows} == {"New A", "New B", "New C"}


# ---- 3: row-version conflict -----------------------------------------------


async def test_finalize_conflict_renders_review_flash_and_preserves_draft(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Conflict target")
    await db_session.commit()
    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    original_primary_loss = dict(s.primary_loss)

    tx = await _post_re_estimate(client, s.id)

    # Simulate another user's concurrent edit landing between seed and finalize.
    s.row_version = 2
    db_session.add(s)
    await db_session.commit()

    r = await _walk_reestimate_to_finalize(
        client,
        db_session,
        tx=tx,
        scenario=s,
        tef=[("A", 1.0, 5.0)],
        vuln=[("A", 0.05, 0.4)],
        pl=[("A", 100_000.0, 1_000_000.0)],
    )
    assert r.status_code == 422, r.text
    assert "edited while you were estimating" in r.text

    await db_session.close()
    draft = await _draft_for_tx(db_session, user_id=user_id, tx=str(tx))
    assert draft is not None, "draft must survive a conflict-rejected finalize"

    refreshed = (await db_session.execute(select(Scenario).where(Scenario.id == s.id))).scalar_one()
    assert refreshed.row_version == 2  # not bumped again by the rejected attempt
    assert refreshed.primary_loss == original_primary_loss  # not overwritten


# ---- 4: register-import upgrade --------------------------------------------


async def test_finalize_register_import_upgrade(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(
        db_session,
        org_id=org_id,
        name="Register-import target",
        source=ScenarioSource.QUALITATIVE_REGISTER_IMPORT,
        vuln_framing="legacy_residual",
        conversion_metadata={"register_row_id": "R-42", "confidence": "medium"},
    )
    await db_session.commit()

    # No SME rows persisted for this scenario -> empty rehydration.
    tx = await _post_re_estimate(client, s.id)
    r = await _walk_reestimate_to_finalize(
        client,
        db_session,
        tx=tx,
        scenario=s,
        tef=[("A", 1.0, 6.0)],
        vuln=[("A", 0.05, 0.4)],
        pl=[("A", 100_000.0, 1_000_000.0)],
    )
    assert r.status_code == 303, r.text

    await db_session.close()
    updated = (await db_session.execute(select(Scenario).where(Scenario.id == s.id))).scalar_one()
    assert updated.source == ScenarioSource.EXPERT_JUDGMENT
    assert updated.vuln_framing == "inherent"
    assert updated.conversion_metadata is None  # amendment 14

    rows = (
        (
            await db_session.execute(
                select(ScenarioSMEEstimate).where(ScenarioSMEEstimate.scenario_id == s.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3  # tef + vuln + pl, one entered row each


# ---- 5: create path unchanged ----------------------------------------------


async def test_create_path_unchanged(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Regression guard: the plain (non-targeted) wizard flow must still
    CREATE a new scenario — the is_reestimate dispatch must not leak into
    the untargeted path. (test_wizard_finalize.py covers the create path's
    library_pin / audit-log contract in full; this pins the #56 boundary.)
    """
    client, org_id = authed_analyst
    user_id = await _resolve_user_id(db_session, "analyst@test.local")

    await csrf_post(client, "/scenarios/new/wizard/step/1", data={"skip_library": "1"})
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={
            "name": "Fresh create-path scenario",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
        },
    )
    tx_row = (
        await db_session.execute(
            select(WizardDraft)
            .where(WizardDraft.user_id == user_id)
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one()
    tx = tx_row.tx_id

    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[("A", 1.0, 12.0)],
        vuln=[("A", 0.05, 0.5)],
        pl=[("A", 100_000.0, 5_000_000.0)],
    )
    await db_session.close()
    vt = await _current_version_token(db_session, tx)
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    await db_session.close()
    created = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "Fresh create-path scenario")
            )
        )
        .scalars()
        .all()
    )
    assert len(created) == 1
    assert created[0].row_version == 1  # create path stamps a fresh row, not an update
    assert created[0].source == ScenarioSource.EXPERT_JUDGMENT


# ---- 6: DEPRECATED control link survives -----------------------------------


async def test_deprecated_control_link_survives_reestimate(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_control_factory: Any,
) -> None:
    client, org_id = authed_analyst
    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    s = _seed_scenario(db_session, org_id=org_id, name="Control-link survival target")
    await db_session.commit()

    ctrl_active = await seed_control_factory(
        name="Active control", organization_id=org_id, created_by=user_id
    )
    ctrl_deprecated = await seed_control_factory(
        name="Deprecated control", organization_id=org_id, created_by=user_id
    )

    await ScenarioRepo(db_session).set_mitigating_controls(
        scenario_id=s.id,
        organization_id=org_id,
        control_ids=[ctrl_active.id, ctrl_deprecated.id],
    )
    await db_session.commit()

    ctrl_deprecated.status = EntityStatus.DEPRECATED
    db_session.add(ctrl_deprecated)
    await db_session.commit()

    tx = await _post_re_estimate(client, s.id)
    # Only ctrl_active has a checkbox on the ACTIVE-only step-5 picker; the
    # analyst re-submits just that one selection.
    r = await _walk_reestimate_to_finalize(
        client,
        db_session,
        tx=tx,
        scenario=s,
        tef=[("A", 1.0, 6.0)],
        vuln=[("A", 0.05, 0.4)],
        pl=[("A", 100_000.0, 1_000_000.0)],
        control_id=ctrl_active.id,
    )
    assert r.status_code == 303, r.text

    await db_session.close()
    updated = (await db_session.execute(select(Scenario).where(Scenario.id == s.id))).scalar_one()
    linked_ids = {c.id for c in updated.mitigating_controls}
    assert linked_ids == {ctrl_active.id, ctrl_deprecated.id}, (
        "the DEPRECATED control's link must survive even though its "
        "checkbox never appeared on the step-5 picker"
    )


# ---- 7: cancel is a no-op --------------------------------------------------


async def test_cancel_targeted_draft_is_noop(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Cancel target")
    await db_session.commit()
    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    original_row_version = s.row_version

    tx = await _post_re_estimate(client, s.id)

    r = await csrf_post(client, f"/scenarios/new/wizard/cancel?tx={tx}", {}, follow_redirects=False)
    assert r.status_code == 303, r.text

    await db_session.close()
    draft = await _draft_for_tx(db_session, user_id=user_id, tx=str(tx))
    assert draft is None, "cancel must delete the targeted draft"

    refreshed = (await db_session.execute(select(Scenario).where(Scenario.id == s.id))).scalar_one()
    assert refreshed.row_version == original_row_version
