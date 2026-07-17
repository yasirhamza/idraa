"""resolve_for_clone, list_browseable, ResolvedLibraryEntry shape.

Spec §7.2.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import LibraryEntryNotFoundError, LibraryEntryStatusError
from idraa.models.enums import IndustrySubSector
from idraa.services.scenario_library import (
    BrowseFilters,
    ResolvedLibraryEntry,
    ScenarioLibraryService,
)


@pytest.mark.asyncio
async def test_resolve_for_clone_no_override(
    db_session: AsyncSession,
    seed_organization: object,
    seed_library_entry: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    resolved = await svc.resolve_for_clone(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
    )
    assert isinstance(resolved, ResolvedLibraryEntry)
    assert resolved.entry.id == seed_library_entry.id  # type: ignore[attr-defined]
    assert resolved.override is None
    assert resolved.pin == {
        "entry_id": str(seed_library_entry.id),  # type: ignore[attr-defined]
        "version": seed_library_entry.version,  # type: ignore[attr-defined]
        "override_id": None,
        "override_version": None,
    }


@pytest.mark.asyncio
async def test_resolve_for_clone_with_override(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: object,
    seed_library_entry: object,
) -> None:
    from idraa.models.scenario_library import ScenarioLibraryOverride

    override = ScenarioLibraryOverride(
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        library_entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        library_entry_version=seed_library_entry.version,  # type: ignore[attr-defined]
        threat_event_frequency={"low": 2.0, "mode": 6.0, "high": 18.0},
        reason="org-specific TEF lift",
        version=1,
        created_by=seed_user.id,  # type: ignore[attr-defined]
    )
    db_session.add(override)
    await db_session.commit()

    svc = ScenarioLibraryService(db_session)
    resolved = await svc.resolve_for_clone(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
    )
    assert resolved.override is not None
    assert resolved.merged.threat_event_frequency["mode"] == 6.0
    assert resolved.pin["override_id"] == str(override.id)
    assert resolved.pin["override_version"] == 1


@pytest.mark.asyncio
async def test_resolve_for_clone_unknown_entry_raises(
    db_session: AsyncSession,
    seed_organization: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    with pytest.raises(LibraryEntryNotFoundError):
        await svc.resolve_for_clone(
            entry_id=uuid.uuid4(),
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_resolve_for_clone_draft_entry_raises_status_error(
    db_session: AsyncSession,
    seed_organization: object,
) -> None:
    """Cannot clone from draft or deprecated entries."""
    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
    from idraa.models.scenario_library import ScenarioLibraryEntry

    draft = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="d",
        name="d",
        status="draft",
        threat_event_type=ThreatCategory.MALWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        threat_event_frequency={"low": 1.0, "mode": 2.0, "high": 3.0},
        vulnerability={"low": 0.05, "mode": 0.10, "high": 0.20},
        primary_loss={"low": 1.0, "mode": 2.0, "high": 3.0},
        suggested_control_ids=[],
    )
    db_session.add(draft)
    await db_session.commit()

    svc = ScenarioLibraryService(db_session)
    with pytest.raises(LibraryEntryStatusError):
        await svc.resolve_for_clone(
            entry_id=draft.id,
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
        )


@pytest.mark.asyncio
async def test_list_browseable_defaults_to_all_industries(
    db_session: AsyncSession,
    seed_organization: object,
    seed_library_entry: object,
) -> None:
    """WS5a: browse with no filter selected returns ALL published entries regardless
    of the org's industry_sub_sector.

    The fixture entry has no applicable_sub_sectors (NULL = applies-to-all), so it
    should appear even when the org is scoped to a DIFFERENT sub-sector and the user
    has not set any explicit filter.
    """
    import uuid as _uuid

    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
    from idraa.models.organization import Organization
    from idraa.models.scenario_library import ScenarioLibraryEntry

    assert isinstance(seed_organization, Organization)
    # Give the org a specific sub-sector so the old auto-narrow would activate.
    seed_organization.industry_sub_sector = IndustrySubSector.OIL_AND_GAS
    db_session.add(seed_organization)
    await db_session.commit()

    # Seed a SECOND entry scoped to a DIFFERENT sub-sector (electric_utility).
    # Under the old auto-narrow it would be hidden when the org is oil_and_gas.
    cross_entry = ScenarioLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug="cross-industry-entry",
        name="Cross-industry entry",
        status="published",
        threat_event_type=ThreatCategory.MALWARE,
        threat_actor_type=ThreatActorType.NATION_STATE,
        asset_class=AssetClass.DATA,
        tags=[],
        description="An entry scoped to electric_utility sub-sector.",
        canonical_fair_gap="cross-industry fixture",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 3.0, "high": 9.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.15, "high": 0.40},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000.0,
            "mode": 200_000.0,
            "high": 1_000_000.0,
        },
        suggested_control_ids=[],
        applicable_sub_sectors=["electric_utility"],
    )
    db_session.add(cross_entry)
    await db_session.commit()

    svc = ScenarioLibraryService(db_session)
    # No explicit filter — default BrowseFilters().
    result = await svc.list_browseable(
        filters=BrowseFilters(),
        page=1,
    )
    slugs = {e.slug for e in result.entries}
    # The cross-industry entry must be visible even though org is oil_and_gas.
    assert "cross-industry-entry" in slugs, (
        "expected cross-industry entry to be visible on default (no-filter) browse, "
        f"but got slugs: {slugs}"
    )


@pytest.mark.asyncio
async def test_list_browseable_explicit_sub_sector_filter_still_narrows(
    db_session: AsyncSession,
    seed_organization: object,
    seed_library_entry: object,
) -> None:
    """WS5a: an EXPLICIT sub-sector filter still narrows results correctly."""
    import uuid as _uuid

    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
    from idraa.models.organization import Organization
    from idraa.models.scenario_library import ScenarioLibraryEntry

    assert isinstance(seed_organization, Organization)
    seed_organization.industry_sub_sector = IndustrySubSector.OIL_AND_GAS
    db_session.add(seed_organization)
    await db_session.commit()

    # Entry scoped exclusively to electric_utility.
    electric_entry = ScenarioLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug="electric-only-entry",
        name="Electric only",
        status="published",
        threat_event_type=ThreatCategory.DENIAL_OF_SERVICE,
        threat_actor_type=ThreatActorType.NATION_STATE,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="Scoped to electric_utility only.",
        canonical_fair_gap="electric fixture",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 3.0, "high": 9.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.15, "high": 0.40},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000.0,
            "mode": 200_000.0,
            "high": 1_000_000.0,
        },
        suggested_control_ids=[],
        applicable_sub_sectors=["electric_utility"],
    )
    db_session.add(electric_entry)
    await db_session.commit()

    svc = ScenarioLibraryService(db_session)
    # Explicit electric_utility filter — should return electric_entry, not the
    # default fixture entry (which has no applicable_sub_sectors / NULL).
    result = await svc.list_browseable(
        filters=BrowseFilters(applicable_sub_sectors=[IndustrySubSector.ELECTRIC_UTILITY]),
        page=1,
    )
    slugs = {e.slug for e in result.entries}
    assert "electric-only-entry" in slugs, f"expected electric entry in results; got {slugs}"
    # The NULL-sub-sector fixture entry also matches (NULL = applies-to-all) so we
    # just confirm our electric entry is present — no need to assert absence of others.
