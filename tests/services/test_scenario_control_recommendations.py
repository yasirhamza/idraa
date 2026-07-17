"""Resolver tests for scenario_control_recommendations (P2c, Task 3).

Resolves a scenario library entry's ``suggested_control_ids`` (catalog slugs)
into ``ControlRecommendation`` rows, marking which the caller's org already
adopted (via ``Control.library_pin``). Pure read; org-scoped adoption lookup.
"""

from __future__ import annotations

import pytest

from idraa.models.control import Control
from idraa.models.control_library import ControlLibraryEntry
from idraa.models.enums import (
    AssetClass,
    ControlSource,
    ControlType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.scenario_control_recommendations import recommended_controls_for
from tests.factories import create_org


async def _catalog(db, slug, *, status="published", version=1):
    e = ControlLibraryEntry(
        version=version,
        slug=slug,
        name=slug.upper(),
        description="a" * 25,
        control_type=ControlType.TECHNICAL,
        nist_csf_subcategories=[],
        cis_safeguards=[],
        iso_27001_controls=[],
        compliance_mappings={},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status=status,
    )
    db.add(e)
    await db.flush()
    return e


async def _entry(db, suggested):
    e = ScenarioLibraryEntry(
        version=1,
        slug="ransomware-on-ehr",
        name="R",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        description="d" * 25,
        source_citations=[],
        canonical_fair_gap="g" * 25,
        threat_event_frequency={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        suggested_control_ids=suggested,
        calibration_anchor={"industry": "other", "revenue_tier": "100m_to_1b"},
    )
    db.add(e)
    await db.flush()
    return e


@pytest.mark.asyncio
async def test_resolves_slugs_and_marks_adopted(db_session):
    org = await create_org(db_session)
    mfa = await _catalog(db_session, "multi-factor-authentication")
    await _catalog(db_session, "endpoint-detection-response")
    entry = await _entry(db_session, ["multi-factor-authentication", "endpoint-detection-response"])
    # org already adopted MFA:
    db_session.add(
        Control(
            organization_id=org.id,
            name="MFA",
            type=ControlType.TECHNICAL,
            annual_cost=0,
            source=ControlSource.LIBRARY_DERIVED,
            library_pin={"entry_id": str(mfa.id), "version": 1},
        )
    )
    await db_session.flush()
    recs = await recommended_controls_for(db_session, entry=entry, org_id=org.id)
    # order preserved (curated order):
    assert [r.catalog_entry.slug for r in recs] == [
        "multi-factor-authentication",
        "endpoint-detection-response",
    ]
    by_slug = {r.catalog_entry.slug: r for r in recs}
    assert by_slug["multi-factor-authentication"].adopted is True
    assert by_slug["multi-factor-authentication"].adopted_control_id is not None
    assert by_slug["endpoint-detection-response"].adopted is False
    assert by_slug["endpoint-detection-response"].adopted_control_id is None


@pytest.mark.asyncio
async def test_skips_unresolvable_and_unpublished(db_session):
    org = await create_org(db_session)
    await _catalog(db_session, "data-backup-recovery", status="draft")  # not published
    entry = await _entry(db_session, ["data-backup-recovery", "does-not-exist"])
    recs = await recommended_controls_for(db_session, entry=entry, org_id=org.id)
    assert recs == []  # draft skipped, unknown skipped


@pytest.mark.asyncio
async def test_adopted_is_org_scoped(db_session):
    org_a = await create_org(db_session, name="Org A")
    org_b = await create_org(db_session, name="Org B")
    mfa = await _catalog(db_session, "multi-factor-authentication")
    entry = await _entry(db_session, ["multi-factor-authentication"])
    # org B adopted it; org A did not:
    db_session.add(
        Control(
            organization_id=org_b.id,
            name="MFA",
            type=ControlType.TECHNICAL,
            annual_cost=0,
            source=ControlSource.LIBRARY_DERIVED,
            library_pin={"entry_id": str(mfa.id), "version": 1},
        )
    )
    await db_session.flush()
    recs = await recommended_controls_for(db_session, entry=entry, org_id=org_a.id)
    assert recs[0].adopted is False  # org A hasn't adopted it
