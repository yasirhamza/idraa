"""ScenarioLibraryOverride — per-org override layer for ScenarioLibraryEntry.

Spec: §6.1 — composite FK to (entry_id, version); (org_id, library_entry_id) unique;
override fields all nullable (resolution: if override is non-null, use it).

Spec: §12.1 — TOMBSTONE policy (override soft-delete preserves the row for pin lookup).
Tombstone deferred to F9; F3 keeps schema minimal (no deleted_at column).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.scenario_library import ScenarioLibraryEntry, ScenarioLibraryOverride
from idraa.models.user import User


@pytest.mark.asyncio
async def test_override_created_with_minimal_fields(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_library_entry: ScenarioLibraryEntry,
) -> None:
    """An override with only TEF override (other fields NULL) round-trips."""
    override = ScenarioLibraryOverride(
        organization_id=seed_organization.id,
        library_entry_id=seed_library_entry.id,
        library_entry_version=seed_library_entry.version,
        threat_event_frequency={"distribution": "PERT", "low": 2.0, "mode": 6.0, "high": 18.0},
        vulnerability=None,
        primary_loss=None,
        secondary_loss=None,
        reason="Healthcare org sees 1.5x baseline TEF for ransomware per internal IR data.",
        methodology_change_reason=None,
        version=1,
        created_by=seed_user.id,
    )
    db_session.add(override)
    await db_session.commit()
    await db_session.refresh(override)
    assert override.id is not None
    assert override.threat_event_frequency is not None
    assert override.threat_event_frequency["mode"] == 6.0
    assert override.vulnerability is None


@pytest.mark.asyncio
async def test_unique_org_entry_blocks_duplicate(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_library_entry: ScenarioLibraryEntry,
) -> None:
    """(organization_id, library_entry_id) UNIQUE — can't have two active overrides per (org, entry)."""
    a = ScenarioLibraryOverride(
        organization_id=seed_organization.id,
        library_entry_id=seed_library_entry.id,
        library_entry_version=seed_library_entry.version,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 4.0},
        reason="A",
        version=1,
        created_by=seed_user.id,
    )
    db_session.add(a)
    await db_session.commit()

    b = ScenarioLibraryOverride(
        organization_id=seed_organization.id,
        library_entry_id=seed_library_entry.id,
        library_entry_version=seed_library_entry.version,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 3.0, "high": 5.0},
        reason="B",
        version=1,
        created_by=seed_user.id,
    )
    db_session.add(b)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_composite_fk_to_entry_version(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_library_entry: ScenarioLibraryEntry,
) -> None:
    """Override's (library_entry_id, library_entry_version) FK must resolve to a real
    (id, version) on scenario_library_entries."""
    bad = ScenarioLibraryOverride(
        organization_id=seed_organization.id,
        library_entry_id=seed_library_entry.id,
        library_entry_version=seed_library_entry.version + 99,  # nonexistent version
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 4.0},
        reason="r",
        version=1,
        created_by=seed_user.id,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_override_version_bumps_in_place(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_library_entry: ScenarioLibraryEntry,
) -> None:
    """Override IS the row — version field bumps in-place on edit (not a new row).
    F9 wires the version-bump path; this test only verifies the schema admits it."""
    override = ScenarioLibraryOverride(
        organization_id=seed_organization.id,
        library_entry_id=seed_library_entry.id,
        library_entry_version=seed_library_entry.version,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 4.0},
        reason="initial",
        version=1,
        created_by=seed_user.id,
    )
    db_session.add(override)
    await db_session.commit()

    override.threat_event_frequency = {
        "distribution": "PERT",
        "low": 1.0,
        "mode": 5.0,
        "high": 10.0,
    }
    override.version = 2
    override.row_version = 2
    await db_session.commit()
    await db_session.refresh(override)
    assert override.version == 2
    assert override.threat_event_frequency["mode"] == 5.0
