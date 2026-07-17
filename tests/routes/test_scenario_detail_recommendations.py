"""Route tests for the scenario-detail recommended-controls nudge (P2c, Task 5).

A library-derived Scenario (``source=LIBRARY_DERIVED``, ``library_pin={entry_id, version}``)
re-resolves its source ScenarioLibraryEntry's ``suggested_control_ids`` into recommendations
and shows a nudge panel listing the UN-ADOPTED ones (spec §6.3). A custom scenario (no
``library_pin``) shows no panel; a library-derived scenario whose suggested controls are ALL
adopted by the org also shows no panel (the un-adopted list is empty → ``{% if recommendations %}``
guard renders nothing).

Fixtures seed into the authed analyst's own org so the org-scoped scenario lookup in
``view_scenario`` resolves; the catalog + scenario-library rows are canonical (not org-scoped).
``authed_client`` aliases ``analyst_client`` so the same authenticated session resolves both the
client arg and the org id used by the seed fixtures.
"""

from __future__ import annotations

import uuid as _uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_library import ControlLibraryEntry
from idraa.models.enums import (
    AssetClass,
    ControlSource,
    ControlType,
    EntityStatus,
    ScenarioSource,
    ScenarioType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry


@pytest_asyncio.fixture
async def authed_client(analyst_client: AsyncClient) -> AsyncClient:
    """Alias for ``analyst_client`` so the plan's ``authed_client`` arg resolves.
    Analyst role → ``can_adopt`` true, so the inline adopt form renders."""
    return analyst_client


def _catalog_entry(slug: str, name: str) -> ControlLibraryEntry:
    return ControlLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug=slug,
        name=name,
        description="m" * 25,
        control_type=ControlType.TECHNICAL,
        nist_csf_subcategories=[],
        cis_safeguards=[],
        iso_27001_controls=[],
        compliance_mappings={},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status="published",
    )


def _scenario_library_entry(suggested: list[str]) -> ScenarioLibraryEntry:
    return ScenarioLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug="ransomware-on-ehr",
        name="Ransomware on EHR",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d" * 25,
        canonical_fair_gap="g" * 25,
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=suggested,
    )


def _library_derived_scenario(
    org_id: _uuid.UUID, created_by: _uuid.UUID, entry: ScenarioLibraryEntry
) -> Scenario:
    return Scenario(
        organization_id=org_id,
        name="Ransomware on EHR (cloned)",
        scenario_type=ScenarioType.CUSTOM,
        source=ScenarioSource.LIBRARY_DERIVED,
        library_pin={"entry_id": str(entry.id), "version": entry.version},
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )


@pytest_asyncio.fixture
async def seed_library_scenario_with_recs(
    db_session: AsyncSession, authed_analyst: tuple[AsyncClient, _uuid.UUID]
) -> Scenario:
    """A library-derived Scenario in the analyst's org whose source library entry
    suggests "multi-factor-authentication" (a published catalog entry NOT adopted by the
    org) → the recommendation renders un-adopted."""
    _client, org_id = authed_analyst
    from sqlalchemy import select

    from idraa.models.user import User

    created_by = (
        await db_session.execute(select(User.id).where(User.organization_id == org_id).limit(1))
    ).scalar_one()

    catalog = _catalog_entry("multi-factor-authentication", "Multi-factor Authentication")
    entry = _scenario_library_entry(["multi-factor-authentication"])
    db_session.add(catalog)
    db_session.add(entry)
    await db_session.flush()
    scenario = _library_derived_scenario(org_id, created_by, entry)
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario


@pytest_asyncio.fixture
async def custom_scenario(
    db_session: AsyncSession, authed_analyst: tuple[AsyncClient, _uuid.UUID]
) -> Scenario:
    """A custom Scenario in the analyst's org with NO library_pin → no recommendation panel."""
    _client, org_id = authed_analyst
    from sqlalchemy import select

    from idraa.models.user import User

    created_by = (
        await db_session.execute(select(User.id).where(User.organization_id == org_id).limit(1))
    ).scalar_one()

    scenario = Scenario(
        organization_id=org_id,
        name="Hand-built custom scenario",
        scenario_type=ScenarioType.CUSTOM,
        source=ScenarioSource.EXPERT_JUDGMENT,
        library_pin=None,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000.0,
            "mode": 250_000.0,
            "high": 2_000_000.0,
        },
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario


@pytest_asyncio.fixture
async def seed_library_scenario_all_adopted(
    db_session: AsyncSession, authed_analyst: tuple[AsyncClient, _uuid.UUID]
) -> Scenario:
    """A library-derived Scenario whose source entry suggests one catalog control that the
    org HAS already adopted (a Control with ``source=LIBRARY_DERIVED`` +
    ``library_pin={entry_id: <catalog id>}``) → un-adopted list is empty → no panel."""
    _client, org_id = authed_analyst
    from sqlalchemy import select

    from idraa.models.user import User

    created_by = (
        await db_session.execute(select(User.id).where(User.organization_id == org_id).limit(1))
    ).scalar_one()

    catalog = _catalog_entry("multi-factor-authentication", "Multi-factor Authentication")
    entry = _scenario_library_entry(["multi-factor-authentication"])
    db_session.add(catalog)
    db_session.add(entry)
    await db_session.flush()

    adopted = Control(
        id=_uuid.uuid4(),
        organization_id=org_id,
        name="Multi-factor Authentication",
        type=ControlType.TECHNICAL,
        source=ControlSource.LIBRARY_DERIVED,
        library_pin={"entry_id": str(catalog.id), "version": catalog.version},
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=created_by,
    )
    db_session.add(adopted)

    scenario = _library_derived_scenario(org_id, created_by, entry)
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario


@pytest.mark.asyncio
async def test_library_scenario_shows_unadopted_recommendation(
    authed_client, seed_library_scenario_with_recs
):
    scenario = seed_library_scenario_with_recs
    r = await authed_client.get(f"/scenarios/{scenario.id}")
    assert r.status_code == 200
    assert b"Recommended controls" in r.content
    assert (
        b"Multi-factor Authentication" in r.content or b"multi-factor-authentication" in r.content
    )


@pytest.mark.asyncio
async def test_custom_scenario_shows_no_recommendation_panel(authed_client, custom_scenario):
    r = await authed_client.get(f"/scenarios/{custom_scenario.id}")
    assert b"Recommended controls" not in r.content  # no library_pin → no panel


@pytest.mark.asyncio
async def test_all_adopted_shows_no_panel(authed_client, seed_library_scenario_all_adopted):
    # §6.3 (Spec-NTH-1): when the org has adopted EVERY suggested control, the un-adopted
    # list is empty → the panel renders nothing (guards against a future filter inversion).
    scenario = seed_library_scenario_all_adopted
    r = await authed_client.get(f"/scenarios/{scenario.id}")
    assert b"Recommended controls" not in r.content
