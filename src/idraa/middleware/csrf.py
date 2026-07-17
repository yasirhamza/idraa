"""CSRF protection — stateless double-submit signed-cookie pattern.

Design (see Task 1.1.0 brief):

- Token shape: ``<nonce_hex>.<sig_hex>`` where ``nonce`` is 32 random bytes
  hex-encoded and ``sig`` is ``HMAC-SHA256(session_secret, nonce_bytes)``
  hex-encoded.
- Cookie ``csrf_token`` is set on any response that does not already carry
  one. ``HttpOnly=False`` (intentional — the double-submit pattern requires
  JS/Jinja to read the value for the form field or X-CSRF-Token header).
  ``SameSite=Strict`` + ``Secure`` in non-dev envs. ``Path=/``.
- On unsafe methods (POST/PUT/PATCH/DELETE), the middleware requires the
  cookie value to match EITHER a form field ``_csrf`` OR a header
  ``X-CSRF-Token``. Signature is re-verified from the nonce portion. No
  server-side storage is needed — the secret is the source of truth.
- Safe methods (GET/HEAD/OPTIONS) bypass verification; the cookie is still
  issued on the response so the FIRST POST after a GET has something to
  match against.

Stateless-ness is the whole point: session middleware does not land until
plan Task 1.1.4, but CSRF must be usable by Task 1.1.5 (/setup) before
that. This middleware has zero dependencies on session state.

Middleware order (set in ``app.py``):

    CSRFMiddleware runs INSIDE SecurityHeadersMiddleware so a 403 emitted
    here still gets Content-Security-Policy, X-Content-Type-Options, etc.
    FastAPI runs middleware in reverse-add order (LIFO), so ``app.py`` adds
    ``CSRFMiddleware`` BEFORE ``SecurityHeadersMiddleware``.

Body-stream replay (Task 1.1.0.a):

    ``BaseHTTPMiddleware`` exposes ``request.form()`` / ``request.body()``,
    but consuming either one-way-drains the ASGI receive stream — downstream
    ``Form(...)`` handlers then see an empty dict. We cache the body with
    ``await request.body()`` BEFORE parsing the form, then reinject a fresh
    ``receive`` coroutine into ``request._receive`` so the route handler
    re-reads the same bytes. Regression test:
    ``test_downstream_form_handler_reads_body``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "csrf_token"
CSRF_FORM_FIELD = "_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def generate_csrf_token(secret: str) -> str:
    """Return a fresh ``<nonce_hex>.<sig_hex>`` token.

    Nonce is 32 random bytes from ``secrets.token_hex`` (CSPRNG).
    Signature is HMAC-SHA256 over the nonce bytes (NOT the hex string — we
    sign the underlying bytes so a future change to nonce encoding does not
    silently shift signatures).

    Defensive: reject empty / whitespace-only secrets with ``ValueError``
    rather than silently producing an HMAC keyed by "". Belt-and-suspenders
    against callers that bypass ``Settings`` (direct construction in tests,
    scripts, etc.) — ``Settings`` itself enforces ``min_length=16``.
    """
    if not secret or not secret.strip():
        raise ValueError("CSRF signing requires a non-empty session_secret")
    nonce_hex = secrets.token_hex(32)
    nonce_bytes = bytes.fromhex(nonce_hex)
    sig = hmac.new(secret.encode("utf-8"), nonce_bytes, hashlib.sha256).hexdigest()
    return f"{nonce_hex}.{sig}"


def verify_csrf_token(token: str, secret: str) -> bool:
    """Return True iff ``token`` is a well-formed, correctly-signed token.

    Uses :func:`hmac.compare_digest` for the final comparison so the check
    is constant-time w.r.t. the signature bytes (blocks timing side-channels
    that could leak the valid HMAC one byte at a time).
    """
    if not token or "." not in token:
        return False
    try:
        nonce_hex, sig_hex = token.split(".", 1)
    except ValueError:
        return False
    if not nonce_hex or not sig_hex:
        return False
    try:
        nonce_bytes = bytes.fromhex(nonce_hex)
    except ValueError:
        return False
    expected = hmac.new(secret.encode("utf-8"), nonce_bytes, hashlib.sha256).hexdigest()
    # Constant-time compare — see module docstring.
    return hmac.compare_digest(expected, sig_hex)


async def _extract_submitted_token(request: Request) -> str | None:
    """Pull a CSRF token from the request — header first, then form field.

    Header is checked first because HTMX callers with ``hx-headers`` set
    won't send a form body at all. Reading the form body is only attempted
    when no header is present, to avoid parsing JSON/multipart bodies for
    no reason.

    Note on body consumption: the caller (``CSRFMiddleware.dispatch``) has
    ALREADY cached ``await request.body()`` and reinjected ``request._receive``
    before calling us, so ``request.form()`` here re-reads from the cached
    buffer and downstream ``Form(...)`` handlers still see the fields.
    """
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if header_value:
        return header_value
    content_type = request.headers.get("content-type", "")
    # Only parse form-ish bodies. Starlette's request.form() handles both
    # urlencoded and multipart — anything else (JSON API calls) must use the
    # header instead.
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except (AssertionError, ValueError, UnicodeDecodeError):
            # Malformed / truncated / non-UTF-8 body is "no token" — fail-closed.
            # Narrow the catch from bare `Exception` so programmer bugs
            # (AttributeError, etc.) still surface instead of being swallowed.
            logger.debug("csrf: form parse failed", exc_info=True)
            return None
        value = form.get(CSRF_FORM_FIELD)
        if isinstance(value, str):
            return value
    return None


class CSRFMiddleware(BaseHTTPMiddleware):
    """Stateless CSRF — double-submit a signed cookie.

    Attaches ``request.state.csrf_token`` for downstream handlers / Jinja
    globals: if an inbound cookie is present and valid, reuse it; otherwise
    mint a fresh one now. That way the SAME value is available to render
    into forms AND set on the Set-Cookie header of the response.

    Fail-closed: any missing/invalid piece on an unsafe method returns
    ``403 Forbidden``. No allowlist of exempt routes — callers that truly
    need to POST without CSRF (none today) must opt out explicitly.

    Error responses deliberately use a single opaque ``"Forbidden"`` body;
    the specific failure mode (cookie missing / cookie invalid / token
    missing / token mismatch) is logged at WARNING for operators but NOT
    leaked to the caller — distinguishing them client-side gave an attacker
    a free oracle on which half of the double-submit failed.
    """

    def __init__(self, app: ASGIApp, secret: str, *, secure_cookie: bool) -> None:
        super().__init__(app)
        self._secret = secret
        self._secure_cookie = secure_cookie

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # 0. Cache the body + reinject a fresh receive BEFORE any form
        #    parsing. BaseHTTPMiddleware's request.form() / request.body()
        #    drain the ASGI receive stream one-way; without this replay,
        #    downstream ``Form(...)`` handlers see an empty dict and raise
        #    422. We only need to do this for methods that might carry a
        #    body — safe methods skip the whole path.
        if request.method in _UNSAFE_METHODS:
            body = await request.body()

            async def _replay_receive() -> dict[str, object]:
                return {"type": "http.request", "body": body, "more_body": False}

            # Starlette's Request exposes `_receive` specifically so middleware
            # can splice in a new source — see starlette.requests.Request.
            request._receive = _replay_receive

        # 1. Resolve the token for THIS request. Prefer a valid inbound
        #    cookie; mint a new one only if absent or tampered. This keeps
        #    issuance idempotent across consecutive GETs (test:
        #    ``test_consecutive_gets_return_same_cookie``).
        inbound = request.cookies.get(CSRF_COOKIE_NAME)
        if inbound and verify_csrf_token(inbound, self._secret):
            token = inbound
            fresh = False
        else:
            token = generate_csrf_token(self._secret)
            fresh = True

        # Expose to downstream (templates read this via the csrf_token global).
        request.state.csrf_token = token

        # 2. Verify unsafe methods BEFORE invoking the route — a forgery
        #    must never reach the handler.
        if request.method in _UNSAFE_METHODS:
            # First-post-without-prior-GET: no cookie at all => reject.
            if not inbound:
                return self._forbid("cookie missing", request)
            if not verify_csrf_token(inbound, self._secret):
                return self._forbid("cookie invalid", request)
            submitted = await _extract_submitted_token(request)
            if not submitted:
                return self._forbid("token missing from request", request)
            # Constant-time compare of the two full token strings — we
            # already verified the cookie signature, so this is the
            # double-submit equality check.
            if not hmac.compare_digest(submitted, inbound):
                return self._forbid("token mismatch", request)

        # 3. Run the route, then attach the cookie if it was freshly minted.
        response = await call_next(request)
        if fresh:
            response.set_cookie(
                CSRF_COOKIE_NAME,
                token,
                httponly=False,  # JS/Jinja must read — double-submit pattern
                samesite="strict",
                secure=self._secure_cookie,
                path="/",
            )
        return response

    @staticmethod
    def _forbid(reason: str, request: Request) -> Response:
        # Body is a fixed opaque string. The specific reason lives only in
        # the server log — distinguishing failure modes client-side hands
        # an attacker a free oracle on which check tripped.
        logger.warning(
            "csrf: rejected %s %s (%s)",
            request.method,
            request.url.path,
            reason,
        )
        return PlainTextResponse("Forbidden", status_code=403)
