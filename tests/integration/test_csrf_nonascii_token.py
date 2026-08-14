"""Regression tests for D1: non-ASCII CSRF token must 403, never 500.

``hmac.compare_digest`` raises ``TypeError: comparing strings with non-ASCII
characters is not supported`` when either operand contains a non-ASCII
character. Both ``hmac.compare_digest`` call sites in
``idraa.middleware.csrf`` compare attacker-controllable strings (the
submitted form/header token, and the raw cookie signature) that were never
ASCII/hex-validated before reaching the compare — so a non-ASCII token turns
into an unhandled 500 instead of a clean CSRF rejection. The cookie-path case
is the most severe: ``verify_csrf_token`` runs on EVERY request including
safe GETs (before the unsafe-method branch), so a crafted
``Cookie: csrf_token=<hex>.<non-ascii>`` is an unauthenticated 500 on any
route, not just POSTs.

Companion to ``tests/integration/test_csrf_integration.py``.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_nonascii_form_csrf_is_403(client: AsyncClient) -> None:
    """Non-ASCII ``_csrf`` form field must 403, not 500 (dispatch compare site)."""
    await client.get("/login")
    tok = client.cookies.get("csrf_token")
    assert tok
    r = await client.post(
        "/login",
        data={"email": "a@b.c", "password": "x", "_csrf": tok + "¿"},
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_nonascii_header_csrf_is_403(client: AsyncClient) -> None:
    """Non-ASCII X-CSRF-Token header must 403, not 500 (dispatch compare site).

    httpx's ``Headers`` normalizer ascii-encodes a ``str`` header value
    client-side and raises ``UnicodeEncodeError`` before the request is even
    built — so a plain ``str`` value here never reaches the server, it fails
    in the test client instead (confirmed: this repo's pinned httpx version
    does exactly that). Passing the value pre-encoded as ``bytes`` skips that
    str-path entirely (httpx forwards bytes header values unmodified), which
    is what actually gets a non-ASCII byte onto the wire for this guard.
    """
    await client.get("/login")
    tok = client.cookies.get("csrf_token")
    assert tok
    r = await client.post(
        "/login",
        data={"email": "a@b.c", "password": "x"},
        headers={b"X-CSRF-Token": (tok + "¿").encode("utf-8")},
        follow_redirects=False,
    )
    assert r.status_code == 403


async def test_nonascii_cookie_sig_is_not_500(client: AsyncClient) -> None:
    """Non-ASCII inbound cookie signature must not 500 on a plain GET.

    ``verify_csrf_token`` runs unconditionally at the top of ``dispatch`` (to
    decide whether to reuse or reissue the cookie) — well before the
    unsafe-method branch — so this is reachable on any GET, unauthenticated.

    Same httpx client-side ascii-encode caveat as the header test above, but
    on the ``Cookie`` request header this time: ``client.cookies.set(...)``
    stores the raw str fine, but serializing it onto the wire via the
    stdlib-``cookiejar``-driven ``Cookies.set_cookie_header`` ascii-encodes
    and raises client-side (confirmed) before the request is ever sent. Set
    the ``Cookie`` header directly as pre-encoded ``bytes`` instead — the
    jar stays empty so it never emits a competing ``Cookie`` header, and this
    is what actually gets a non-ASCII byte onto the wire for this guard.
    """
    r = await client.get(
        "/login",
        headers={b"Cookie": b"csrf_token=aabbccdd.\xc2\xbf"},
        follow_redirects=False,
    )
    # A non-ASCII cookie sig is rejected as a mismatch -> the cookie is
    # reissued -> the plain GET /login still renders normally: 200, not 500.
    assert r.status_code == 200
