"""Concurrent wizard sessions, race-window edge cases."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.user import User

pytestmark = pytest.mark.asyncio


# E17: Two concurrent wizard tabs same user -> state isolation
async def test_two_concurrent_wizard_tx_isolated(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    """Two wizard tx_ids for the same user must have independent state.

    Simulates two browser tabs open simultaneously: tab 1 sets
    threat_category=ransomware, tab 2 sets threat_category=malware. After
    both advance, tab 1's state must still show ransomware.

    NOTE: industry/revenue_tier were removed from WizardState in issue #88
    Task 8 — calibration anchors are now read live from the org row.
    The isolation property is demonstrated via threat_category instead.
    """
    from idraa.services.wizard_state import WizardStateService

    svc = WizardStateService(db_session)

    # Tab 1: create a new state for threat_category=ransomware
    state_1 = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )
    state_1.threat_category = "ransomware"
    await svc.advance_step(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        state=state_1,
    )
    tx_id_1 = uuid.UUID(state_1.tx_id)

    # Tab 2: create a SEPARATE state for threat_category=malware (new tx_id minted)
    state_2 = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        tx_id=None,  # explicitly no tx_id: mint a new one
    )
    state_2.threat_category = "malware"
    await svc.advance_step(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        state=state_2,
    )
    await db_session.commit()

    # Tx IDs must be distinct
    assert state_1.tx_id != state_2.tx_id, (
        "Two independent get_or_create calls without tx_id must produce distinct tx_ids"
    )

    # Reload tab 1 by tx_id — threat_category must still be ransomware
    state_1_reloaded = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        tx_id=tx_id_1,
    )
    assert state_1_reloaded.threat_category == "ransomware", (
        f"tab 1 state was contaminated by tab 2: "
        f"threat_category={state_1_reloaded.threat_category!r}, expected 'ransomware'"
    )


# E19: Hard-deleted overlay race — Apply-overlay returns 404, not 500
async def test_apply_overlay_404_when_overlay_hard_deleted(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Hard-delete (DELETE FROM overlay_definitions) between page load and
    click must yield 404 from the apply-overlay endpoint, not a 500.
    """
    from sqlalchemy import delete

    from idraa.models.overlay import OverlayDefinition
    from tests.conftest import csrf_post
    from tests.integration._wizard_step3_test_helpers import (
        _bootstrap_wizard_through_step_2,
        _seed_overlay_inline,
        _user_id_from_org,
    )

    client, org_id = authed_analyst
    user_id = await _user_id_from_org(db_session, org_id)

    # Seed an overlay, then hard-delete it to simulate the race window.
    overlay = await _seed_overlay_inline(
        db_session,
        organization_id=org_id,
        tag="hard-deleted-overlay",
        display_name="Hard-deleted overlay",
    )
    overlay_id = overlay.id

    # Hard delete — delete revision first (FK), then the definition.
    from idraa.models.overlay import OverlayDefinitionRevision

    await db_session.execute(
        delete(OverlayDefinitionRevision).where(
            OverlayDefinitionRevision.overlay_definition_id == overlay_id
        )
    )
    await db_session.execute(delete(OverlayDefinition).where(OverlayDefinition.id == overlay_id))
    await db_session.commit()

    # Bootstrap the wizard (client uses its own DB engine connection)
    await db_session.close()
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)

    # 2026-05-28 step-3 split: apply-overlay reads rows from persisted state and
    # takes a `page` param (likelihood/impact); the dead PERT body fields are
    # gone. The hard-delete 404 fires on the overlay 404 check BEFORE the page
    # scoping, so the page value is incidental here — pass a valid one.
    resp = await csrf_post(
        client,
        "/scenarios/wizard/apply-overlay",
        data={
            "tx": str(tx),
            "overlay_id": str(overlay_id),
            "page": "likelihood",
        },
    )
    assert resp.status_code == 404, (
        f"expected 404 for hard-deleted overlay, got {resp.status_code}: {resp.text[:300]}"
    )
