"""Regression: file-download links must opt out of global ``hx-boost``.

``base.html`` sets ``<body hx-boost="true">``, so HTMX intercepts every
anchor click, fetches the target via AJAX, and swaps the response body into
the DOM. For a file-download endpoint (CSV export, executive PDF, control
matrix) the server replies ``Content-Disposition: attachment`` — but a
boosted click never triggers a browser download; the bytes get swapped into
the page and render as text.

Every download link must therefore carry ``hx-boost="false"`` so the click
performs a real navigation and the attachment downloads. These tests render
the real pages and assert the opt-out is present on each download anchor.
"""

from __future__ import annotations

import re
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from tests.integration._reports_fixtures import _make_completed_aggregate_run

# Anchors are emitted across multiple lines by the macros, so match with DOTALL.
_ANCHOR_RE = re.compile(r"<a\b.*?>", re.IGNORECASE | re.DOTALL)


def _anchors_to(html: str, href_substr: str) -> list[str]:
    """Return the opening ``<a ...>`` tags whose attributes reference href_substr."""
    return [tag for tag in _ANCHOR_RE.findall(html) if href_substr in tag]


async def _org_for(db_session: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db_session.get(Organization, org_id)
    assert org is not None
    return org


async def test_reports_page_download_links_opt_out_of_boost(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """A populated /reports page exercises all three download-link sites:
    page_header Export CSV, data_table export button, and the row action_menu
    'Download PDF'. None may be boosted."""
    client, org_id = authed_admin
    org = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, org, name="ready")
    await db_session.commit()

    r = await client.get("/reports")
    assert r.status_code == 200
    html = r.text

    csv_anchors = _anchors_to(html, "/reports/export.csv")
    assert csv_anchors, "expected at least one /reports/export.csv link"
    for tag in csv_anchors:
        assert 'hx-boost="false"' in tag, f"CSV export link is boosted: {tag!r}"

    # T8(e) #351: download link now points to /reports/run/{id} (unified route).
    pdf_anchors = _anchors_to(html, f"/reports/run/{run.id}")
    assert pdf_anchors, "expected a run-PDF download link for completed run"
    for tag in pdf_anchors:
        assert 'hx-boost="false"' in tag, f"PDF download link is boosted: {tag!r}"


@pytest.mark.parametrize(
    "path,href",
    [
        ("/scenarios", "/scenarios/export?format=csv"),
        ("/users", "/users/export.csv"),
        ("/controls", "/controls/export.csv"),
        ("/library", "/library/export.csv"),
        ("/overlays", "/overlays/export.csv"),
        ("/overlays", "/overlays/template.csv"),
    ],
)
async def test_list_page_export_links_opt_out_of_boost(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    path: str,
    href: str,
) -> None:
    """The page_header Export CSV / CSV-template actions render regardless of
    row count, so an empty DB is sufficient to assert the opt-out."""
    client, _ = authed_admin
    r = await client.get(path)
    if r.status_code == 404:
        pytest.skip(f"{path} not mounted")
    assert r.status_code == 200
    anchors = _anchors_to(r.text, href)
    assert anchors, f"expected a {href} download link on {path}"
    for tag in anchors:
        assert 'hx-boost="false"' in tag, f"{href} link is boosted: {tag!r}"
