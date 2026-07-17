"""ScenarioLibraryEntry — composite PK (id, version), immutable history.

Spec: docs/superpowers/specs/2026-04-28-phase-1.5a-scenario-library-design.md §6.1
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario_library import ScenarioLibraryEntry


def _new_entry(
    version: int = 1,
    slug: str = "ransomware-on-ehr",
    status: str = "published",
) -> ScenarioLibraryEntry:
    return ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=version,
        slug=slug,
        name="Ransomware on EHR",
        status=status,
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        attack_vector="email_phishing",
        tags=["healthcare", "ot-relevant"],
        description="Cybercriminal ransomware deployed against an EHR cluster.",
        canonical_fair_gap="FAIR's MALWARE category does not segment ransomware-by-target-class.",
        source_citations=["Cyentia IRIS 2025 §healthcare"],
        applicable_industries=["healthcare"],
        applicable_sub_sectors=None,
        applicable_org_sizes=["medium", "large"],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        secondary_loss=None,
        suggested_control_ids=[],
        standards_references=None,
    )


@pytest.mark.asyncio
async def test_composite_pk_allows_multiple_versions(db_session: AsyncSession) -> None:
    """Same id with different versions = two distinct rows. Composite PK admits both."""
    entry_id = uuid.uuid4()
    v1 = _new_entry(version=1)
    v1.id = entry_id
    v2 = _new_entry(version=2)
    v2.id = entry_id
    v2.slug = "ransomware-on-ehr"

    db_session.add_all([v1, v2])
    await db_session.commit()

    from sqlalchemy import select

    rows = (
        (
            await db_session.execute(
                select(ScenarioLibraryEntry).where(ScenarioLibraryEntry.id == entry_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {r.version for r in rows} == {1, 2}


@pytest.mark.asyncio
async def test_composite_pk_blocks_duplicate(db_session: AsyncSession) -> None:
    """Same (id, version) cannot be inserted twice."""
    entry_id = uuid.uuid4()
    a = _new_entry(version=1)
    a.id = entry_id
    b = _new_entry(version=1)
    b.id = entry_id
    b.slug = "ransomware-on-ehr-2"
    db_session.add(a)
    await db_session.commit()
    db_session.add(b)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_slug_version_unique(db_session: AsyncSession) -> None:
    """Same (slug, version) cannot be reused across logical entries."""
    a = _new_entry(version=1, slug="phishing-leads-to-creds")
    b = _new_entry(version=1, slug="phishing-leads-to-creds")  # different id
    db_session.add(a)
    await db_session.commit()
    db_session.add(b)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_status_constraint_rejects_invalid_value(db_session: AsyncSession) -> None:
    """status must be in {draft, published, deprecated}."""
    bad = _new_entry()
    bad.status = "approved"  # not allowed
    db_session.add(bad)
    # SQLAlchemy may surface the CHECK violation as IntegrityError (DB), or
    # the Enum coercion may raise LookupError / StatementError / ValueError
    # depending on dialect.
    with pytest.raises((IntegrityError, StatementError, LookupError, ValueError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_canonical_fair_gap_required_at_db_level(db_session: AsyncSession) -> None:
    """canonical_fair_gap is NOT NULL at DB level — model rejects None at insert."""
    entry = _new_entry()
    entry.canonical_fair_gap = None  # type: ignore[assignment]
    db_session.add(entry)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
