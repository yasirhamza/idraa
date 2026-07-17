"""Wizard finalize copies the pinned entry's curated ATT&CK mappings (issue #475 T10).

Setup mirrors tests/integration/test_wizard_finalize.py's
``test_wizard_finalize_with_library_records_library_pin``: seed a canonical
ScenarioLibraryEntry (via the ``seed_library_entry`` conftest fixture) + 3
curated ``ScenarioLibraryEntryAttackMapping`` rows, drive the wizard through
library selection (step 1) -> step 2 -> steps 3+4 (SME rows) -> finalize, then
assert the created scenario has all 3 mappings with source='library' and a
non-empty rationale (adapter-iteration contract, N>=3).

Also: an expert-mode (no library pin, ``skip_library``) finalize creates zero
mappings.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import ScenarioAttackMapping, ScenarioLibraryEntryAttackMapping
from idraa.models.scenario import Scenario
from idraa.models.sme import SubjectMatterExpert
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)
from tests.models.test_attack_models import _technique

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
        email="test-sme-attack-copy@example.com",
        created_by=created_by,
        created_via="admin",
    )
    db.add(sme)
    await db.flush()
    await db.commit()
    return sme.id


async def _current_tx(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
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


async def _seed_three_curated_mappings(
    db: AsyncSession, *, entry_id: uuid.UUID, entry_version: int
) -> None:
    """N>=3 adapter-iteration contract seed: 3 curated technique claims."""
    techs = [
        _technique(technique_id="T1566", name="Phishing", tactics=["initial-access"]),
        _technique(technique_id="T1486", name="Data Encrypted for Impact", tactics=["impact"]),
        _technique(technique_id="T1490", name="Inhibit System Recovery", tactics=["impact"]),
    ]
    db.add_all(techs)
    await db.flush()
    for t in techs:
        db.add(
            ScenarioLibraryEntryAttackMapping(
                library_entry_id=entry_id,
                library_entry_version=entry_version,
                technique_id=t.id,
                rationale=f"Curated: {t.name} is characteristic of this scenario family.",
                provenance="expert-estimate",
                citations=[],
            )
        )
    await db.commit()


async def _persist_rows_and_finalize(
    client: AsyncClient,
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    sme_id: uuid.UUID,
    tx: uuid.UUID,
) -> Any:
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
async def test_finalize_with_library_pin_copies_all_curated_mappings(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await _seed_three_curated_mappings(
        db_session, entry_id=seed_library_entry.id, entry_version=seed_library_entry.version
    )
    await db_session.close()

    await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(seed_library_entry.id)},
    )
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={"name": "Wizard scenario clone-copy T10", **_STEP_2_BASE},
    )
    tx = await _current_tx(db_session, user_id)
    r = await _persist_rows_and_finalize(client, db_session, user_id=user_id, sme_id=sme_id, tx=tx)
    assert r.status_code == 303, r.text

    scenario_rows = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "Wizard scenario clone-copy T10")
            )
        )
        .scalars()
        .all()
    )
    assert len(scenario_rows) == 1
    created_scenario_id = scenario_rows[0].id

    rows = (
        (
            await db_session.execute(
                select(ScenarioAttackMapping).where(
                    ScenarioAttackMapping.scenario_id == created_scenario_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3
    assert all(r.source == "library" for r in rows)
    assert all(r.rationale for r in rows)  # curated rationale copied


@pytest.mark.asyncio
async def test_finalize_expert_mode_no_pin_copies_zero_mappings(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """No library entry chosen (expert mode / skip_library) -> zero mappings."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_user_id(db_session)
    sme_id = await _seed_one_sme(db_session, org_id=org_id, created_by=user_id)
    await db_session.close()

    await csrf_post(client, "/scenarios/new/wizard/step/1", data={"skip_library": "1"})
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={"name": "Wizard scenario expert-mode T10", **_STEP_2_BASE},
    )
    tx = await _current_tx(db_session, user_id)
    r = await _persist_rows_and_finalize(client, db_session, user_id=user_id, sme_id=sme_id, tx=tx)
    assert r.status_code == 303, r.text

    scenario_rows = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "Wizard scenario expert-mode T10")
            )
        )
        .scalars()
        .all()
    )
    assert len(scenario_rows) == 1
    created_scenario_id = scenario_rows[0].id

    rows = (
        (
            await db_session.execute(
                select(ScenarioAttackMapping).where(
                    ScenarioAttackMapping.scenario_id == created_scenario_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []
