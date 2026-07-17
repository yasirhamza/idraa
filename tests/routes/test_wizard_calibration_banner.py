"""Wizard FAIR-page banner + per-field badge rendering tests.

Verifies HTML output matches the spec §7 microcopy and the badge is
applied only to PL/SL inputs.

2026-05-28 step-3 split: the PL/SL calibration banner + per-field badges
moved from the (now-removed) combined step-3 page to the IMPACT page
(step 4). PL/SL is the only calibrated half, so the banner is gated to
step 4 via ``show_calibration_banner`` and must be ABSENT on step 3
(Likelihood: TEF+Vuln). These tests assert the banner/badges render on
``/step/4`` and are absent on ``/step/3`` — the banner-on-FAIR-page
behavior persists, only the surface moved.

NOTE: deviation from plan tests — the plan uses `analyst_client` +
`organization` fixtures, but `authed_analyst` creates its OWN org
(distinct from the `organization` fixture). We need to mutate the
analyst's actual org so the wizard route reads industry/revenue
correctly during the library-pick flow. We use `authed_analyst` +
`_resolve_org` (same pattern as F4 wizard-calibration tests).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

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
from tests.conftest import csrf_post


def _entry_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "f5-test",
        "name": "F5 Test",
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
async def test_banner_renders_envelope_note_for_library_entry(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")  # → 100m_to_1b tier
    await db_session.commit()
    await db_session.close()

    entry = ScenarioLibraryEntry(**_entry_kwargs(slug="f5-cal"))
    entry_id = entry.id
    db_session.add(entry)
    await db_session.commit()
    await db_session.close()

    pick_resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    tx = pick_resp.headers["location"].split("tx=")[-1]

    # The calibration banner is gated to the IMPACT page (step 4) — PL/SL is
    # the only calibrated half. Step 3 (Likelihood: TEF+Vuln) must NOT carry it.
    step3_resp = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    step3_body = step3_resp.text
    assert "alert alert-info" not in step3_body, "calibration banner must be absent on Likelihood"
    assert "calibrated for your org context" not in step3_body.lower()

    step4_resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    body = step4_resp.text

    # Envelope note (org loss-scaling removed 2026-07-07): the info banner states
    # the loss reflects the IRIS sector envelope and is NOT scaled to org size.
    assert "alert alert-info" in body, "info-level envelope note expected on the Impact page"
    assert "reference-class loss" in body.lower()
    assert "not scaled to your org size" in body.lower()
    # The old scaling microcopy must NOT appear.
    assert "calibrated for your org context" not in body.lower()
    assert "rescaled from anchor" not in body.lower()
    # PL/SL fieldsets present, TEF absent (Impact page). No per-field "calibrated" badge.
    pl_legend = body.find("Primary loss")
    sl_legend = body.find("Secondary loss")
    assert pl_legend > 0 and sl_legend > 0
    assert "Threat event frequency" not in body, "TEF must not appear on the Impact page"


@pytest.mark.asyncio
async def test_no_envelope_note_for_from_scratch_scenario(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """A from-scratch scenario (skip_library — library_entry_id is None) must show
    NO info note on the Impact page: its PL/SL are not a curated library loss, so
    the 'curated reference-class loss' note would be a false provenance claim. Guards
    the 2026-07-07 regression where the note was gated only on page==impact."""
    client, _org_id = authed_analyst

    pick_resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": "", "skip_library": "1"},
    )
    tx = pick_resp.headers["location"].split("tx=")[-1]
    step4_resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert step4_resp.status_code == 200
    body = step4_resp.text
    assert "Primary loss" in body, "Impact form must have rendered"

    assert "alert alert-info" not in body, "from-scratch scenario must show no info note"
    assert "reference-class loss" not in body.lower()
    assert "not scaled to your org size" not in body.lower()
    assert "calibrated for your org context" not in body.lower()
    assert "alert-warning" not in body  # no override banner either


@pytest.mark.asyncio
async def test_envelope_note_shows_for_library_entry_without_anchor(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The info note is gated on LIBRARY-DERIVED (library_entry_id), not on the
    anchor: even a legacy no-anchor library entry is a curated library loss, so
    the note still renders."""
    client, _org_id = authed_analyst

    entry = ScenarioLibraryEntry(**_entry_kwargs(slug="f5-legacy", calibration_anchor=None))
    entry_id = entry.id
    db_session.add(entry)
    await db_session.commit()
    await db_session.close()

    pick_resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    tx = pick_resp.headers["location"].split("tx=")[-1]
    step4_resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    body = step4_resp.text

    assert "alert alert-info" in body
    assert "reference-class loss" in body.lower()
    assert "not scaled to your org size" in body.lower()
    # Not the removed scaling microcopy.
    assert "calibrated for your org context" not in body.lower()
    assert "rescaled from anchor" not in body.lower()


@pytest.mark.asyncio
async def test_override_active_renders_warning_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Spec §7.3 — override active renders an alert-warning with specific microcopy."""
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")
    await db_session.commit()

    entry = ScenarioLibraryEntry(**_entry_kwargs(slug="f5-override"))
    entry_id = entry.id
    entry_version = entry.version
    db_session.add(entry)
    await db_session.flush()

    db_session.add(
        ScenarioLibraryOverride(
            organization_id=org_id,
            library_entry_id=entry_id,
            library_entry_version=entry_version,
            primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
            secondary_loss=None,
            threat_event_frequency=None,
            vulnerability=None,
            reason="test",
            version=1,
        )
    )
    await db_session.commit()
    await db_session.close()

    pick_resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    tx = pick_resp.headers["location"].split("tx=")[-1]

    # The override warning banner is gated to the Impact page (step 4). The
    # Likelihood page (step 3) must NOT carry it (TEF/Vuln are not calibrated).
    step3_resp = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    step3_body = step3_resp.text
    assert "alert alert-warning" not in step3_body, "override banner must be absent on Likelihood"
    assert "pl/sl pre-fill from your organization" not in step3_body.lower()

    step4_resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    body = step4_resp.text

    # Override-active path: alert-warning banner with explicit microcopy.
    assert "alert alert-warning" in body, "warning banner expected for override-active state"
    assert "pl/sl pre-fill from your organization" in body.lower()
    # NOT the calibrated banner or the envelope note (override takes precedence).
    assert "calibrated for your org context" not in body.lower()
    assert "reference-class loss" not in body.lower()
    # Per-field 'override' badges (badge-warning) appear on PL/SL inputs. Scope
    # to the per-fieldset <legend> region; ``badge-warning`` is the fieldset-
    # local anchor (the word "override" also appears in apply-overlay button
    # labels). The Impact page only renders PL/SL — TEF is on the Likelihood
    # page now, so the "TEF has no badge" check is the step-3 absence above.
    pl_legend = body.find("Primary loss")
    assert pl_legend > 0
    assert "Threat event frequency" not in body, "TEF must not appear on the Impact page"
    pl_fieldset_start = body.rfind("<fieldset", 0, pl_legend)
    pl_legend_end = body.find("</legend>", pl_legend)
    pl_window = body[pl_fieldset_start:pl_legend_end]
    assert "badge-warning" in pl_window, "PL fieldset missing override badge (badge-warning)"


@pytest.mark.asyncio
async def test_impact_get_propagates_calibration_metadata_to_context(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """GET the Impact page (step 4) with calibrated state renders the banner
    microcopy; the Likelihood page (step 3) does not.

    2026-05-28 step-3 split: the PL/SL calibration surface moved from the
    combined step-3 page to step 4 (Impact). This re-pins the propagation
    assertion to the new surface and confirms step 3 stays clean.
    """
    client, org_id = authed_analyst

    org = await _resolve_org(db_session, org_id)
    org.industry_type = IndustryType.HEALTHCARE
    org.annual_revenue = Decimal("500000000")
    await db_session.commit()
    await db_session.close()

    entry = ScenarioLibraryEntry(**_entry_kwargs(slug="f5-impact-prop"))
    entry_id = entry.id
    db_session.add(entry)
    await db_session.commit()
    await db_session.close()

    pick_resp = await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": ""},
    )
    tx = pick_resp.headers["location"].split("tx=")[-1]

    step3_resp = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert step3_resp.status_code == 200
    assert "calibrated for your org context" not in step3_resp.text.lower()

    step4_resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert step4_resp.status_code == 200
    body = step4_resp.text
    assert "reference-class loss" in body.lower()
    assert "not scaled to your org size" in body.lower()
