"""Integration tests for CSRFMiddleware — cookie issuance + verification.

Companion to ``tests/unit/test_csrf_middleware.py`` (pure-function tests).
These exercise the middleware in a real FastAPI app so we catch regressions
in request wiring (cookie source, header fallback, safe-method bypass,
middleware ordering vs SecurityHeaders).
"""

# omicron-1 F12: dashboard now requires auth; use /login for anon middleware probes.

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import Form
from httpx import ASGITransport, AsyncClient

from idraa import config, db
from idraa.app import create_app
from idraa.db import Base, get_engine
from idraa.models.enums import IndustryType, OrganizationSize, UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.services.auth import SESSION_COOKIE, hash_password

_SEED_EMAIL = "csrf-seed@example.test"


async def _seeded_admin_session_cookie() -> str:
    """Mint a fresh session for the fixture-seeded admin and return the
    signed ``idraa_session`` cookie value — the session-binding tests below
    need a real, independently-mintable session cookie per call (test 2's
    "session-A vs session-B" case needs TWO distinct ones), which is more
    direct than driving the full password-login form each time.

    Uses ``get_engine()`` (not a fresh engine) so this shares the exact
    connection/schema the ``csrf_client`` fixture already set up on the
    running app.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from idraa.services.auth import create_session, sign_session_id

    engine = get_engine()
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        user = (await session.execute(select(User).where(User.email == _SEED_EMAIL))).scalar_one()
        sess = await create_session(session, user.id, ip="127.0.0.1")
        await session.commit()
        return sign_session_id(sess.id)


@pytest_asyncio.fixture
async def csrf_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """AsyncClient on an app with a throwaway POST route for CSRF round-trips.

    We bolt a ``/__csrf_test__`` POST route onto the app BEFORE middleware
    runs (routes are matched after middleware, so this is fine). That gives
    us a success target; without it there are no POST routes to hit.

    Since Task 1.1.5 added the setup-guard middleware, we also need a real
    SQLite file (not ``:memory:``) with schema created AND at least one user
    seeded — otherwise every non-allowlisted request is 307-redirected to
    /setup before CSRF even gets a chance to run. The seed is minimal
    (one org + one user) because this fixture only exists to exercise CSRF
    behaviour, not auth.
    """
    # Per-test SQLite file so we get a real connection pool and schema
    # survives across the guard's get_session() calls and the test's posts.
    db_file = tmp_path / f"csrf-{uuid.uuid4().hex}.db"
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    config.reset_for_tests()
    db.reset_for_tests()

    app = create_app()

    # Build schema on the engine the running app will share via get_engine().
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed one user so the setup-guard's "no users -> redirect" branch
    # does not fire on the /__csrf_test__ and /__csrf_form_test__ endpoints.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        org = Organization(
            name="csrf-seed",
            industry_type=IndustryType.INFORMATION,
            organization_size=OrganizationSize.SMALL,
        )
        session.add(org)
        await session.flush()
        session.add(
            User(
                organization_id=org.id,
                email=_SEED_EMAIL,
                password_hash=hash_password("unused-for-csrf-tests"),
                full_name="CSRF Seed",
                role=UserRole.ADMIN,
                is_active=True,
                # MFA-enrolled so EnrollmentGuardMiddleware never intercepts
                # the session-binding tests below with a redirect/204
                # HX-Redirect before CSRF's own verdict is observable —
                # same precedent as security/dast/seed.py's seeded admin.
                mfa_enrolled_at=datetime.now(UTC),
            )
        )
        await session.commit()

    @app.post("/__csrf_test__")
    async def _echo() -> dict[str, str]:
        return {"ok": "true"}

    @app.post("/__csrf_form_test__")
    async def _form_echo(value: str = Form(...)) -> dict[str, str]:
        # Reads a Form(...) field AFTER the CSRF middleware has already
        # consumed the body via request.form() — regression target for the
        # BaseHTTPMiddleware body-stream consumption bug.
        return {"value": value}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()
    db.reset_for_tests()
    config.reset_for_tests()


async def test_get_sets_csrf_cookie(csrf_client: AsyncClient) -> None:
    r = await csrf_client.get("/login")
    assert r.status_code == 200
    assert "csrf_token" in r.cookies
    token = r.cookies["csrf_token"]
    assert "." in token and len(token) > 10


async def test_csrf_cookie_has_expected_attributes(csrf_client: AsyncClient) -> None:
    """Cookie must be SameSite=Strict + Path=/; HttpOnly must be FALSE so
    JS/Jinja can read it for the double-submit pattern.
    """
    r = await csrf_client.get("/login")
    set_cookies = r.headers.get_list("set-cookie")
    csrf_header = next((c for c in set_cookies if c.startswith("csrf_token=")), None)
    assert csrf_header is not None, f"csrf_token not in Set-Cookie: {set_cookies}"
    # Attributes — Starlette emits them in lowercase with capitalized keys per RFC.
    assert "samesite=strict" in csrf_header.lower()
    assert "path=/" in csrf_header.lower()
    # Double-submit requires the client to be able to read the cookie value.
    assert "httponly" not in csrf_header.lower()


async def test_consecutive_gets_return_same_cookie(csrf_client: AsyncClient) -> None:
    """Token issuance must be idempotent — once the browser has a cookie,
    subsequent GETs must not rotate it (else the cookie and any cached
    form-field value would drift out of sync mid-session)."""
    r1 = await csrf_client.get("/login")
    token1 = r1.cookies["csrf_token"]
    # httpx AsyncClient preserves cookies across requests automatically.
    r2 = await csrf_client.get("/login")
    # If the middleware tried to re-issue, Set-Cookie would appear on r2.
    set_cookies = r2.headers.get_list("set-cookie")
    assert not any(c.startswith("csrf_token=") for c in set_cookies), (
        f"CSRF cookie was rotated on second GET: {set_cookies}"
    )
    # And the value the client still holds is the original one.
    assert csrf_client.cookies["csrf_token"] == token1


async def test_post_without_cookie_and_without_field_returns_403(
    csrf_client: AsyncClient,
) -> None:
    # Fresh client with no prior GET => no cookie.
    r = await csrf_client.post("/__csrf_test__", data={})
    assert r.status_code == 403


async def test_post_with_cookie_but_no_field_returns_403(csrf_client: AsyncClient) -> None:
    await csrf_client.get("/login")  # seed cookie
    r = await csrf_client.post("/__csrf_test__", data={})
    assert r.status_code == 403


async def test_post_with_mismatched_field_returns_403(csrf_client: AsyncClient) -> None:
    await csrf_client.get("/login")
    r = await csrf_client.post("/__csrf_test__", data={"_csrf": "not-the-real-token"})
    assert r.status_code == 403


async def test_post_with_matching_form_field_succeeds(csrf_client: AsyncClient) -> None:
    await csrf_client.get("/login")
    token = csrf_client.cookies["csrf_token"]
    r = await csrf_client.post("/__csrf_test__", data={"_csrf": token})
    assert r.status_code == 200, r.text


async def test_post_with_matching_header_succeeds(csrf_client: AsyncClient) -> None:
    await csrf_client.get("/login")
    token = csrf_client.cookies["csrf_token"]
    r = await csrf_client.post("/__csrf_test__", headers={"X-CSRF-Token": token})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_bypass_csrf(csrf_client: AsyncClient, method: str) -> None:
    """Safe methods must pass even without any CSRF state — GET is where the
    cookie originates, and HEAD/OPTIONS must also be reachable pre-issuance.

    Tight assertion: 200 (GET / HEAD succeeded) or 405 (OPTIONS without CORS
    is Method Not Allowed). Any other code — especially 500 or 403 — is a
    real failure and must not be masked by a bare ``!= 403``.
    """
    r = await csrf_client.request(method, "/healthz")
    assert r.status_code in {200, 405}


async def test_403_still_has_security_headers(csrf_client: AsyncClient) -> None:
    """CSRFMiddleware must run INSIDE SecurityHeadersMiddleware so 403
    responses still carry CSP / X-Content-Type-Options / etc."""
    r = await csrf_client.post("/__csrf_test__", data={})
    assert r.status_code == 403
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Content-Security-Policy" in r.headers


async def test_csrf_field_template_global_emits_hidden_input(
    csrf_client: AsyncClient,
) -> None:
    """The ``csrf_field()`` Jinja global must be callable from templates and
    produce a hidden input whose value matches the cookie the same response
    sets. Without this the /setup wizard (plan Task 1.1.5) cannot submit.

    We test by rendering a small inline template against the real Jinja env
    with a faked request whose ``state.csrf_token`` matches the cookie — this
    exercises the helper in isolation without needing a full FastAPI route
    (routes in the test app are fixed before the test fixture hands us the
    client, so adding new ones mid-test is fragile).
    """
    from types import SimpleNamespace

    from idraa.app import templates

    # Prime the cookie so we have a real token to compare against.
    await csrf_client.get("/login")
    token = csrf_client.cookies["csrf_token"]

    # Fake Request with just the attributes the helper reads.
    fake_request = SimpleNamespace(state=SimpleNamespace(csrf_token=token))
    rendered = templates.env.from_string("<body>{{ csrf_field() }}</body>").render(
        request=fake_request
    )
    assert 'type="hidden"' in rendered
    assert 'name="_csrf"' in rendered
    assert token in rendered


async def test_csrf_token_template_var_is_raw_string(
    csrf_client: AsyncClient,
) -> None:
    """Companion to ``csrf_field``: the ``csrf_token`` template variable must
    equal the raw token string (injected by the context processor), for use
    in e.g. ``hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'``.

    We render through the real Jinja env with the same context-variable name
    the context processor uses — a simple equality check that the render
    path doesn't mangle the value.
    """
    from idraa.app import templates

    # Seed the cookie so we have a stable token to thread through.
    await csrf_client.get("/login")
    token = csrf_client.cookies["csrf_token"]
    rendered = templates.env.from_string("{{ csrf_token }}").render(csrf_token=token)
    assert rendered == token


async def test_downstream_form_handler_reads_body(csrf_client: AsyncClient) -> None:
    """Body-stream replay regression — Form(...) handlers must see the fields.

    BaseHTTPMiddleware consumes ``request.form()`` one-way unless the
    middleware explicitly re-injects a cached body via ``request._receive``.
    Without the replay, downstream ``Form(...)`` handlers receive an empty
    dict and raise 422 ("field required"). This test guards against that
    regression: we submit a valid CSRF token in the form AND a real payload,
    and assert the handler actually got the payload.
    """
    await csrf_client.get("/login")  # seed cookie
    token = csrf_client.cookies["csrf_token"]
    r = await csrf_client.post(
        "/__csrf_form_test__",
        data={"_csrf": token, "value": "hello-from-form"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"value": "hello-from-form"}


# --- Session-binding advisory proofs (GHSA-46jj-823j-mjj9 B4) ---
#
# Named wrong implementations these prove wrong, per the design doc:
#   W1 — HEAD's unbound signer (token proves only "server minted it").
#   W2 — binding built from request.state.session/user (always None on the
#        wire position CSRF actually runs at — outside/before Session — so
#        W2 degenerates to "everything binds anon").
#   W4 — re-verification still using the unbound verify, so nothing re-mints
#        after login.


async def test_anon_minted_token_rejected_on_authenticated_write(
    csrf_client: AsyncClient,
) -> None:
    """A token minted for an anonymous visitor must not authorize a write
    once a session cookie is present — the token proves "this server minted
    it", not "for this session" (red under W1: HEAD's unbound signer
    accepts any correctly-HMAC'd token regardless of session).
    """
    r = await csrf_client.get("/login")
    anon_token = r.cookies["csrf_token"]

    cookie = await _seeded_admin_session_cookie()
    csrf_client.cookies.set(SESSION_COOKIE, cookie)

    r = await csrf_client.post("/__csrf_test__", data={"_csrf": anon_token})
    assert r.status_code == 403


async def test_session_a_token_rejected_with_session_b_cookie(
    csrf_client: AsyncClient,
) -> None:
    """A token bound to session A must not authorize a write presented
    alongside session B's cookie (red under W1 and W2: W1 has no binding at
    all; W2's binding is always anon regardless of which session cookie is
    actually present, so A and B collapse to the same "anon" binding).
    """
    cookie_a = await _seeded_admin_session_cookie()
    csrf_client.cookies.set(SESSION_COOKIE, cookie_a)
    r = await csrf_client.get("/login")
    token_a = r.cookies["csrf_token"]

    cookie_b = await _seeded_admin_session_cookie()
    csrf_client.cookies.set(SESSION_COOKIE, cookie_b)

    r = await csrf_client.post("/__csrf_test__", data={"_csrf": token_a})
    assert r.status_code == 403


async def test_duplicate_session_cookies_bind_to_last_not_first(
    csrf_client: AsyncClient,
) -> None:
    """Starlette's Cookie parser is last-wins on a duplicate header name —
    the SAME accessor SessionMiddleware authenticates with (session.py:44,
    last-wins on duplicates). A token manually bound to the FIRST duplicate
    value must be rejected when the live request's duplicate cookie resolves
    (last-wins) to a DIFFERENT value (red under W3: a binding read via a
    different parser, or the first duplicate, would incorrectly accept the
    A-bound token here since it'd also compute the binding from "A").
    """
    from starlette.requests import Request

    from idraa.middleware.csrf import csrf_binding, derive_csrf_key, generate_csrf_token

    # Matches the csrf_client fixture's monkeypatched SESSION_SECRET.
    key = derive_csrf_key("x" * 32)
    fake_request_a = Request({"type": "http", "headers": [(b"cookie", b"idraa_session=A")]})
    binding_a = csrf_binding(fake_request_a, SESSION_COOKIE)
    token_bound_to_a = generate_csrf_token(key, binding_a)

    r = await csrf_client.post(
        "/__csrf_test__",
        data={"_csrf": token_bound_to_a},
        headers={
            b"Cookie": (f"csrf_token={token_bound_to_a}; idraa_session=A; idraa_session=B").encode()
        },
    )
    assert r.status_code == 403


async def test_authenticated_get_with_anon_bound_cookie_remints(
    csrf_client: AsyncClient,
) -> None:
    """An authenticated GET carrying a still-anon-bound cookie must re-mint
    (fresh Set-Cookie, new value) — and a SUBSEQUENT authenticated GET with
    the now session-bound cookie must NOT rotate again (idempotent, same
    invariant ``test_consecutive_gets_return_same_cookie`` pins pre-login).

    Red under W4: if re-verification still used the unbound verify, the
    anon-bound cookie would look "still valid" forever and never re-mint
    after login.
    """
    r = await csrf_client.get("/login")
    anon_token = r.cookies["csrf_token"]

    cookie = await _seeded_admin_session_cookie()
    csrf_client.cookies.set(SESSION_COOKIE, cookie)

    r2 = await csrf_client.get("/login")
    set_cookies2 = r2.headers.get_list("set-cookie")
    csrf_header = next((c for c in set_cookies2 if c.startswith("csrf_token=")), None)
    assert csrf_header is not None, (
        "expected a re-mint (Set-Cookie) once a session cookie is present "
        f"alongside the still-anon-bound cookie: {set_cookies2}"
    )
    new_token = csrf_client.cookies["csrf_token"]
    assert new_token != anon_token

    # Idempotent from here on — a second authenticated GET must not rotate.
    r3 = await csrf_client.get("/login")
    set_cookies3 = r3.headers.get_list("set-cookie")
    assert not any(c.startswith("csrf_token=") for c in set_cookies3), (
        f"CSRF cookie was rotated on a second authenticated GET: {set_cookies3}"
    )


async def test_stale_cookie_post_with_hx_request_gets_refresh_and_fresh_cookie(
    csrf_client: AsyncClient,
) -> None:
    """A present-but-stale inbound cookie (fails the current binding) must
    403 WITH a fresh Set-Cookie AND ``HX-Refresh: true`` for an HX-Request,
    so HTMX reloads the whole page (mints a valid cookie) instead of
    swapping a bare "Forbidden" into the DOM (rev 3 Sec-N1/Arch-N6).
    """
    r = await csrf_client.get("/login")
    anon_token = r.cookies["csrf_token"]

    cookie = await _seeded_admin_session_cookie()
    csrf_client.cookies.set(SESSION_COOKIE, cookie)

    r2 = await csrf_client.post(
        "/__csrf_test__",
        data={"_csrf": anon_token},
        headers={"HX-Request": "true"},
    )
    assert r2.status_code == 403
    assert r2.headers.get("HX-Refresh") == "true"
    set_cookies = r2.headers.get_list("set-cookie")
    assert any(c.startswith("csrf_token=") for c in set_cookies), (
        f"present-but-stale inbound cookie must re-mint on its own 403: {set_cookies}"
    )


async def test_missing_cookie_post_with_hx_request_gets_refresh_but_no_cookie(
    csrf_client: AsyncClient,
) -> None:
    """No inbound cookie at all (the cross-site-POST shape — a cross-site
    page cannot read/send a Strict cookie) must still 403 + HX-Refresh, but
    must NEVER Set-Cookie on that response — minting there would let a
    cross-site page reset the victim's cookie (rev 3 Sec-N1).
    """
    r = await csrf_client.post(
        "/__csrf_test__",
        data={},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 403
    assert r.headers.get("HX-Refresh") == "true"
    set_cookies = r.headers.get_list("set-cookie")
    assert not any(c.startswith("csrf_token=") for c in set_cookies), (
        f"a cookie-less 403 must never Set-Cookie: {set_cookies}"
    )


async def test_non_hx_403_has_no_hx_refresh_header(csrf_client: AsyncClient) -> None:
    """A plain (non-HTMX) 403 must not carry HX-Refresh — it's meaningless
    (and mildly informative to non-HTMX callers) outside an HX-Request."""
    r = await csrf_client.post("/__csrf_test__", data={})
    assert r.status_code == 403
    assert "HX-Refresh" not in r.headers
