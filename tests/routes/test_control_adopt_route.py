"""Adopt route RBAC + redirect tests (P2b Task 8, §10).

POST /controls/library/{entry_id}/adopt is analyst+ ({ADMIN, ANALYST});
reviewer must 403. A successful adopt redirects to the new control
(303 for plain form, 204 + HX-Redirect for HTMX).

Also covers the re-adopt path (§6.5 / §10 "re-adopt warns but succeeds"):
after one adopt the browse card shows an "Already adopted" marker (org-scoped),
and a SECOND adopt still succeeds.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.enums import ControlType
from idraa.models.enums import FairCamSubFunction as F


async def _seed_published_entry(db: AsyncSession, *, slug: str = "mfa") -> ControlLibraryEntry:
    e = ControlLibraryEntry(
        version=1,
        slug=slug,
        name="Multi-Factor Authentication",
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


async def _csrf_token_for(client: AsyncClient) -> str:
    """Prime CSRF by GET'ing the library browse page + return the cookie value."""
    r = await client.get("/controls/library")
    assert r.status_code == 200, f"bootstrap GET returned {r.status_code}"
    token = client.cookies.get("csrf_token")
    assert token, "csrf_token cookie missing post-bootstrap"
    return token


@pytest.mark.asyncio
async def test_adopt_reviewer_forbidden(
    reviewer_client: AsyncClient, db_session: AsyncSession
) -> None:
    # §10: adopt is analyst+ ({ADMIN, ANALYST}); reviewer must 403.
    entry = await _seed_published_entry(db_session)
    csrf = await _csrf_token_for(reviewer_client)
    r = await reviewer_client.post(f"/controls/library/{entry.id}/adopt", data={"_csrf": csrf})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_adopt_analyst_succeeds_and_redirects(
    analyst_client: AsyncClient, db_session: AsyncSession
) -> None:
    entry = await _seed_published_entry(db_session)
    csrf = await _csrf_token_for(analyst_client)
    r = await analyst_client.post(
        f"/controls/library/{entry.id}/adopt",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code in (303, 204)  # 204 + HX-Redirect for HTMX, 303 otherwise
    assert r.headers["location"].startswith("/controls/") or r.headers.get("HX-Redirect")


@pytest.mark.asyncio
async def test_adopt_missing_entry_returns_404(
    analyst_client: AsyncClient, db_session: AsyncSession
) -> None:
    csrf = await _csrf_token_for(analyst_client)
    r = await analyst_client.post(
        f"/controls/library/{uuid.uuid4()}/adopt",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_re_adopt_shows_marker_and_still_succeeds(
    analyst_client: AsyncClient, db_session: AsyncSession
) -> None:
    """§6.5 / §10: after one adopt the browse card shows 'Already adopted'
    (org-scoped) for that entry, and a SECOND adopt still SUCCEEDS."""
    entry = await _seed_published_entry(db_session)
    csrf = await _csrf_token_for(analyst_client)

    r1 = await analyst_client.post(
        f"/controls/library/{entry.id}/adopt",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r1.status_code in (303, 204)

    # Browse now marks the entry as already adopted (org-scoped lookup).
    browse = await analyst_client.get("/controls/library")
    assert browse.status_code == 200
    assert b"Already adopted" in browse.content

    # A second adopt still succeeds (non-blocking warning).
    csrf2 = analyst_client.cookies.get("csrf_token") or csrf
    r2 = await analyst_client.post(
        f"/controls/library/{entry.id}/adopt",
        data={"_csrf": csrf2},
        follow_redirects=False,
    )
    assert r2.status_code in (303, 204)
