"""Route tests for the control library catalog browse UI + export (P2b Task 7).

Mirrors the scenario-library browse-route expectations: viewer+ may browse,
the cards partial honours filter query params, and export.csv streams a CSV.
The Arch-B1 regression test asserts /controls/library is NOT shadowed by the
UUID-typed /controls/{control_id} detail route (registration order).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def seed_control_library_entry(db_session: AsyncSession) -> Any:
    """ORM-insert one published ControlLibraryEntry + a FAIR-CAM assignment.

    Shares the per-test SQLite file with the ``client`` engine, so the seeded
    row is visible to requests issued through the authed-client fixtures.
    """
    from idraa.models.control_library import (
        ControlLibraryEntry,
        ControlLibraryEntryAssignment,
    )
    from idraa.models.enums import ControlType, FairCamSubFunction

    entry_id = _uuid.uuid4()
    entry = ControlLibraryEntry(
        id=entry_id,
        version=1,
        slug="mfa-fixture",
        name="Multi-Factor Authentication",
        description="Require a second authentication factor for privileged access.",
        control_type=ControlType.TECHNICAL,
        reference_annual_cost=12000,
        nist_csf_subcategories=["PR.AA-01"],
        cis_safeguards=["6.3"],
        iso_27001_controls=["A.5.17"],
        compliance_mappings={},
        applicable_industries=["financial_services"],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status="published",
    )
    db_session.add(entry)
    db_session.add(
        ControlLibraryEntryAssignment(
            library_entry_id=entry_id,
            library_entry_version=1,
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_default=0.7,
            coverage_default=0.8,
            reliability_default=0.9,
        )
    )
    await db_session.commit()
    await db_session.refresh(entry)
    return entry


@pytest.mark.asyncio
async def test_browse_requires_auth_viewer_ok(
    viewer_client: AsyncClient, seed_control_library_entry: Any
) -> None:
    r = await viewer_client.get("/controls/library")
    assert r.status_code == 200
    assert b"Control Library" in r.content or b"control-library" in r.content


@pytest.mark.asyncio
async def test_browse_export_csv(
    viewer_client: AsyncClient, seed_control_library_entry: Any
) -> None:
    r = await viewer_client.get("/controls/library/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert b"Multi-Factor Authentication" in r.content


@pytest.mark.asyncio
async def test_cards_partial_filters(
    viewer_client: AsyncClient, seed_control_library_entry: Any
) -> None:
    r = await viewer_client.get("/controls/library/_partials/cards?control_type=technical")
    assert r.status_code == 200
    assert b"Multi-Factor Authentication" in r.content


@pytest.mark.asyncio
async def test_cards_partial_excludes_nonmatching_type(
    viewer_client: AsyncClient, seed_control_library_entry: Any
) -> None:
    r = await viewer_client.get("/controls/library/_partials/cards?control_type=physical")
    assert r.status_code == 200
    assert b"Multi-Factor Authentication" not in r.content


@pytest.mark.asyncio
async def test_cards_partial_ignores_empty_free_text_facets(
    viewer_client: AsyncClient, seed_control_library_entry: Any
) -> None:
    # Regression: the sidebar's hx-include always sends the empty free-text
    # facet inputs (nist_csf=&cis=&industry=) alongside the real filter. An
    # empty param must be treated as "no filter", not as a literal "" tag that
    # matches nothing — otherwise EVERY checkbox/search filter returns 0 cards
    # (the JSON-facet post-filter rejects all rows on set([""]) & tags == {}).
    r = await viewer_client.get(
        "/controls/library/_partials/cards?control_type=technical&nist_csf=&cis=&industry=&q="
    )
    assert r.status_code == 200
    assert b"Multi-Factor Authentication" in r.content


@pytest.mark.asyncio
async def test_controls_library_not_shadowed_by_control_detail(
    viewer_client: AsyncClient, seed_control_library_entry: Any
) -> None:
    # Arch-B1: /controls/library must NOT be swallowed by /controls/{control_id:uuid}.
    r = await viewer_client.get("/controls/library")
    assert r.status_code == 200  # 422 here = router registered in the wrong order
