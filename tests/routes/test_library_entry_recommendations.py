"""Route tests for the library-entry-detail recommended-controls panel (P2c, Task 4).

The detail page resolves the entry's ``suggested_control_ids`` (catalog slugs) into
displayable recommendations and renders a panel after the FAIR-distributions card.
Viewer+ sees the panel; only analyst/admin (``can_adopt``, the Sec-N2 convention)
sees the inline "Add to my controls" adopt form.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control_library import ControlLibraryEntry
from idraa.models.enums import (
    AssetClass,
    ControlType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario_library import ScenarioLibraryEntry


@pytest_asyncio.fixture
async def seed_library_entry_with_recs(db_session: AsyncSession) -> ScenarioLibraryEntry:
    """A published ScenarioLibraryEntry suggesting "multi-factor-authentication",
    plus a published ControlLibraryEntry with that slug. Library + catalog rows are
    canonical (NOT org-scoped), so the client's own org sees them regardless; no
    Control adoption is seeded, so the recommendation renders un-adopted."""
    catalog = ControlLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug="multi-factor-authentication",
        name="Multi-factor Authentication",
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
    entry = ScenarioLibraryEntry(
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
        suggested_control_ids=["multi-factor-authentication"],
    )
    db_session.add(catalog)
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    return entry


@pytest.mark.asyncio
async def test_detail_shows_recommended_controls_panel(viewer_client, seed_library_entry_with_recs):
    entry = seed_library_entry_with_recs
    r = await viewer_client.get(f"/library/entries/{entry.id}")
    assert r.status_code == 200
    assert b"Recommended controls" in r.content
    assert (
        b"Multi-factor Authentication" in r.content or b"multi-factor-authentication" in r.content
    )


@pytest.mark.asyncio
async def test_viewer_sees_panel_but_no_adopt_button(viewer_client, seed_library_entry_with_recs):
    entry = seed_library_entry_with_recs
    r = await viewer_client.get(f"/library/entries/{entry.id}")
    assert b"Recommended controls" in r.content
    assert b"Add to my controls" not in r.content  # viewer can't adopt


@pytest.mark.asyncio
async def test_analyst_sees_adopt_button(analyst_client, seed_library_entry_with_recs):
    entry = seed_library_entry_with_recs
    r = await analyst_client.get(f"/library/entries/{entry.id}")
    assert b"Add to my controls" in r.content
    assert b"/controls/library/" in r.content  # the adopt form action
