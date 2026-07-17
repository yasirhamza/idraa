"""Integration tests for the ADMIN-gated FX rate-admin routes (Task 5).

These tests exercise:
- Role gate (analyst 403 on both GET and POST, even with valid CSRF)
- Admin create → 303 + persisted row
- Out-of-range rate → 400
- Missing CSRF → 403 (global middleware)
- GET list renders _csrf field and form action
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.fx_rate import FxRate
from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_analyst_cannot_post_rate(authed_analyst) -> None:
    client, _ = authed_analyst
    # csrf_post supplies a valid _csrf, so a 403 here is the ROLE gate, not CSRF.
    resp = await csrf_post(
        client,
        "/fx-rates",
        {"code": "SAR", "usd_rate": "3.75", "as_of_date": "2026-06-14", "source": "SAMA"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_analyst_cannot_get_rate_list(authed_analyst) -> None:
    client, _ = authed_analyst
    resp = await client.get("/fx-rates")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_creates_rate(authed_admin, db_session: AsyncSession) -> None:
    client, _ = authed_admin
    resp = await csrf_post(
        client,
        "/fx-rates",
        {"code": "SAR", "usd_rate": "3.75", "as_of_date": "2026-06-14", "source": "SAMA"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.text
    row = (await db_session.execute(select(FxRate).where(FxRate.code == "SAR"))).scalar_one()
    assert str(row.usd_rate) == "3.75000000" and row.is_active is True


@pytest.mark.asyncio
async def test_admin_rejects_out_of_range_rate(authed_admin) -> None:
    client, _ = authed_admin
    resp = await csrf_post(
        client,
        "/fx-rates",
        {"code": "SAR", "usd_rate": "0.0000001", "as_of_date": "2026-06-14", "source": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_without_csrf_rejected(authed_admin) -> None:
    client, _ = authed_admin
    # Bypass csrf_post: raw POST with no _csrf → global middleware 403.
    resp = await client.post(
        "/fx-rates",
        data={"code": "SAR", "usd_rate": "3.75", "as_of_date": "2026-06-14", "source": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_get_list_renders_csrf_field(authed_admin) -> None:
    client, _ = authed_admin
    resp = await client.get("/fx-rates")
    assert resp.status_code == 200
    assert 'name="_csrf"' in resp.text
    assert 'action="/fx-rates"' in resp.text


@pytest.mark.asyncio
async def test_rate_input_accepts_decimals(authed_admin) -> None:
    """The usd_rate input must allow arbitrary decimals (SAR 3.75, EUR 0.92,
    KWD 0.307). A `step="1"` would make the browser reject 3.65 ("nearest valid
    values are 3 and 4"), blocking every real FX rate."""
    client, _ = authed_admin
    resp = await client.get("/fx-rates")
    assert resp.status_code == 200
    import re

    m = re.search(r'<input[^>]*\bname="usd_rate"[^>]*>', resp.text)
    assert m, "usd_rate input not found"
    tag = m.group(0)
    assert 'step="any"' in tag
    assert 'step="1"' not in tag
