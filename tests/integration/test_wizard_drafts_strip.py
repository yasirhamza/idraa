"""Wizard drafts surfaced — /scenarios drafts strip (drafts-surfaced T3).

Spec §1: per-user, org-scoped, step>=2-only, cap-20-newest-first strip
between the page header and the status filter chips.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from tests.factories import create_user


async def _analyst_user(db: AsyncSession, org_id: uuid.UUID) -> User:
    """Resolve the ``authed_analyst``-minted session user (email is fixed)."""
    return (
        await db.execute(
            select(User).where(User.email == "analyst@test.local", User.organization_id == org_id)
        )
    ).scalar_one()


def _mk_draft(
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    current_step: int,
    name: str | None = "unset",
    target_scenario_id: str | None = None,
    updated_at: datetime.datetime | None = None,
) -> WizardDraft:
    tx = uuid.uuid4()
    state_json: dict[str, Any] = {"tx_id": str(tx), "current_step": current_step}
    if name != "unset":
        state_json["name"] = name
    if target_scenario_id is not None:
        state_json["target_scenario_id"] = target_scenario_id
    kwargs: dict[str, Any] = {
        "user_id": user_id,
        "tx_id": tx,
        "organization_id": organization_id,
        "state_json": state_json,
    }
    if updated_at is not None:
        kwargs["updated_at"] = updated_at
    return WizardDraft(**kwargs)


@pytest.mark.asyncio
async def test_strip_shows_own_drafts_only(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)
    other = await create_user(
        db_session, await db_session.get(Organization, org_id), email="other@test.local"
    )

    db_session.add(
        _mk_draft(user_id=analyst.id, organization_id=org_id, current_step=2, name="Mine")
    )
    db_session.add(
        _mk_draft(user_id=other.id, organization_id=org_id, current_step=2, name="TheirsHidden")
    )
    await db_session.commit()
    await db_session.close()

    resp = await client.get("/scenarios")
    assert resp.status_code == 200
    assert "Mine" in resp.text
    assert "TheirsHidden" not in resp.text


@pytest.mark.asyncio
async def test_strip_org_isolation(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Any,
) -> None:
    """DA-2: a same-user draft stamped with ANOTHER org must not render."""
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)
    other_org = await seed_organization_factory(name="drafts-strip-other-org")

    db_session.add(
        _mk_draft(
            user_id=analyst.id,
            organization_id=other_org.id,
            current_step=2,
            name="CrossOrgSentinel",
        )
    )
    await db_session.commit()
    await db_session.close()

    resp = await client.get("/scenarios")
    assert resp.status_code == 200
    assert "CrossOrgSentinel" not in resp.text


@pytest.mark.asyncio
async def test_strip_excludes_step_1_drafts(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """DA-1: never-advanced (current_step < 2) drafts are filtered out."""
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)

    db_session.add(
        _mk_draft(user_id=analyst.id, organization_id=org_id, current_step=1, name="NeverAdvanced")
    )
    db_session.add(
        _mk_draft(user_id=analyst.id, organization_id=org_id, current_step=2, name="Advanced")
    )
    await db_session.commit()
    await db_session.close()

    resp = await client.get("/scenarios")
    assert resp.status_code == 200
    assert "Advanced" in resp.text
    assert "NeverAdvanced" not in resp.text


@pytest.mark.asyncio
async def test_strip_caps_at_20_newest_first(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)

    base = now_utc()
    # i=0 is the most recent (base - 0 minutes); i=21 is the oldest.
    for i in range(22):
        db_session.add(
            _mk_draft(
                user_id=analyst.id,
                organization_id=org_id,
                current_step=2,
                name=f"Draft{i:02d}",
                updated_at=base - datetime.timedelta(minutes=i),
            )
        )
    await db_session.commit()
    await db_session.close()

    resp = await client.get("/scenarios")
    assert resp.status_code == 200
    body = resp.text
    for i in range(20):
        assert f"Draft{i:02d}" in body, f"Draft{i:02d} (newest 20) should render"
    for i in range(20, 22):
        assert f"Draft{i:02d}" not in body, f"Draft{i:02d} (beyond cap-20) should NOT render"
    # newest-first ordering
    assert body.index("Draft00") < body.index("Draft01") < body.index("Draft19")


@pytest.mark.asyncio
async def test_strip_name_fallback(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)

    db_session.add(_mk_draft(user_id=analyst.id, organization_id=org_id, current_step=2, name=None))
    await db_session.commit()
    await db_session.close()

    resp = await client.get("/scenarios")
    assert resp.status_code == 200
    assert "New scenario" in resp.text


@pytest.mark.asyncio
async def test_strip_reestimating_label_and_pin(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)

    db_session.add(
        _mk_draft(
            user_id=analyst.id,
            organization_id=org_id,
            current_step=3,
            name="ReestimateMe",
            target_scenario_id=uuid.uuid4().hex,
        )
    )
    await db_session.commit()
    await db_session.close()

    resp = await client.get("/scenarios")
    assert resp.status_code == 200
    assert "data-drafts-strip" in resp.text
    assert "re-estimating" in resp.text
    assert "Step 3 of 6" in resp.text


@pytest.mark.asyncio
async def test_strip_absent_when_zero_qualifying_drafts(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)
    # Only a step-1 (non-qualifying) draft exists.
    db_session.add(
        _mk_draft(user_id=analyst.id, organization_id=org_id, current_step=1, name="Ghost")
    )
    await db_session.commit()
    await db_session.close()

    resp = await client.get("/scenarios")
    assert resp.status_code == 200
    assert "data-drafts-strip" not in resp.text


# ---------------------------------------------------------------------------
# T4: re-estimation badge on the scenario page (spec §2, DA-5)
# ---------------------------------------------------------------------------


async def _seed_scenario(
    db: AsyncSession, org_id: uuid.UUID, *, name: str = "Badge scenario"
) -> Any:
    from idraa.models.enums import ScenarioSource, ScenarioType
    from idraa.models.scenario import Scenario

    sc = Scenario(
        organization_id=org_id,
        name=name,
        threat_category="malware",
        scenario_type=ScenarioType.CUSTOM,
        source=ScenarioSource.EXPERT_JUDGMENT,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 1000, "mode": 5000, "high": 20000},
        version="1.0",
    )
    db.add(sc)
    await db.flush()
    return sc


@pytest.mark.asyncio
async def test_badge_shown_for_targeting_draft(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)
    scenario = await _seed_scenario(db_session, org_id)
    draft = _mk_draft(
        user_id=analyst.id,
        organization_id=org_id,
        current_step=3,
        target_scenario_id=scenario.id.hex,
    )
    db_session.add(draft)
    await db_session.commit()
    tx_id = str(draft.tx_id)
    await db_session.close()

    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    body = resp.text
    assert "data-reestimate-draft-badge" in body
    assert f"tx={tx_id}" in body  # Resume link
    assert "Discard" in body


@pytest.mark.asyncio
async def test_badge_absent_without_targeting_draft(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario(db_session, org_id)
    await db_session.commit()
    await db_session.close()

    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert "data-reestimate-draft-badge" not in resp.text


@pytest.mark.asyncio
async def test_badge_newest_of_two_wins(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)
    scenario = await _seed_scenario(db_session, org_id)
    now = now_utc()
    older = _mk_draft(
        user_id=analyst.id,
        organization_id=org_id,
        current_step=2,
        target_scenario_id=scenario.id.hex,
        updated_at=now - datetime.timedelta(minutes=10),
    )
    newer = _mk_draft(
        user_id=analyst.id,
        organization_id=org_id,
        current_step=3,
        target_scenario_id=scenario.id.hex,
        updated_at=now,
    )
    db_session.add(older)
    db_session.add(newer)
    await db_session.commit()
    older_tx, newer_tx = str(older.tx_id), str(newer.tx_id)
    await db_session.close()

    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    body = resp.text
    assert f"tx={newer_tx}" in body
    assert f"tx={older_tx}" not in body


@pytest.mark.asyncio
async def test_badge_step1_targeting_draft_does_not_badge(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    analyst = await _analyst_user(db_session, org_id)
    scenario = await _seed_scenario(db_session, org_id)
    db_session.add(
        _mk_draft(
            user_id=analyst.id,
            organization_id=org_id,
            current_step=1,
            target_scenario_id=scenario.id.hex,
        )
    )
    await db_session.commit()
    await db_session.close()

    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert "data-reestimate-draft-badge" not in resp.text
