"""F4: end-to-end wizard library-pick → step-3 calibration tests.

Exercises the route layer: pick library entry → wizard state stashes
calibrated PL/SL + metadata → step-3 GET renders context with banner data.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    IndustryType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.scenario_library import (
    ScenarioLibraryEntry,
    ScenarioLibraryOverride,
)
from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post


def _seed_entry_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "f4-test-entry",
        "name": "F4 Test Entry",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "d",
        "canonical_fair_gap": "g",
        "source_citations": [],
        "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        "primary_loss": {
            "distribution": "PERT",
            "low": 100000.0,
            "mode": 1000000.0,
            "high": 10000000.0,
        },
        "secondary_loss": {
            "distribution": "PERT",
            "low": 50000.0,
            "mode": 500000.0,
            "high": 5000000.0,
        },
        "suggested_control_ids": [],
        "calibration_anchor": {"industry": "healthcare", "revenue_tier": "10b_to_100b"},
    }
    base.update(overrides)
    return base


async def _resolve_org(db_session: AsyncSession, org_id: object) -> Organization:
    return (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_picking_library_entry_with_anchor_uses_entry_absolute_no_scaling(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Pick a library entry with an anchor whose tier differs from the org tier;
    wizard state must carry the entry's ENTRY-ABSOLUTE PL/SL (no revenue-tier
    scaling) and None calibration metadata (org loss-scaling removed 2026-07-07)."""
    client, org_id = authed_analyst

    # Org tier deliberately differs from the entry anchor tier — under the old code
    # this scaled PL/SL down ~5x; now it must have NO effect.
    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")  # → 100m_to_1b tier
    await db_session.commit()
    await db_session.close()

    entry = ScenarioLibraryEntry(**_seed_entry_kwargs())
    entry_pl = dict(entry.primary_loss)
    entry_tef = dict(entry.threat_event_frequency)
    entry_vuln = dict(entry.vulnerability)
    entry_id = entry.id
    db_session.add(entry)
    await db_session.commit()
    await db_session.close()

    # POST step-1: pick the library entry
    resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    assert resp.status_code in (302, 303), (
        f"expected redirect, got {resp.status_code}: {resp.text[:300]}"
    )

    # Re-fetch the wizard draft directly
    draft = (
        await db_session.execute(select(WizardDraft).order_by(WizardDraft.updated_at.desc()))
    ).scalar_one_or_none()
    assert draft is not None
    state = draft.state_json
    assert state["library_entry_id"] == str(entry_id)

    # PL/SL must be ENTRY-ABSOLUTE — byte-identical to the entry, NOT tier-scaled.
    assert state["primary_loss"] == entry_pl
    # TEF/Vuln unchanged (archetype-curated, never calibration-scaled).
    assert state["threat_event_frequency"] == entry_tef
    assert state["vulnerability"] == entry_vuln
    # No calibration metadata / banner after scaling removal — the field was
    # removed from WizardState entirely (issue #516), so it is absent from
    # the persisted state_json rather than present-and-None.
    assert "calibration_metadata" not in state


@pytest.mark.asyncio
async def test_picking_library_entry_with_override_skips_calibration(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """When override exists for (org, entry), pre-fill uses override absolutes, no metadata."""
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")
    await db_session.commit()

    entry = ScenarioLibraryEntry(**_seed_entry_kwargs(slug="f4-override-entry"))
    entry_id = entry.id
    entry_version = entry.version
    db_session.add(entry)
    await db_session.flush()

    override = ScenarioLibraryOverride(
        organization_id=org_id,
        library_entry_id=entry_id,
        library_entry_version=entry_version,
        threat_event_frequency=None,
        vulnerability=None,
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        secondary_loss=None,
        reason="test override",
        version=1,
    )
    db_session.add(override)
    await db_session.commit()
    await db_session.close()

    resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    assert resp.status_code in (302, 303)

    draft = (
        await db_session.execute(select(WizardDraft).order_by(WizardDraft.updated_at.desc()))
    ).scalar_one_or_none()
    assert draft is not None
    state = draft.state_json

    # PL from override, not calibrated
    assert state["primary_loss"]["mode"] == 2.0
    # calibration_metadata was removed from WizardState entirely (issue #516).
    assert "calibration_metadata" not in state


# test_picking_library_entry_without_anchor_uses_entry_absolutes — DELETED in PR γ-4 (#115)
# Post NOT NULL flip, calibration_anchor=None can no longer be inserted at the
# DB layer. A malformed anchor is now simply IGNORED (the anchor is not read for
# scaling — org loss-scaling was removed 2026-07-07); entry-absolute pass-through
# is covered by tests/services/test_library_calibration.py
# ::test_pre_fill_ignores_malformed_anchor_returns_entry_absolute.


@pytest.mark.asyncio
async def test_picking_draft_or_unknown_library_entry_returns_404_generic(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Wrong UUID and draft/deprecated entries both return 404 with constant detail.

    Closes the existence-oracle: a 500 for unknown vs 404 for draft would
    itself leak which entries are in the library.
    """
    client, _org_id = authed_analyst

    # Case 1: unknown UUID -> LibraryEntryNotFoundError -> 404 constant detail
    unknown_id = uuid.uuid4()
    resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(unknown_id), "skip_library": ""},
    )
    assert resp.status_code == 404, f"unknown UUID should 404, got {resp.status_code}"
    # FastAPI's default 404 response shape includes 'detail'; check the constant string
    assert "Library entry not available" in resp.text

    # Case 2: draft entry -> LibraryEntryStatusError -> same 404 + same detail
    entry = ScenarioLibraryEntry(**_seed_entry_kwargs(slug="f4-draft", status="draft"))
    db_session.add(entry)
    await db_session.commit()
    entry_id = entry.id
    await db_session.close()

    resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    assert resp.status_code == 404, f"draft entry should 404, got {resp.status_code}"
    assert "Library entry not available" in resp.text
