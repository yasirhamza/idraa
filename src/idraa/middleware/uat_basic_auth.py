"""UAT basic-auth pre-gate middleware (Phase 1.5.5).

The OUTERMOST request-path layer for the hosted UAT runtime. When
``UAT_BASIC_AUTH_PASSWORD`` is set in the env (Fly secrets in production
UAT), every request without a valid ``Authorization: Basic`` header is
rejected with 401 — except ``/healthz``, which is unconditionally exempt
so Fly's health probe can pass.

When the password is unset (dev, test, local docker), this middleware is
a no-op: the existing inner stack (setup_guard → SecurityHeaders → CSRF
→ Session → UnconfirmedCount) handles the request normally.

The pre-gate is layered on top of the app's existing /login session
auth; compromise of the edge credential does not equal compromise of
the app. Single shared credential per Q3 / Q5 of the design doc.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

EXEMPT_PATHS = frozenset({"/healthz"})

DispatchFn = Callable[[Request], Awaitable[Response]]
MiddlewareFn = Callable[[Request, DispatchFn], Awaitable[Response]]


def uat_basic_auth_factory(*, user: str | None = None, password: str | None = None) -> MiddlewareFn:
    """Build the basic-auth middleware function.

    If ``user`` or ``password`` is None, the corresponding env var
    (``UAT_BASIC_AUTH_USER``, ``UAT_BASIC_AUTH_PASSWORD``) is consulted.
    Tests pass explicit values; production reads env via Fly secrets.
    """
    eff_user = user if user is not None else os.environ.get("UAT_BASIC_AUTH_USER")
    eff_password = password if password is not None else os.environ.get("UAT_BASIC_AUTH_PASSWORD")

    async def uat_basic_auth(request: Request, call_next: DispatchFn) -> Response:
        # Health-probe exemption is unconditional: Fly's probe runs without
        # credentials, and a 401 there would loop the deploy.
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        # No password configured = middleware is a no-op (dev/test).
        if not eff_password:
            return await call_next(request)
        # Empty-string user is a misconfiguration trap: the comparison would
        # let any caller through with empty user + correct password. Fail
        # closed before parsing the header.
        if not eff_user:
            return _unauthorized()
        if not _check_auth(
            request.headers.get("authorization", ""),
            expected_user=eff_user,
            expected_password=eff_password,
        ):
            return _unauthorized()
        return await call_next(request)

    return uat_basic_auth


def _unauthorized() -> Response:
    return Response(
        content="UAT auth required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Idraa UAT"'},
    )


def _check_auth(header: str, *, expected_user: str, expected_password: str) -> bool:
    """Validate an ``Authorization: Basic ...`` header in constant time.

    secrets.compare_digest mitigates timing-leak attacks on the credential
    comparison. A naive ``==`` would short-circuit on the first mismatched
    byte and leak length/prefix information over the network.

    Both compares MUST run on every attempt. The naive form
    ``compare_digest(u, eu) and compare_digest(p, ep)`` short-circuits on
    user-mismatch — leaving observable timing differences for "wrong user"
    vs "wrong password". Assigning each compare to a bool first and AND-ing
    at the end ensures both calls run unconditionally.
    """
    # Scheme prefix check is space-only by design. RFC 7235 §2.1 permits
    # any LWS between scheme and credentials, but no real client (browser
    # or curl) emits a tab here, and accepting tabs broadens the parsing
    # surface unnecessarily. If a non-conforming client matters later,
    # widen this check + add a tab-separator test.
    if not header.lower().startswith("basic "):
        return False
    encoded = header.split(" ", 1)[1].strip()
    # .strip() handles `Basic  <payload>` (double space) — split on first
    # space leaves leading whitespace which strip removes.
    try:
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    # partition (not split) so passwords containing `:` survive intact —
    # `user:pass:word` → user="user", password="pass:word".
    user, _, password = decoded.partition(":")
    ok_user = secrets.compare_digest(user, expected_user)
    ok_password = secrets.compare_digest(password, expected_password)
    return ok_user and ok_password
