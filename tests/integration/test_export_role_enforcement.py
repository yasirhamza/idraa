"""#315: data exports need analyst or admin; the run PDF stays open to every role.

Behavioural companion to ``tests/unit/test_export_role_gate.py`` (which pins the
gate over the route table). The role fixtures share one client, so each test
uses exactly one role.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

_RID = "00000000-0000-4000-8000-000000000315"

# Every role-gated export. Id-bearing routes use an id that names nothing: the
# role gate answers before any lookup, so a read-only role sees 403, not 404.
_GATED = [
    "/scenarios/export?format=csv",
    "/scenarios/export?format=json",
    f"/scenarios/{_RID}/export?format=json",
    "/controls/export.csv",
    "/controls/library/export.csv",
    "/overlays/export.csv",
    "/library/export.csv",
    "/library/export",
    f"/library/entries/{_RID}/export",
    "/analyses/export.csv",
    "/reports/export.csv",
    f"/reports/run/{_RID}/verification.xlsx",
    f"/runs/{_RID}/control-matrix.csv",
    f"/runs/{_RID}/samples.csv.gz",
]

# List pages and the export links each offers to a user who may export.
_PAGES = [
    ("/scenarios", "/scenarios/export?format=csv"),
    ("/controls", "/controls/export.csv"),
    ("/controls/library", "/controls/library/export.csv"),
    ("/overlays", "/overlays/export.csv"),
    ("/library", "/library/export.csv"),
    ("/reports", "/reports/export.csv"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED)
async def test_viewer_cannot_export(viewer_client: AsyncClient, path: str) -> None:
    r = await viewer_client.get(path)
    assert r.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED)
async def test_reviewer_cannot_export(reviewer_client: AsyncClient, path: str) -> None:
    r = await reviewer_client.get(path)
    assert r.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED)
async def test_analyst_passes_the_role_gate(analyst_client: AsyncClient, path: str) -> None:
    """Non-vacuity control: the same paths are not 403 for the export role."""
    r = await analyst_client.get(path)
    assert r.status_code in (200, 404)


@pytest.mark.asyncio
async def test_admin_passes_the_role_gate(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/controls/export.csv")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_run_pdf_stays_open_to_viewer(viewer_client: AsyncClient) -> None:
    """The PDF report is exempt: a viewer passes the gate and reaches the lookup (404)."""
    r = await viewer_client.get(f"/reports/run/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(("page", "link"), _PAGES)
async def test_viewer_is_not_offered_export_buttons(
    viewer_client: AsyncClient, page: str, link: str
) -> None:
    r = await viewer_client.get(page)
    assert r.status_code == 200
    assert link.replace("&", "&amp;") not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("page", "link"), _PAGES)
async def test_analyst_is_offered_export_buttons(
    analyst_client: AsyncClient, page: str, link: str
) -> None:
    r = await analyst_client.get(page)
    assert r.status_code == 200
    assert link.replace("&", "&amp;") in r.text
