"""ScenarioLibraryRepo — canonical-layer queries with version-aware lookups.

Spec §7.1.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    IndustrySubSector,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario_library import (
    ScenarioLibraryEntry,
    ScenarioLibraryOverride,
)
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo


def _entry(
    version: int = 1,
    slug: str = "rwa-ehr",
    status: str = "published",
    threat_event_type: ThreatCategory = ThreatCategory.RANSOMWARE,
    industries: list[str] | None = None,
    sub_sectors: list[str] | None = None,
) -> ScenarioLibraryEntry:
    return ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=version,
        slug=slug,
        name=slug,
        status=status,
        threat_event_type=threat_event_type,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        applicable_industries=industries,
        applicable_sub_sectors=sub_sectors,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        suggested_control_ids=[],
    )


@pytest.mark.asyncio
async def test_list_published_returns_only_published_latest_version(
    db_session: AsyncSession,
) -> None:
    """list_published returns ONLY the latest published version per logical id."""
    repo = ScenarioLibraryRepo(db_session)
    e1 = _entry(slug="a", status="published")
    e1_v2 = _entry(version=2, slug="a", status="published")
    e1_v2.id = e1.id
    e2 = _entry(slug="b", status="draft")
    e3 = _entry(slug="c", status="deprecated")
    db_session.add_all([e1, e1_v2, e2, e3])
    await db_session.commit()

    rows = await repo.list_published()
    slugs = sorted(r.slug for r in rows)
    versions = {r.id: r.version for r in rows}
    assert slugs == ["a"]
    assert versions[e1.id] == 2


@pytest.mark.asyncio
async def test_list_published_filters_by_threat_actor(
    db_session: AsyncSession,
) -> None:
    repo = ScenarioLibraryRepo(db_session)
    a = _entry(slug="a")
    a.threat_actor_type = ThreatActorType.NATION_STATE
    b = _entry(slug="b")
    b.threat_actor_type = ThreatActorType.CYBERCRIMINALS
    db_session.add_all([a, b])
    await db_session.commit()

    rows = await repo.list_published(threat_actor_types=[ThreatActorType.NATION_STATE])
    assert {r.slug for r in rows} == {"a"}


@pytest.mark.asyncio
async def test_list_published_filters_by_sub_sector_overlap(
    db_session: AsyncSession,
) -> None:
    """applicable_sub_sectors JSON array; filter matches if any sub-sector overlaps."""
    repo = ScenarioLibraryRepo(db_session)
    a = _entry(slug="a", sub_sectors=["oil_and_gas", "pipeline"])
    b = _entry(slug="b", sub_sectors=["chemical_manufacturing"])
    c = _entry(slug="c", sub_sectors=None)  # NULL = applies to all
    db_session.add_all([a, b, c])
    await db_session.commit()

    rows = await repo.list_published(applicable_sub_sectors=[IndustrySubSector.OIL_AND_GAS])
    slugs = sorted(r.slug for r in rows)
    assert "a" in slugs
    assert "b" not in slugs
    assert "c" in slugs


@pytest.mark.asyncio
async def test_get_by_id_version_returns_exact_version(
    db_session: AsyncSession,
) -> None:
    repo = ScenarioLibraryRepo(db_session)
    v1 = _entry(slug="x", status="deprecated")
    v2 = _entry(version=2, slug="x", status="published")
    v2.id = v1.id
    db_session.add_all([v1, v2])
    await db_session.commit()

    row = await repo.get_by_id_version(v1.id, 1)
    assert row is not None
    assert row.version == 1
    assert row.status == "deprecated"


@pytest.mark.asyncio
async def test_get_override_returns_latest_org_override(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: object,
    seed_library_entry: object,
) -> None:
    repo = ScenarioLibraryRepo(db_session)
    override = ScenarioLibraryOverride(
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        library_entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        library_entry_version=seed_library_entry.version,  # type: ignore[attr-defined]
        threat_event_frequency={"distribution": "PERT", "low": 2.0, "mode": 6.0, "high": 18.0},
        reason="r",
        version=1,
        created_by=seed_user.id,  # type: ignore[attr-defined]
    )
    db_session.add(override)
    await db_session.commit()

    found = await repo.get_override(
        seed_organization.id,  # type: ignore[attr-defined]
        seed_library_entry.id,  # type: ignore[attr-defined]
    )
    assert found is not None
    assert found.threat_event_frequency is not None
    assert found.threat_event_frequency["mode"] == 6.0


@pytest.mark.asyncio
async def test_list_versions_returns_all_versions_of_entry(
    db_session: AsyncSession,
) -> None:
    repo = ScenarioLibraryRepo(db_session)
    eid = uuid.uuid4()
    rows_in = [_entry(version=i, slug="x") for i in range(1, 4)]
    for r in rows_in:
        r.id = eid
    db_session.add_all(rows_in)
    await db_session.commit()

    versions = await repo.list_versions(eid)
    assert sorted(v.version for v in versions) == [1, 2, 3]


@pytest.mark.asyncio
async def test_get_by_slug_returns_latest_published_when_version_none(
    db_session: AsyncSession,
) -> None:
    """version=None: latest *published* row for the slug; drafts/deprecated excluded."""
    repo = ScenarioLibraryRepo(db_session)
    eid = uuid.uuid4()
    v1 = _entry(version=1, slug="dual-status", status="published")
    v1.id = eid
    v2 = _entry(version=2, slug="dual-status", status="published")
    v2.id = eid
    v3 = _entry(version=3, slug="dual-status", status="draft")
    v3.id = eid
    db_session.add_all([v1, v2, v3])
    await db_session.commit()

    row = await repo.get_by_slug("dual-status")
    assert row is not None
    assert row.version == 2  # latest published; v3 (draft) excluded


@pytest.mark.asyncio
async def test_get_by_slug_returns_exact_version_regardless_of_status(
    db_session: AsyncSession,
) -> None:
    """version=N: returns the exact (slug, version) row even if deprecated."""
    repo = ScenarioLibraryRepo(db_session)
    e = _entry(version=1, slug="audit", status="deprecated")
    db_session.add(e)
    await db_session.commit()

    row = await repo.get_by_slug("audit", version=1)
    assert row is not None
    assert row.status == "deprecated"


@pytest.mark.asyncio
async def test_get_override_by_version_returns_pin_resolution(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: object,
    seed_library_entry: object,
) -> None:
    """Pin-resolution lookup: returns the row at the exact (override_id, version)."""
    repo = ScenarioLibraryRepo(db_session)
    o = ScenarioLibraryOverride(
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        library_entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        library_entry_version=seed_library_entry.version,  # type: ignore[attr-defined]
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        reason="r",
        version=1,
        created_by=seed_user.id,  # type: ignore[attr-defined]
    )
    db_session.add(o)
    await db_session.commit()
    await db_session.refresh(o)

    row = await repo.get_override_by_version(o.id, 1)
    assert row is not None
    assert row.version == 1


@pytest.mark.asyncio
async def test_search_text_escapes_percent_and_underscore(
    db_session: AsyncSession,
) -> None:
    """Fix 2: user-supplied % and _ in search_text don't expand as wildcards."""
    repo = ScenarioLibraryRepo(db_session)
    a = _entry(slug="literal_underscore_match")
    b = _entry(slug="should-not-match")
    db_session.add_all([a, b])
    await db_session.commit()

    # Search for "_underscore_" — the underscores are LITERAL chars, not LIKE wildcards.
    rows = await repo.list_published(search_text="_underscore_")
    slugs = {r.slug for r in rows}
    assert "literal_underscore_match" in slugs
    assert "should-not-match" not in slugs

    # Search for "%-not-%" — the percents are LITERAL chars; matches the literal substring "-not-"
    # only if % is escaped (otherwise % would expand to anything and match both rows).
    # With escaping: pattern is "%\%-not-\%%" which only matches rows containing the literal "%-not-%".
    rows = await repo.list_published(search_text="%-not-%")
    slugs = {r.slug for r in rows}
    assert slugs == set()  # no row contains the literal substring "%-not-%"
