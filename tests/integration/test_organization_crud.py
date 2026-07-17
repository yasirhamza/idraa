"""Organization profile editor — GET renders form, POST updates + audits.

POST uses the shared ``csrf_post`` helper (tests/conftest.py) because
CSRFMiddleware rejects un-tokened POSTs with a flat 403. ``authed_admin``
sets only the ``idraa_session`` cookie — no ``csrf_token`` cookie has been
minted yet — so ``csrf_post`` does a GET via its default bootstrap URL
(``/setup``) to make the middleware's response path issue the cookie.
``/setup`` 303s once users exist, and Starlette still runs the CSRF
response-path on a 303 (same pattern as test_login_flow.py's logout
test).

GET needs no CSRF — CSRFMiddleware only guards unsafe methods.
"""

from __future__ import annotations

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from tests.conftest import csrf_post


async def test_get_organization_renders_form(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _org_id = authed_admin
    r = await client.get("/organization")
    assert r.status_code == 200
    assert "Organization" in r.text
    assert 'name="name"' in r.text
    assert "Test Org" in r.text  # factory default


async def test_get_organization_renders_revenue_tier_hint(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Annual-revenue input has the live IRIS tier hint (issue #69)."""
    client, _org_id = authed_admin
    r = await client.get("/organization")
    assert r.status_code == 200
    body = r.text
    assert 'x-model="value"' in body
    assert 'x-text="label(value)"' in body
    assert "Enter total annual revenue in USD" in body
    assert "5000000000 for $5 billion" in body


async def test_get_organization_size_maturity_appetite_options_are_humanized(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Polish-4 (post-#454 SWE review): the organization/size, security-
    maturity, and risk-appetite <select> options must display humanized
    label text ("Small", "Basic", "Conservative", ...) rather than the raw
    lowercase enum value -- matching the industry select's existing #454
    item-3 treatment. Option `value=` attributes stay the raw stored enum
    value (only the visible LABEL is humanized) so form submission and
    round-trip persistence are unaffected."""
    client, _org_id = authed_admin
    r = await client.get("/organization")
    assert r.status_code == 200
    body = r.text

    # Humanized display labels present for each enum family.
    assert ">Small</option>" in body, "organization_size option label not humanized"
    assert ">Basic</option>" in body, "security_maturity option label not humanized"
    assert ">Conservative</option>" in body, "risk_appetite option label not humanized"

    # Raw lowercase values remain the option `value=` attribute (stored value
    # untouched) -- and no longer leak into the LABEL text.
    assert 'value="small"' in body
    assert 'value="basic"' in body
    assert 'value="conservative"' in body
    assert ">small</option>" not in body
    assert ">basic</option>" not in body
    assert ">conservative</option>" not in body


async def test_post_organization_updates_and_audits(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Acme Updated",
            "industry_type": "healthcare",
            "organization_size": "large",
            "annual_revenue": "250000000",
            "has_cyber_insurance": "on",
            "cyber_insurance_limit": "5000000",
            "cyber_insurance_deductible": "250000",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "USD",
            "preferred_language": "en",
            "compliance_requirements": "NIST_CSF, ISO_27001",
            "regulatory_environment": "GDPR",
            "geographic_regions": "NA, EMEA",
            "technology_stack": "AWS, Microsoft_365",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # POST redirects to GET with ?saved=1 so GET can render the success flash
    # (project has no session-stored flash; query-string is the lightest
    # pattern that still confirms the save).
    assert r.headers["location"] == "/organization?saved=1"

    # DB reflects changes
    org = (await db_session.execute(select(Organization))).scalar_one()
    assert org.name == "Acme Updated"
    assert org.industry_type.value == "healthcare"
    assert org.annual_revenue == Decimal("250000000")
    assert org.has_cyber_insurance is True
    assert org.compliance_requirements == ["NIST_CSF", "ISO_27001"]

    # AuditLog captured the update
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "update")))
        .scalars()
        .all()
    )
    assert any(r.entity_type == "organization" for r in rows)


async def test_post_organization_persists_annual_security_budget(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Task 1 (dashboard redesign): ``annual_security_budget`` is already
    persisted generically by the route + schema (Numeric(18,2) column,
    OrganizationForm field) -- only the form input was missing. Round-trip:
    POST a value, confirm it lands in the DB, then confirm the edit form
    re-renders the saved value on the next GET."""
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Acme Updated",
            "industry_type": "healthcare",
            "organization_size": "large",
            "annual_security_budget": "3500000",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "USD",
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    org = (await db_session.execute(select(Organization))).scalar_one()
    assert org.annual_security_budget == Decimal("3500000")

    page = await client.get("/organization")
    assert page.status_code == 200
    assert 'name="annual_security_budget"' in page.text
    assert "3500000" in page.text


# ---- Hotfix tests: empty optional number fields + validation-error UX ----


async def test_post_organization_blank_optional_numbers_succeed(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Hotfix A: HTML number inputs send `""` when blanked; route must
    coerce to None for the 5 nullable numeric fields rather than 400."""
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Acme",
            "industry_type": "healthcare",
            "organization_size": "large",
            # All five optional number fields blanked:
            "employee_count": "",
            "annual_revenue": "",
            "annual_security_budget": "",
            "cyber_insurance_limit": "",
            "cyber_insurance_deductible": "",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "USD",
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"expected 303, got {r.status_code} body={r.text[:500]!r}"
    org = (await db_session.execute(select(Organization))).scalar_one()
    assert org.employee_count is None
    assert org.annual_revenue is None
    assert org.annual_security_budget is None
    assert org.cyber_insurance_limit is None
    assert org.cyber_insurance_deductible is None


async def test_post_organization_blank_whitespace_optional_numbers_succeed(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Whitespace-only is also coerced to None (defensive)."""
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Acme",
            "industry_type": "healthcare",
            "organization_size": "large",
            "annual_revenue": "   ",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "USD",
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_post_organization_validation_error_preserves_form_values(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Hotfix B: on 400, the re-rendered form must show the user's typed-in
    values (so changes aren't visually erased) AND a per-field error badge."""
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            # Real validation error — negative annual_revenue.
            "name": "User Typed Name",
            "industry_type": "healthcare",
            "organization_size": "large",
            "annual_revenue": "-1000",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "USD",
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    # User's typed-in name is preserved (NOT reverted to org's prior value)
    assert 'value="User Typed Name"' in r.text
    # User's typed-in invalid annual_revenue is preserved (so they can fix it)
    assert 'value="-1000"' in r.text
    # The form-errors alert is present with proper ARIA semantics
    # F22: form_error_summary uses role="alert" (id="form-errors" removed)
    assert 'role="alert"' in r.text
    # Per-field error styling: the bad field has border-status-critical (F22: replaces input-error)
    assert "status-critical" in r.text or "border-status-critical" in r.text


async def test_post_organization_validation_error_top_alert_summarizes_count(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Top alert summarizes the error count and uses the proper plural form."""
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            # Two validation errors: negative revenue + bad currency length
            "name": "Acme",
            "industry_type": "healthcare",
            "organization_size": "large",
            "annual_revenue": "-1",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "TOOLONG",  # > 3 chars
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    # F22: form_error_summary uses "Please fix the following:" heading
    # (replaces old "Couldn't save — N validation errors" text)
    assert "Please fix" in r.text or "status-critical" in r.text


async def test_get_organization_with_saved_query_renders_success_flash(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """GET /organization?saved=1 renders the success flash. The POST handler
    redirects with this query string after a successful save so the user
    gets a visible 'Saved' confirmation."""
    client, _org_id = authed_admin
    r = await client.get("/organization?saved=1")
    assert r.status_code == 200
    assert "alert-success" in r.text
    assert "Organization profile saved." in r.text
    # Issue #107: base.html → layouts/_flash.html is the only flash source.
    # Inline duplicate block was deleted; assert single render.
    assert r.text.count("alert alert-") == 1


async def test_get_organization_without_saved_query_renders_no_flash(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Cold GET /organization shows no flash (clean state)."""
    client, _org_id = authed_admin
    r = await client.get("/organization")
    assert r.status_code == 200
    assert "alert-success" not in r.text
    assert "Organization profile saved." not in r.text
    assert r.text.count("alert alert-") == 0


async def test_post_organization_full_save_round_trip_shows_flash(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """End-to-end: POST then follow the 303 to land on GET with the flash
    rendered. The browser does this automatically on a successful submit."""
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Round Trip Co",
            "industry_type": "healthcare",
            "organization_size": "large",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "USD",
            "preferred_language": "en",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Organization profile saved." in r.text
    assert "Round Trip Co" in r.text  # name was actually persisted
    assert r.text.count("alert alert-") == 1
