# tests/contracts/test_legacy_pert_scenarios_render.py
"""Legacy PERT-only scenarios (no distribution_fit_metadata sidecar) still
render on the detail page and produce a valid pyfair model (spec §9.2 §7.3).

Spec-2 / Spec-23: distribution_fit_metadata is OPTIONAL on the JSON column
so pre-T11 scenarios that were saved with just `{distribution, low, mode,
high}` keep working. This test pins that back-compat contract.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    EntityStatus,
    ScenarioSource,
    ScenarioType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario


@pytest.mark.asyncio
async def test_legacy_pert_only_scenario_renders_detail_page(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    authed_analyst: tuple[AsyncClient, Any],
) -> None:
    """Pre-T11 PERT-only scenario JSON shape still renders /scenarios/<id>.

    Build a Scenario row with the OLD JSON shape (no distribution_fit_metadata
    key on any distribution column) and confirm the detail-page GET returns 200.
    Regression guard for the T11 sidecar-additive change.
    """
    client, org_id = authed_analyst

    legacy_pert = {"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}
    legacy_vuln = {"distribution": "PERT", "low": 0.05, "mode": 0.1, "high": 0.2}
    legacy_pl = {"distribution": "PERT", "low": 1000.0, "mode": 5000.0, "high": 10000.0}

    scenario = Scenario(
        organization_id=org_id,
        name="legacy-pert-scenario",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        attack_vector="email",
        threat_event_frequency=legacy_pert,
        vulnerability=legacy_vuln,
        primary_loss=legacy_pl,
        secondary_loss=None,
        source=ScenarioSource.EXPERT_JUDGMENT,
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=seed_user.id,
    )
    db_session.add(scenario)
    await db_session.commit()
    sid = scenario.id
    await db_session.close()

    r = await client.get(f"/scenarios/{sid}")
    assert r.status_code == 200, r.text
    assert b"legacy-pert-scenario" in r.content
