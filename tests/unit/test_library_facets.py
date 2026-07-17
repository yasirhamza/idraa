"""Unit tests for available_facets() — data-driven browse facet computation.

Step 1 (TDD): write failing tests before implementing the function.
The function lives at idraa.services.scenario_library.available_facets.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    slug: str,
    name: str,
    threat_actor_type: ThreatActorType = ThreatActorType.CYBERCRIMINALS,
    threat_event_type: ThreatCategory = ThreatCategory.RANSOMWARE,
    asset_class: AssetClass = AssetClass.OT_SYSTEMS,
    applicable_sub_sectors: list[str] | None = None,
    applicable_industries: list[str] | None = None,
    status: str = "published",
) -> ScenarioLibraryEntry:
    return ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        name=name,
        status=status,
        threat_event_type=threat_event_type,
        threat_actor_type=threat_actor_type,
        asset_class=asset_class,
        tags=[],
        description=f"Fixture entry: {name}",
        canonical_fair_gap="Test gap.",
        source_citations=[],
        applicable_sub_sectors=applicable_sub_sectors,
        applicable_industries=applicable_industries,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_available_facets_imports() -> None:
    """available_facets must be importable from services.scenario_library."""
    from idraa.services.scenario_library import available_facets  # noqa: F401


@pytest.mark.asyncio
async def test_available_facets_returns_only_present_asset_classes(
    db_session: AsyncSession,
) -> None:
    """Core bug-class guard: zero-coverage asset class MUST NOT appear in facet.

    business_process_third_party_revenue has 0 published entries →
    must be absent.  ot_systems has entries → must appear with correct count.
    """
    from idraa.services.scenario_library import FacetOption, available_facets

    # Seed two ot_systems entries, one data entry, one draft (must be excluded).
    entries = [
        _entry("ot-a", "OT Entry A", asset_class=AssetClass.OT_SYSTEMS),
        _entry("ot-b", "OT Entry B", asset_class=AssetClass.OT_SYSTEMS),
        _entry("data-a", "Data Entry A", asset_class=AssetClass.DATA),
        _entry(
            "draft-bptr",
            "Draft BPTR",
            asset_class=AssetClass.BUSINESS_PROCESS_THIRD_PARTY_REVENUE,
            status="draft",
        ),
    ]
    for e in entries:
        db_session.add(e)
    await db_session.commit()

    facets = await available_facets(db_session)

    ac_values = [opt.value for opt in facets["asset_class"]]

    # ot_systems has 2 published entries → present
    assert "ot_systems" in ac_values, "ot_systems must appear in asset_class facet"

    # data has 1 published entry → present
    assert "data" in ac_values, "data must appear in asset_class facet"

    # business_process_third_party_revenue: zero published entries → absent
    assert "business_process_third_party_revenue" not in ac_values, (
        "business_process_third_party_revenue (0 published entries) must NOT "
        "appear in asset_class facet — dead-end filter guard"
    )

    # Counts must be correct
    ot_opt = next(o for o in facets["asset_class"] if o.value == "ot_systems")
    assert ot_opt.count == 2, f"ot_systems count expected 2, got {ot_opt.count}"

    data_opt = next(o for o in facets["asset_class"] if o.value == "data")
    assert data_opt.count == 1, f"data count expected 1, got {data_opt.count}"

    # FacetOption is the right type
    assert isinstance(ot_opt, FacetOption)
    assert ot_opt.label  # non-empty label


@pytest.mark.asyncio
async def test_available_facets_threat_actor(db_session: AsyncSession) -> None:
    """Threat-actor facet contains only values present in published entries."""
    from idraa.services.scenario_library import available_facets

    entries = [
        _entry("cy-1", "Cyber 1", threat_actor_type=ThreatActorType.CYBERCRIMINALS),
        _entry("cy-2", "Cyber 2", threat_actor_type=ThreatActorType.CYBERCRIMINALS),
        _entry("ns-1", "NS 1", threat_actor_type=ThreatActorType.NATION_STATE),
    ]
    for e in entries:
        db_session.add(e)
    await db_session.commit()

    facets = await available_facets(db_session)

    ta_values = {opt.value for opt in facets["threat_actor_type"]}
    assert "cybercriminals" in ta_values
    assert "nation_state" in ta_values

    # competitors has 0 entries → absent
    assert "competitors" not in ta_values

    cy_opt = next(o for o in facets["threat_actor_type"] if o.value == "cybercriminals")
    assert cy_opt.count == 2


@pytest.mark.asyncio
async def test_available_facets_threat_category(db_session: AsyncSession) -> None:
    """Threat-category facet only shows values from published entries."""
    from idraa.services.scenario_library import available_facets

    entries = [
        _entry("rs-1", "Ransomware 1", threat_event_type=ThreatCategory.RANSOMWARE),
        _entry("mw-1", "Malware 1", threat_event_type=ThreatCategory.MALWARE),
        _entry("mw-2", "Malware 2", threat_event_type=ThreatCategory.MALWARE),
    ]
    for e in entries:
        db_session.add(e)
    await db_session.commit()

    facets = await available_facets(db_session)

    tc_values = {opt.value for opt in facets["threat_category"]}
    assert "ransomware" in tc_values
    assert "malware" in tc_values

    # supply_chain has 0 entries → absent
    assert "supply_chain" not in tc_values

    mw_opt = next(o for o in facets["threat_category"] if o.value == "malware")
    assert mw_opt.count == 2


@pytest.mark.asyncio
async def test_available_facets_sub_sector(db_session: AsyncSession) -> None:
    """Sub-sector facet: counts entries that EXPLICITLY name each value.

    NULL/empty applicable_sub_sectors means 'applies to all' and does NOT
    add the entry to any specific sub-sector facet.
    """
    from idraa.services.scenario_library import available_facets

    entries = [
        _entry("og-1", "Oil Gas 1", applicable_sub_sectors=["oil_and_gas"]),
        _entry("og-2", "Oil Gas 2", applicable_sub_sectors=["oil_and_gas", "pipeline"]),
        _entry("all-1", "All sectors", applicable_sub_sectors=None),  # applies to all
    ]
    for e in entries:
        db_session.add(e)
    await db_session.commit()

    facets = await available_facets(db_session)

    ss_values = {opt.value for opt in facets["sub_sector"]}
    assert "oil_and_gas" in ss_values
    assert "pipeline" in ss_values

    # water_utility has 0 explicit entries → absent
    assert "water_utility" not in ss_values

    og_opt = next(o for o in facets["sub_sector"] if o.value == "oil_and_gas")
    assert og_opt.count == 2

    pl_opt = next(o for o in facets["sub_sector"] if o.value == "pipeline")
    assert pl_opt.count == 1


@pytest.mark.asyncio
async def test_available_facets_industry(db_session: AsyncSession) -> None:
    """Industry facet: counts entries that EXPLICITLY name each industry.

    NULL/empty applicable_industries = 'applies to all' — does NOT add to facet.
    """
    from idraa.services.scenario_library import available_facets

    entries = [
        _entry("mfg-1", "Mfg 1", applicable_industries=["manufacturing"]),
        _entry("mfg-2", "Mfg 2", applicable_industries=["manufacturing", "utilities"]),
        _entry("all-ind", "All industries", applicable_industries=None),
    ]
    for e in entries:
        db_session.add(e)
    await db_session.commit()

    facets = await available_facets(db_session)

    ind_values = {opt.value for opt in facets["industry"]}
    assert "manufacturing" in ind_values
    assert "utilities" in ind_values

    # healthcare has 0 explicit entries → absent
    assert "healthcare" not in ind_values

    mfg_opt = next(o for o in facets["industry"] if o.value == "manufacturing")
    assert mfg_opt.count == 2


@pytest.mark.asyncio
async def test_available_facets_excludes_draft_entries(db_session: AsyncSession) -> None:
    """Draft entries must not contribute to facet counts."""
    from idraa.services.scenario_library import available_facets

    published = _entry("pub", "Published", asset_class=AssetClass.DATA)
    draft = _entry("drft", "Draft", asset_class=AssetClass.SAFETY_SYSTEMS, status="draft")
    db_session.add(published)
    db_session.add(draft)
    await db_session.commit()

    facets = await available_facets(db_session)

    ac_values = {opt.value for opt in facets["asset_class"]}
    assert "data" in ac_values
    # safety_systems only in draft → absent from facet
    assert "safety_systems" not in ac_values


@pytest.mark.asyncio
async def test_available_facets_empty_db(db_session: AsyncSession) -> None:
    """No published entries → all facet lists are empty, no KeyError."""
    from idraa.services.scenario_library import available_facets

    facets = await available_facets(db_session)

    assert facets["asset_class"] == []
    assert facets["threat_actor_type"] == []
    assert facets["threat_category"] == []
    assert facets["sub_sector"] == []
    assert facets["industry"] == []


@pytest.mark.asyncio
async def test_available_facets_stable_order(db_session: AsyncSession) -> None:
    """Facet results must be in a deterministic order (count desc, value asc tiebreak)."""
    from idraa.services.scenario_library import available_facets

    # cybercriminals × 3, nation_state × 1 → cybercriminals first by count desc
    entries = [
        _entry("cy-o1", "Cy O1", threat_actor_type=ThreatActorType.CYBERCRIMINALS),
        _entry("cy-o2", "Cy O2", threat_actor_type=ThreatActorType.CYBERCRIMINALS),
        _entry("cy-o3", "Cy O3", threat_actor_type=ThreatActorType.CYBERCRIMINALS),
        _entry("ns-o1", "NS O1", threat_actor_type=ThreatActorType.NATION_STATE),
    ]
    for e in entries:
        db_session.add(e)
    await db_session.commit()

    facets = await available_facets(db_session)
    ta = facets["threat_actor_type"]

    assert ta[0].value == "cybercriminals", (
        f"Expected cybercriminals first (highest count), got {ta[0].value}"
    )
    assert ta[1].value == "nation_state"
