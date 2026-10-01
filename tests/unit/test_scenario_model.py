"""Scenario ORM round-trip + enum constraint coverage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import ValidationError
from idraa.models.enums import (
    AssetClass,
    IndustryType,
    OrganizationSize,
    ScenarioSource,
    ScenarioType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.threat_community import canonical_threat_community_id
from idraa.schemas.scenario import ScenarioForm
from idraa.services.scenarios import ScenarioService


async def test_scenario_roundtrip_minimal_fields(db_session: AsyncSession) -> None:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()

    s = Scenario(
        organization_id=org.id,
        name="Ransomware via phishing",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        threat_community_provenance="unassigned",
    )
    db_session.add(s)
    await db_session.commit()

    fetched = (await db_session.execute(select(Scenario))).scalar_one()
    assert fetched.threat_category == ThreatCategory.RANSOMWARE
    assert fetched.source is ScenarioSource.EXPERT_JUDGMENT
    assert fetched.asset_class is None
    assert fetched.row_version == 1


async def test_scenario_source_enum_values(db_session: AsyncSession) -> None:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()

    s = Scenario(
        organization_id=org.id,
        name="Test",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        source=ScenarioSource.EXPERT_JUDGMENT,
        threat_community_provenance="unassigned",
    )
    db_session.add(s)
    await db_session.commit()

    fetched = (await db_session.execute(select(Scenario))).scalar_one()
    assert fetched.source is ScenarioSource.EXPERT_JUDGMENT
    assert fetched.source.value == "expert_judgment"


@pytest.mark.asyncio
async def test_scenario_mitigating_controls_default_empty(
    db_session: AsyncSession, seed_scenario_factory: Callable[..., Awaitable[Any]]
) -> None:
    """Newly created Scenario has no mitigating_controls."""
    scenario = await seed_scenario_factory(name="no-controls")
    await db_session.refresh(scenario, attribute_names=["mitigating_controls"])
    assert scenario.mitigating_controls == []


@pytest.mark.asyncio
async def test_scenario_library_pin_round_trips_dict(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """F4: library_pin JSON column round-trips a dict."""
    pin = {
        "entry_id": "00000000-0000-0000-0000-000000000001",
        "version": 1,
        "override_id": None,
        "override_version": None,
    }
    s = Scenario(
        organization_id=seed_organization.id,
        name="Pinned scenario",
        threat_category=ThreatCategory.MALWARE,
        threat_community_id=canonical_threat_community_id("nation_state"),
        threat_community_version=1,
        threat_community_provenance="assigned",
        asset_class=AssetClass.SYSTEMS,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        source=ScenarioSource.LIBRARY_DERIVED,
        library_pin=pin,
        created_by=seed_user.id,
    )
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)
    assert s.library_pin == pin
    assert s.source == ScenarioSource.LIBRARY_DERIVED


@pytest.mark.asyncio
async def test_scenario_library_pin_defaults_to_none(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """F4: library_pin defaults to NULL for expert-mode scenarios."""
    s = Scenario(
        organization_id=seed_organization.id,
        name="No-library scenario",
        threat_category=ThreatCategory.MALWARE,
        threat_community_id=canonical_threat_community_id("cybercriminals"),
        threat_community_version=1,
        threat_community_provenance="assigned",
        asset_class=AssetClass.SYSTEMS,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        source=ScenarioSource.EXPERT_JUDGMENT,
        created_by=seed_user.id,
    )
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)
    assert s.library_pin is None


@pytest.mark.asyncio
async def test_scenario_organization_relationship_returns_org(
    db_session: AsyncSession,
) -> None:
    """Scenario.organization returns the Organization row via the FK."""
    from decimal import Decimal

    org = Organization(
        name="Acme",
        industry_type=IndustryType.HEALTHCARE,
        organization_size=OrganizationSize.MEDIUM,
        annual_revenue=Decimal("4000000000"),
    )
    db_session.add(org)
    await db_session.flush()

    scenario = Scenario(
        organization_id=org.id,
        name="Test",
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "pert", "low": 0.1, "mode": 0.5, "high": 1.0},
        vulnerability={"distribution": "pert", "low": 0.1, "mode": 0.5, "high": 0.9},
        primary_loss={"distribution": "pert", "low": 1000.0, "mode": 5000.0, "high": 10000.0},
        threat_community_provenance="unassigned",
    )
    db_session.add(scenario)
    await db_session.flush()

    # Re-fetch via select exercises the configured lazy strategy
    # (lazy="select"); in-memory .organization access on the pre-refresh
    # instance would lazy-load via a separate query regardless.
    fetched = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario.id))
    ).scalar_one()
    assert fetched.organization.id == org.id
    assert fetched.organization.revenue_tier == "1b_to_10b"


# ---------------------------------------------------------------------------
# Task 1 — ScenarioEffect enum + effect column (indirect-attribution Slice 1)
# ---------------------------------------------------------------------------


def test_scenario_effect_enum_values() -> None:
    from idraa.models.enums import ScenarioEffect

    assert ScenarioEffect.CONFIDENTIALITY.value == "confidentiality"
    assert ScenarioEffect.INTEGRITY.value == "integrity"
    assert ScenarioEffect.AVAILABILITY.value == "availability"
    assert [e.value for e in ScenarioEffect] == [
        "confidentiality",
        "integrity",
        "availability",
    ]


async def test_scenario_effect_column_roundtrips_and_defaults_none(
    db_session: AsyncSession,
    seed_organization: Any,
) -> None:
    from idraa.models.enums import ScenarioEffect

    s = Scenario(
        organization_id=seed_organization.id,
        name="avail-scenario",
        threat_category=ThreatCategory.OT_AVAILABILITY,
        threat_event_frequency={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        effect=ScenarioEffect.AVAILABILITY,
        threat_community_provenance="unassigned",
    )
    db_session.add(s)
    await db_session.flush()
    await db_session.refresh(s)
    assert s.effect is ScenarioEffect.AVAILABILITY

    s2 = Scenario(
        organization_id=seed_organization.id,
        name="no-effect-scenario",
        threat_category=ThreatCategory.DATA_DISCLOSURE,
        threat_event_frequency={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        threat_community_provenance="unassigned",
    )
    db_session.add(s2)
    await db_session.flush()
    await db_session.refresh(s2)
    assert s2.effect is None


# ---------------------------------------------------------------------------
# Task 6 — Threat Agent Library: NULL<=>unassigned invariant + provenance_override
# ---------------------------------------------------------------------------


async def test_null_community_iff_unassigned_invariant(
    seed_organization: Any,
    seed_threat_communities: dict[str, Any],
) -> None:
    """``ScenarioService._assign_threat_community`` is the ONLY writer of the
    (threat_community_id, threat_community_version, threat_community_provenance)
    triple — None clears the pair to NULL + 'unassigned'; a row stamps the
    pair + 'assigned'."""
    s = Scenario(
        organization_id=seed_organization.id,
        name="Invariant scenario",
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        threat_community_id=canonical_threat_community_id("cybercriminals"),
        threat_community_version=1,
        threat_community_provenance="assigned",
    )

    ScenarioService._assign_threat_community(s, None)
    assert s.threat_community_id is None
    assert s.threat_community_version is None
    assert s.threat_community_provenance == "unassigned"

    row = seed_threat_communities["hacktivists"]
    ScenarioService._assign_threat_community(s, row)
    assert (s.threat_community_id, s.threat_community_version) == (row.id, row.version)
    assert s.threat_community_provenance == "assigned"


async def test_provenance_override_rejections(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_threat_communities: dict[str, Any],
) -> None:
    """``provenance_override`` is service-level only (no route passes it). All
    three rejection shapes raise ValidationError BEFORE ``self._db.add`` —
    nothing lands in the session."""

    def _base_form(threat_community: str | None) -> ScenarioForm:
        return ScenarioForm(
            name="S",
            threat_category="ransomware",
            threat_community=threat_community,
            threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.0},
            vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
            primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        )

    async def _nothing_pending() -> None:
        assert (await db_session.execute(select(Scenario))).scalars().first() is None

    svc = ScenarioService(db_session)

    # Case 1: a bogus provenance value altogether.
    with pytest.raises(ValidationError):
        await svc.create(
            organization_id=seed_organization.id,
            form=_base_form("hacktivists"),
            current_user=seed_user,
            provenance_override="bogus",
        )
    await _nothing_pending()

    # Case 2: 'unassigned' override with a valid (resolved) slug.
    with pytest.raises(ValidationError):
        await svc.create(
            organization_id=seed_organization.id,
            form=_base_form("hacktivists"),
            current_user=seed_user,
            provenance_override="unassigned",
        )
    await _nothing_pending()

    # Case 3: a non-'unassigned' override with a blank slug.
    with pytest.raises(ValidationError):
        await svc.create(
            organization_id=seed_organization.id,
            form=_base_form(None),
            current_user=seed_user,
            provenance_override="migrated_split_default",
        )
    await _nothing_pending()
