"""Entry route + button gating for wizard re-elicitation (#56).

Reuses the ``authed_analyst`` / ``authed_reviewer`` fixtures and
``_seed_scenario`` builder from ``tests/integration/test_scenario_routes.py``
(imported directly — mirrors the ``test_draft_workflow.py`` precedent of
importing ``_seed_scenario`` across test modules rather than duplicating it).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import ScenarioFieldset
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.models.sme import SubjectMatterExpert
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from idraa.services.wizard_state import load_sme_rows
from tests.conftest import csrf_post
from tests.integration.test_scenario_routes import _seed_scenario


async def _resolve_user_id(db: AsyncSession, email: str) -> uuid.UUID:
    """Mirrors ``_resolve_analyst_user_id`` in
    ``test_wizard_review_renders_sme_estimates.py`` — resolve the seeded
    analyst's user id so ``recorded_by`` / ``created_by`` FKs on
    directly-inserted rows are valid, and so ``WizardDraft`` can be looked
    up by its composite (user_id, tx_id) key.
    """
    row = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    assert row is not None
    return row.id


async def _seed_sme(db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
    sme = SubjectMatterExpert(
        organization_id=org_id,
        name="Erin SME",
        email="erin-sme@example.com",
        created_by=created_by,
        created_via="admin",
    )
    db.add(sme)
    await db.flush()
    return sme.id


async def _draft_for_tx(db: AsyncSession, *, user_id: uuid.UUID, tx: str) -> WizardDraft | None:
    return (
        await db.execute(
            select(WizardDraft).where(
                WizardDraft.user_id == user_id,
                WizardDraft.tx_id == uuid.UUID(tx),
            )
        )
    ).scalar_one_or_none()


def _tx_from_location(location: str) -> str:
    m = re.match(r"^/scenarios/new/wizard/step/2\?tx=([0-9a-f-]+)$", location)
    assert m, location
    return m.group(1)


# ---- 1/2: button gating -------------------------------------------------


async def test_reestimate_button_rendered_for_analyst(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="RW")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200
    assert f'action="/scenarios/{s.id}/re-estimate"' in r.text


async def test_reestimate_button_absent_for_reviewer(
    authed_reviewer: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_reviewer
    s = _seed_scenario(db_session, org_id=org_id, name="RW")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200
    assert f'action="/scenarios/{s.id}/re-estimate"' not in r.text


# ---- 3: seeds draft + redirects -----------------------------------------


async def test_post_seeds_draft_and_redirects_to_step_2(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="RW")
    await db_session.commit()

    r = await csrf_post(client, f"/scenarios/{s.id}/re-estimate", {}, follow_redirects=False)
    assert r.status_code == 303
    tx = _tx_from_location(r.headers["location"])

    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    row = await _draft_for_tx(db_session, user_id=user_id, tx=tx)
    assert row is not None
    assert row.state_json["target_scenario_id"] == s.id.hex
    assert row.state_json["target_expected_row_version"] == s.row_version
    assert row.state_json["current_step"] == 2
    assert row.state_json["name"] == s.name


# ---- 4: SME row rehydration ----------------------------------------------


async def test_post_rehydrates_sme_rows(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="RW")
    await db_session.commit()

    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    sme_id = await _seed_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.commit()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            ScenarioSMEEstimate(
                organization_id=org_id,
                scenario_id=s.id,
                fieldset=ScenarioFieldset.TEF,
                sme_id=None,
                sme_name="Alice",
                low=0.1,
                high=2.0,
                recorded_at=now,
                recorded_by=user_id,
            ),
            ScenarioSMEEstimate(
                organization_id=org_id,
                scenario_id=s.id,
                fieldset=ScenarioFieldset.TEF,
                sme_id=None,
                sme_name="Bob",
                low=0.2,
                high=3.0,
                recorded_at=now + timedelta(seconds=1),
                recorded_by=user_id,
            ),
            ScenarioSMEEstimate(
                organization_id=org_id,
                scenario_id=s.id,
                fieldset=ScenarioFieldset.TEF,
                sme_id=sme_id,
                sme_name=None,
                low=0.3,
                high=4.0,
                recorded_at=now + timedelta(seconds=2),
                recorded_by=user_id,
            ),
        ]
    )
    await db_session.commit()

    r = await csrf_post(client, f"/scenarios/{s.id}/re-estimate", {}, follow_redirects=False)
    assert r.status_code == 303
    tx = _tx_from_location(r.headers["location"])

    row = await _draft_for_tx(db_session, user_id=user_id, tx=tx)
    assert row is not None
    tef_rows = row.state_json["sme_estimates"]["tef"]
    # N>=3 adapter-iteration contract: all 3 rows survive, identity keys intact.
    assert len(tef_rows) == 3
    named = {est["sme_name"] for est in tef_rows if "sme_name" in est}
    assert named == {"Alice", "Bob"}
    ided = [est["sme_id"] for est in tef_rows if "sme_id" in est]
    assert ided == [str(sme_id)]


# ---- 5: cross-org 404 -----------------------------------------------------


async def test_post_404_on_other_org_scenario(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    from tests.factories import create_org

    client, _org_id = authed_analyst
    other_org = await create_org(db_session, name="Other Org")
    other_scenario = _seed_scenario(db_session, org_id=other_org.id, name="cross-org-secret")
    await db_session.commit()

    r = await csrf_post(
        client, f"/scenarios/{other_scenario.id}/re-estimate", {}, follow_redirects=False
    )
    assert r.status_code == 404


# ---- 6: step-3 renders seeded rows ---------------------------------------


async def test_wizard_step_renders_seeded_rows(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="RW")
    await db_session.commit()

    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    db_session.add(
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=s.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=None,
            sme_name="Dana Estimator",
            low=0.1,
            high=2.0,
            recorded_at=datetime.now(UTC),
            recorded_by=user_id,
        )
    )
    await db_session.commit()

    r = await csrf_post(client, f"/scenarios/{s.id}/re-estimate", {}, follow_redirects=False)
    assert r.status_code == 303
    tx = _tx_from_location(r.headers["location"])

    # state.sme_estimates is already seeded non-empty, so the step-3 GET's
    # eager IRIS-seed guard (``if not state.sme_estimates``) does not fire —
    # no need to walk/submit step 2 first before jumping to step 3.
    step3 = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert step3.status_code == 200
    assert "Dana Estimator" in step3.text


# ---- amendment 11: direct load_sme_rows contract test ---------------------


async def test_load_sme_rows_contract_grouping_ordering_and_row_survival(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Arch-N3: db-backed contract test for ``load_sme_rows`` directly.

    2 fieldsets, >=3 rows each, mixed sme_id/sme_name identities. Asserts
    grouping (fieldset -> rows), ordering (recorded_at then id), and full
    row survival — the adapter-iteration contract from CLAUDE.md's "Data
    contract enforcement" policy.

    Disclosed deviation from the plan's "mixed sme_id/sme_name" framing: only
    ONE row (of 6) uses the sme_id FK path via a directly-seeded
    SubjectMatterExpert row; the remaining 5 use sme_name. A single FK row
    is sufficient to exercise the identity branch without standing up a
    full SME-directory fixture per row (plan explicitly sanctions this
    fallback split when full SME-directory seeding is heavyweight).
    """
    _client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="LoadRows")
    await db_session.commit()

    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    sme_id = await _seed_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.commit()

    base = datetime.now(UTC)
    rows = [
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=s.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=None,
            sme_name="TEF Alice",
            low=0.1,
            high=1.0,
            recorded_at=base,
            recorded_by=user_id,
        ),
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=s.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=None,
            sme_name="TEF Bob",
            low=0.2,
            high=1.5,
            recorded_at=base + timedelta(seconds=1),
            recorded_by=user_id,
        ),
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=s.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=sme_id,
            sme_name=None,
            low=0.3,
            high=2.0,
            recorded_at=base + timedelta(seconds=2),
            recorded_by=user_id,
        ),
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=s.id,
            fieldset=ScenarioFieldset.PL,
            sme_id=None,
            sme_name="PL Carol",
            low=1_000.0,
            high=5_000.0,
            recorded_at=base + timedelta(seconds=3),
            recorded_by=user_id,
        ),
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=s.id,
            fieldset=ScenarioFieldset.PL,
            sme_id=None,
            sme_name="PL Dave",
            low=2_000.0,
            high=6_000.0,
            recorded_at=base + timedelta(seconds=4),
            recorded_by=user_id,
        ),
        ScenarioSMEEstimate(
            organization_id=org_id,
            scenario_id=s.id,
            fieldset=ScenarioFieldset.PL,
            sme_id=None,
            sme_name="PL Eve",
            low=3_000.0,
            high=7_000.0,
            recorded_at=base + timedelta(seconds=5),
            recorded_by=user_id,
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    result = await load_sme_rows(db_session, s.id, org_id)

    # Grouping: exactly the two fieldsets with rows are present.
    assert set(result) == {"tef", "pl"}
    assert len(result["tef"]) == 3
    assert len(result["pl"]) == 3

    # Ordering (recorded_at then id) + full row survival, including the
    # sme_id XOR sme_name identity shape per row.
    assert [r.get("sme_name") for r in result["tef"]] == ["TEF Alice", "TEF Bob", None]
    assert result["tef"][2]["sme_id"] == str(sme_id)
    assert "sme_name" not in result["tef"][2]
    assert [r["low"] for r in result["tef"]] == [0.1, 0.2, 0.3]
    assert [r["high"] for r in result["tef"]] == [1.0, 1.5, 2.0]

    assert [r["sme_name"] for r in result["pl"]] == ["PL Carol", "PL Dave", "PL Eve"]
    assert [r["low"] for r in result["pl"]] == [1_000.0, 2_000.0, 3_000.0]
    assert [r["high"] for r in result["pl"]] == [5_000.0, 6_000.0, 7_000.0]


# ---- Task 5: UI copy (shell + review step) ---------------------------------
#
# Both tests below need only the entry route + a direct GET of a step page —
# no full step-walk to finalize is required to assert on rendered copy, so
# they land here (alongside test_wizard_step_renders_seeded_rows, which is
# the same "POST re-estimate then GET a step" idiom) rather than in
# test_wizard_reestimate_finalize.py, whose helpers are built around walking
# a draft all the way to finalize.


async def test_shell_shows_reestimating_title(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Reestimate Title Target")
    await db_session.commit()

    r = await csrf_post(client, f"/scenarios/{s.id}/re-estimate", {}, follow_redirects=False)
    assert r.status_code == 303
    tx = _tx_from_location(r.headers["location"])

    step2 = await client.get(f"/scenarios/new/wizard/step/2?tx={tx}")
    assert step2.status_code == 200
    assert "Re-estimating:" in step2.text
    assert "Reestimate Title Target" in step2.text

    # A fresh, untargeted draft must never show the re-estimating banner.
    fresh = await client.get("/scenarios/new/wizard")
    assert fresh.status_code == 200
    assert "Re-estimating:" not in fresh.text


async def test_review_step_states_update_semantics(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Review Semantics Target")
    await db_session.commit()
    user_id = await _resolve_user_id(db_session, "analyst@test.local")

    r = await csrf_post(client, f"/scenarios/{s.id}/re-estimate", {}, follow_redirects=False)
    assert r.status_code == 303
    tx = _tx_from_location(r.headers["location"])

    step6 = await client.get(f"/scenarios/new/wizard/step/6?tx={tx}")
    assert step6.status_code == 200
    assert "replaces the estimates" in step6.text
    assert "math may have been updated" in step6.text

    # Untargeted (create-path) draft: the update-semantics warning is absent.
    fresh = await client.get("/scenarios/new/wizard")
    assert fresh.status_code == 200
    fresh_draft = (
        await db_session.execute(
            select(WizardDraft)
            .where(WizardDraft.user_id == user_id)
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one()
    fresh_step6 = await client.get(f"/scenarios/new/wizard/step/6?tx={fresh_draft.tx_id}")
    assert fresh_step6.status_code == 200
    assert "replaces the estimates" not in fresh_step6.text
    assert "math may have been updated" not in fresh_step6.text
