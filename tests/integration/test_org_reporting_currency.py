# tests/integration/test_org_reporting_currency.py
"""P3 Task 4: org reporting-currency picker — GET select, POST rated-gate, SECURITY anchor.

Tests:
  (a) GET /organization renders a <select name="preferred_currency"> with USD + active-rate codes.
  (b) POST preferred_currency=SAR (rate seeded) → 303 persisted.
  (c) POST preferred_currency=EUR (no rate seeded) → 400 re-render, NOT persisted.
  (d) SECURITY regression anchor: POST preferred_currency="<i>" → rejected (schema pattern
      ^[A-Z]{3}$ fires before the rated-gate), org keeps prior value.
      This test is the markup-channel regression anchor — if it passes without rejection,
      the markup path to reportlab is open, STOP.

All tests use ``authed_admin`` + ``csrf_post``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.services.fx_rates import FxRateService
from tests.conftest import csrf_post


async def test_get_org_renders_currency_select(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """(a) GET /organization renders a <select name="preferred_currency"> with USD."""
    client, _org_id = authed_admin
    r = await client.get("/organization")
    assert r.status_code == 200
    # A <select> element for preferred_currency must be present
    assert 'name="preferred_currency"' in r.text
    assert "<select" in r.text
    # USD is always in the list
    assert 'value="USD"' in r.text


async def test_get_org_renders_active_rate_codes_in_select(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """(a-ext) GET /organization includes non-USD codes that have an active rate."""
    client, org_id = authed_admin  # type: ignore[misc]
    # Seed an active SAR rate
    await FxRateService(db_session).upsert_rate(
        org_id,  # type: ignore[arg-type]
        "SAR",
        Decimal("3.75"),
        dt.date(2026, 6, 15),
        "SAMA",
        user_id=None,
    )
    await db_session.commit()

    r = await client.get("/organization")
    assert r.status_code == 200
    assert 'value="SAR"' in r.text
    # EUR has no rate → must NOT appear
    assert 'value="EUR"' not in r.text


async def test_post_org_rated_currency_persists(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """(b) POST preferred_currency=SAR (rate seeded) → 303 and persisted."""
    client, org_id = authed_admin  # type: ignore[misc]
    await FxRateService(db_session).upsert_rate(
        org_id,  # type: ignore[arg-type]
        "SAR",
        Decimal("3.75"),
        dt.date(2026, 6, 15),
        "SAMA",
        user_id=None,
    )
    await db_session.commit()

    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Test Org",
            "industry_type": "manufacturing",
            "organization_size": "medium",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "SAR",
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Expected 303, got {r.status_code}: {r.text[:300]}"

    org = (await db_session.execute(select(Organization))).scalar_one()
    assert org.preferred_currency == "SAR"


async def test_post_org_unrated_currency_rejected(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """(c) POST preferred_currency=EUR (no rate) → 400 re-render, NOT persisted."""
    client, _org_id = authed_admin

    # Ensure org starts at USD
    org = (await db_session.execute(select(Organization))).scalar_one()
    prior_currency = org.preferred_currency  # should be USD

    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Test Org",
            "industry_type": "manufacturing",
            "organization_size": "medium",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "EUR",  # no active rate seeded
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    # Error must mention the currency rejection
    assert "EUR" in r.text or "not available" in r.text or "preferred_currency" in r.text

    # DB row must NOT have changed
    await db_session.refresh(org)
    assert org.preferred_currency == prior_currency


async def test_post_org_markup_currency_rejected_security_anchor(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """(d) SECURITY regression anchor: POST preferred_currency="<i>" is rejected.

    The schema's ^[A-Z]{3}$ pattern rejects "<i>" before it reaches the
    rated-gate.  This pins that the markup channel to reportlab is closed.

    CRITICAL: if this test fails (i.e. the org is mutated), the markup
    channel is open — this is a security regression; STOP and report.
    """
    client, _org_id = authed_admin

    # Capture prior value so we can verify no mutation occurred.
    org = (await db_session.execute(select(Organization))).scalar_one()
    prior_currency = org.preferred_currency

    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Test Org",
            "industry_type": "manufacturing",
            "organization_size": "medium",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "<i>",  # markup injection attempt
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    # Must be rejected — 400 from schema validation (pattern ^[A-Z]{3}$ fails on "<i>").
    assert r.status_code == 400, (
        f"SECURITY: markup code '<i>' was NOT rejected (got {r.status_code}). "
        "The markup channel to reportlab is open — this is a regression. STOP."
    )

    # DB row must be unchanged — no mutation.
    await db_session.refresh(org)
    assert org.preferred_currency == prior_currency, (
        f"SECURITY: org.preferred_currency was mutated to {org.preferred_currency!r} "
        f"after posting '<i>'. Prior value was {prior_currency!r}. STOP."
    )
