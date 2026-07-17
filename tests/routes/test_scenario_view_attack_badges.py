"""Scenario detail page renders ATT&CK technique badges (issue #475 T11)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import ScenarioAttackMapping
from tests.models.test_attack_models import _technique


@pytest.mark.asyncio
async def test_view_renders_technique_badges(
    analyst_client: AsyncClient, db_session: AsyncSession, scenario_factory
):
    scenario = await scenario_factory()
    tech = _technique()
    db_session.add(tech)
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=tech.id,
            source="library",
        )
    )
    await db_session.commit()  # SC3-I1: route runs on a separate engine
    resp = await analyst_client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert "T1566" in resp.text
    assert "Phishing" in resp.text


@pytest.mark.asyncio
async def test_view_without_mappings_shows_empty_state(
    analyst_client: AsyncClient, db_session: AsyncSession, scenario_factory
):
    scenario = await scenario_factory()
    await db_session.commit()  # SC3-I1
    resp = await analyst_client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert "No ATT&amp;CK techniques tagged" in resp.text
