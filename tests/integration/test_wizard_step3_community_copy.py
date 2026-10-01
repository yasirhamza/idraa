"""TAL Task 7 review (SC-I1): route-level wiring test for community-specific
question copy + the TEF tooltip on the step-3 (Likelihood) wizard page.

Pre-Task-7, `_build_rendered_questions` passed `threat_actor_type=None`
unconditionally (a TAL bridge), so every wizard-route test would still pass
even if `_fair_page_context` silently dropped the resolved `community` and
passed `None` through — no test outside `tests/services/test_wizard_questions.py`
ever asserted the RENDERED PAGE carries community-specific wording. This test
closes that gap: it bootstraps a real draft through step 2 with a specific
threat_community, GETs the rendered step-3 page, and asserts the page's own
HTML contains the community-conditioned question text AND the community's
curated `threat_event_definition` concatenated into the TEF tooltip (M-I1).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.user import User
from tests.integration._wizard_step3_test_helpers import _bootstrap_wizard_through_step_2


async def _analyst_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    row = (
        await db.execute(
            select(User).where(User.organization_id == org_id, User.email == "analyst@test.local")
        )
    ).scalar_one()
    return row.id


@pytest.mark.asyncio
async def test_step3_page_carries_non_malicious_community_copy_and_tef_tooltip(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_threat_communities: dict,
) -> None:
    """insider_accidental (non_malicious): error-framed TEF question text,
    the community's own curated threat_event_definition, AND the SME
    elicitation convention (5%/95%, per year) all reach the rendered page."""
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(
        client, db_session, user_id, threat_community="insider_accidental"
    )

    r = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert r.status_code == 200, r.text
    body = r.text

    # Non-malicious TEF question copy (render_question / ScenarioContext wiring).
    assert "make an error involving" in body

    # M-I1: the TEF tooltip concatenates the community's curated
    # threat_event_definition onto (not instead of) the SME convention text.
    community = seed_threat_communities["insider_accidental"]
    assert community.threat_event_definition in body
    assert "5%" in body and "95%" in body
    assert "threat events per year" in body


@pytest.mark.asyncio
async def test_step3_page_carries_malicious_community_copy(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_threat_communities: dict,
) -> None:
    """cybercriminals (malicious): the "try to compromise" TEF question text
    and the community's humanized name reach the rendered page."""
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(
        client, db_session, user_id, threat_community="cybercriminals"
    )

    r = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert r.status_code == 200, r.text
    body = r.text

    assert "try to compromise" in body
    # humanize_threat_community("cybercriminals", name) -> "cybercriminals"
    # (the curated phrase dict wins over the seeded row's own Name casing).
    assert "cybercriminals" in body

    # M-I1: the TEF tooltip still carries this community's own curated
    # threat_event_definition concatenated with the SME convention text.
    community = seed_threat_communities["cybercriminals"]
    assert community.threat_event_definition in body
    assert "5%" in body and "95%" in body
