"""Wizard step-5 (control step) recommendations tests (P2c Task 6, §6.2, §9).

Step 5 surfaces a "Recommended for this scenario" section above the org-controls
list, sourced from the started-from library entry's ``suggested_control_ids``:
- an ADOPTED recommendation → its org Control is pre-checked (render-only) below;
- an UN-ADOPTED recommendation → an inline "Add to my controls" adopt form whose
  hidden ``from_wizard_tx`` returns to step 5 after adopting.

Sec-I1/Sec-N1: the adopt route validates ``from_wizard_tx`` to the wizard tx
grammar (UUID4) BEFORE adopting — a bad tx is a clean 400 with NOTHING adopted.

The wizard is analyst+ gated. We drive it from a library entry via the real
step-1 POST (matching the calibration-banner tests), so the tx flows through to
step 5. The org adopts MFA (via the real adopt route, so ``library_pin`` is set
authentically) but not EDR — exercising both the adopted and un-adopted paths.
"""

from __future__ import annotations

import uuid
from html.parser import HTMLParser

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.enums import (
    AssetClass,
    ControlType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.enums import FairCamSubFunction as F
from idraa.models.scenario_library import ScenarioLibraryEntry

MFA_SLUG = "multi-factor-authentication"
EDR_SLUG = "endpoint-detection-response"
MFA_NAME = "Multi-Factor Authentication"
EDR_NAME = "Endpoint Detection and Response"


async def _seed_catalog_entry(db: AsyncSession, *, slug: str, name: str) -> ControlLibraryEntry:
    e = ControlLibraryEntry(
        version=1,
        slug=slug,
        name=name,
        description="a" * 25,
        control_type=ControlType.TECHNICAL,
        reference_annual_cost=30000,
        nist_csf_subcategories=["PR.AC-7"],
        cis_safeguards=["6.3"],
        iso_27001_controls=["A.9.4.2"],
        compliance_mappings={},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status="published",
    )
    db.add(e)
    await db.flush()
    for fn in (F.LEC_PREV_RESISTANCE, F.LEC_DET_VISIBILITY, F.VMC_ID_CONTROL_MONITORING):
        db.add(
            ControlLibraryEntryAssignment(
                library_entry_id=e.id,
                library_entry_version=1,
                sub_function=fn,
                capability_default=0.7,
                coverage_default=0.8,
                reliability_default=0.8,
            )
        )
    await db.flush()
    await db.commit()
    return e


def _scenario_entry_kwargs() -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "p2c-step5-src",
        "name": "P2c Step5 Source",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "d",
        "canonical_fair_gap": "g",
        "source_citations": [],
        "threat_event_frequency": {
            "distribution": "PERT",
            "low": 1.0,
            "mode": 4.0,
            "high": 12.0,
        },
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
        "suggested_control_ids": [MFA_SLUG, EDR_SLUG],
        "calibration_anchor": {"industry": "healthcare", "revenue_tier": "10b_to_100b"},
    }


async def _csrf_token(client: AsyncClient) -> str:
    r = await client.get("/controls/library")
    assert r.status_code == 200, f"bootstrap GET returned {r.status_code}"
    token = client.cookies.get("csrf_token")
    assert token, "csrf_token cookie missing post-bootstrap"
    return token


@pytest_asyncio.fixture
async def edr_catalog_entry(db_session: AsyncSession) -> ControlLibraryEntry:
    """A published EDR catalog entry the org has NOT yet adopted."""
    return await _seed_catalog_entry(db_session, slug=EDR_SLUG, name=EDR_NAME)


@pytest_asyncio.fixture
async def analyst_wizard_at_step5(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    edr_catalog_entry: ControlLibraryEntry,
) -> tuple[AsyncClient, str]:
    """Analyst client mid-wizard at step 5, started from a library entry whose
    ``suggested_control_ids`` = [MFA, EDR]; the org has ADOPTED MFA (via the real
    adopt route, so ``library_pin`` is set) but NOT EDR.
    """
    client, _org_id = authed_analyst

    mfa = await _seed_catalog_entry(db_session, slug=MFA_SLUG, name=MFA_NAME)

    # Adopt MFA via the real route so the Control carries an authentic library_pin.
    csrf = await _csrf_token(client)
    adopt = await client.post(
        f"/controls/library/{mfa.id}/adopt", data={"_csrf": csrf}, follow_redirects=False
    )
    assert adopt.status_code in (303, 204), adopt.status_code

    scenario_entry = ScenarioLibraryEntry(**_scenario_entry_kwargs())
    entry_id = scenario_entry.id
    db_session.add(scenario_entry)
    await db_session.commit()
    await db_session.close()

    # Start the wizard from the library entry → step-1 POST hands back the tx.
    csrf = await _csrf_token(client)
    pick = await client.post(
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(entry_id), "skip_library": "", "_csrf": csrf},
        follow_redirects=False,
    )
    assert pick.status_code == 303, pick.status_code
    tx = pick.headers["location"].split("tx=")[-1]
    return client, tx


@pytest.mark.asyncio
async def test_step5_shows_recommended_section_with_adopted_precheck(
    analyst_wizard_at_step5: tuple[AsyncClient, str],
) -> None:
    client, tx = analyst_wizard_at_step5
    r = await client.get(f"/scenarios/new/wizard/step/5?tx={tx}")
    assert r.status_code == 200
    assert b"Recommended for this scenario" in r.content
    # adopted MFA pre-selected; un-adopted EDR shows an adopt button.
    assert b"Adopted" in r.content
    assert b"Add to my controls" in r.content
    # the from_wizard_tx hidden field carries the tx back into the adopt form.
    assert f'name="from_wizard_tx" value="{tx}"'.encode() in r.content


class _FormNestingDetector(HTMLParser):
    """Track <form> depth; flag if a <form> opens while another is already open."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.nested = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "form":
            if self.depth > 0:
                self.nested = True
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.depth > 0:
            self.depth -= 1


@pytest.mark.asyncio
async def test_step5_recommendation_adopt_form_not_nested_in_wizard_form(
    analyst_wizard_at_step5: tuple[AsyncClient, str],
) -> None:
    """Regression: the recommended-control adopt <form> must NOT be nested inside
    the main step-5 wizard <form>.

    HTML forbids nested forms; the browser collapses them, so clicking "Add to my
    controls" would submit the OUTER wizard form (POST /scenarios/new/wizard/step/5,
    which advances the wizard to the review/final page) instead of the adopt route.
    The route-level tests pass because they POST the adopt endpoint directly,
    bypassing the browser's form collapse — only a structural check on the rendered
    HTML catches this.
    """
    client, tx = analyst_wizard_at_step5
    r = await client.get(f"/scenarios/new/wizard/step/5?tx={tx}")
    assert r.status_code == 200
    # Sanity: the un-adopted recommendation's adopt form is actually present.
    assert b"/adopt" in r.content and b"Add to my controls" in r.content

    detector = _FormNestingDetector()
    detector.feed(r.text)
    assert not detector.nested, (
        "step-5 has a <form> nested inside another <form> — clicking a "
        "recommended control's 'Add to my controls' submits the wizard form "
        "and jumps to the final page instead of adopting"
    )


@pytest.mark.asyncio
async def test_adopt_from_wizard_returns_to_step5(
    analyst_wizard_at_step5: tuple[AsyncClient, str],
    edr_catalog_entry: ControlLibraryEntry,
) -> None:
    client, tx = analyst_wizard_at_step5
    csrf = await _csrf_token(client)
    r = await client.post(
        f"/controls/library/{edr_catalog_entry.id}/adopt",
        data={"_csrf": csrf, "from_wizard_tx": tx},
        follow_redirects=False,
    )
    assert r.status_code in (303, 204)
    loc = r.headers.get("location") or r.headers.get("HX-Redirect")
    assert loc == f"/scenarios/new/wizard/step/5?tx={tx}"  # back to wizard, not /controls/{id}
    # §6.2: after adopting, the new EDR control is in the pick list + checkable.
    follow = await client.get(loc)
    assert follow.status_code == 200
    assert b'name="control_ids"' in follow.content
    assert EDR_NAME.encode() in follow.content


@pytest.mark.asyncio
async def test_adopt_from_wizard_rejects_non_uuid_tx_without_adopting(
    analyst_client: AsyncClient,
    edr_catalog_entry: ControlLibraryEntry,
    db_session: AsyncSession,
) -> None:
    # Sec-I1/Sec-N1: a non-UUID from_wizard_tx → 400 BEFORE any adopt; no Control created.
    before = (await db_session.execute(select(func.count()).select_from(Control))).scalar_one()
    csrf = await _csrf_token(analyst_client)
    r = await analyst_client.post(
        f"/controls/library/{edr_catalog_entry.id}/adopt",
        data={"_csrf": csrf, "from_wizard_tx": "not-a-uuid"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    after = (await db_session.execute(select(func.count()).select_from(Control))).scalar_one()
    assert after == before  # nothing adopted on a bad tx
