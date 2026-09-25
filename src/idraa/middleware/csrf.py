"""CSRF protection — session-bound double-submit signed-cookie pattern.

Design (GHSA-46jj-823j-mjj9 finding "B4", see
``docs/superpowers/specs/2026-09-23-csrf-session-binding-design.md``):

- Token shape: ``<nonce_hex>.<sig_hex>`` where ``nonce`` is 32 random bytes
  hex-encoded and ``sig`` is
  ``HMAC-SHA256(key, nonce_bytes || binding)`` hex-encoded. ``key`` is
  DERIVED once from ``session_secret`` (:func:`derive_csrf_key`) — never the
  raw secret itself, so a token signed the old (pre-B4) way can never
  verify under the new scheme (key separation). ``binding`` is
  :func:`csrf_binding`'s 32-byte hash of the request's raw session-cookie
  value, or a fixed anon binding when no session cookie is present.
  Binding the signature to the session means a token minted for one
  session (or for no session at all) cannot authorize a write presented
  alongside a DIFFERENT session's cookie — closing B4: pre-fix, the token
  proved only "this server minted it", not "for this session".
- Cookie ``csrf_token`` is set on any response that does not already carry
  a cookie valid under the CURRENT binding. ``HttpOnly=False`` (intentional
  — the double-submit pattern requires JS/Jinja to read the value for the
  form field or X-CSRF-Token header). ``SameSite=Strict`` + ``Secure`` in
  non-dev envs. ``Path=/``.
- On unsafe methods (POST/PUT/PATCH/DELETE), the middleware requires the
  cookie value to match EITHER a form field ``_csrf`` OR a header
  ``X-CSRF-Token``, AND to verify under the CURRENT binding. No
  server-side storage is needed — the secret (via the derived key) is the
  source of truth.
- Safe methods (GET/HEAD/OPTIONS) bypass verification; the cookie is still
  issued on the response so the FIRST POST after a GET has something to
  match against.
- **Stale-cookie re-mint (rev 3 Sec-N1):** an inbound cookie that fails
  under the CURRENT binding is treated like a tampered one — mint fresh,
  expose on ``request.state.csrf_token`` for the route/template to use.
  On an UNSAFE method the request still 403s, but the Set-Cookie behavior
  on that 403 depends on why: a cookie PRESENT-but-stale re-mints (Set-
  Cookie on the 403 itself, so the next request already has a valid
  cookie) — but a cookie ABSENT entirely does NOT get a Set-Cookie on its
  403, because that shape is exactly a cross-site POST (no Strict cookie
  rides along), and minting there would let a cross-site page reset the
  victim's csrf_token cookie. Every 403 to an ``HX-Request`` request
  carries ``HX-Refresh: true`` so HTMX reloads the whole page (picking up
  a fresh, valid cookie via the reload's GET) instead of swapping a bare
  "Forbidden" into the DOM.

Stateless-ness (no server-side token store) is the whole point: session
middleware runs INSIDE this one on the wire (see below) and this module
never imports from ``services/`` — it depends only on the raw session
COOKIE VALUE via :func:`csrf_binding`'s ``session_cookie_name`` parameter,
never on ``request.state.session``/``request.state.user`` (which
``SessionMiddleware`` populates further inside the stack, after CSRF has
already made its verdict).

Middleware order (set in ``app.py``):

    CSRFMiddleware runs INSIDE SecurityHeadersMiddleware so a 403 emitted
    here still gets Content-Security-Policy, X-Content-Type-Options, etc.
    FastAPI runs middleware in reverse-add order (LIFO), so ``app.py`` adds
    ``CSRFMiddleware`` BEFORE ``SecurityHeadersMiddleware``. CSRFMiddleware
    also runs OUTSIDE (before) SessionMiddleware — session lookup hits the
    DB, so CSRF should reject forgeries before burning a DB round trip —
    which is exactly why :func:`csrf_binding` reads the raw cookie directly
    rather than ``request.state.session``: that attribute does not exist
    yet when CSRF's dispatch runs.

Body-stream replay + size cap (Task 1.1.0.a; A4 cap 2026-08-09):

    ``BaseHTTPMiddleware`` wraps every request in a ``_CachedRequest`` whose
    ``wrapped_receive`` — the receive the DOWNSTREAM app reads — replays the
    body from ``request._body`` once that attribute is populated. So to let
    downstream ``Form(...)`` handlers see the body (consuming the stream
    naively would leave them an empty dict), we buffer it here BEFORE parsing
    the form and cache it on ``request._body``. ``_read_body_capped`` does
    exactly what ``await request.body()`` did — iterate ``request.stream()``
    and set ``request._body`` — but under a running-total size cap
    (``settings.max_request_body_bytes``): an oversize body returns a 413
    before it is fully buffered, instead of pinning RSS to the body size.
    (No ``request._receive`` reinjection is involved — the ``_body`` cache is
    what drives replay.) Regression test:
    ``test_downstream_form_handler_reads_body``; cap tests:
    ``tests/unit/test_csrf_body_cap.py``.
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

# HMAC "info" string for key derivation (derive_csrf_key) — versioned so a
# future scheme change can mint a "-v3" key without colliding with tokens
# signed under this one (key separation, not secrecy: this string is not
# secret, only the session_secret is).
_KEY_DERIVATION_INFO = b"idraa-csrf-v2"

# Fixed binding used when no session cookie is present (or it's present but
# empty — see csrf_binding). A per-process constant, not itself secret;
# what makes it unforgeable is that a token can only be produced by someone
# holding session_secret (via the derived key).
_ANON_BINDING = hashlib.sha256(b"idraa-csrf-anon").digest()

# Fallback body cap when a caller constructs the middleware without one
# (direct construction in tests/scripts). app.py wires the configured
# ``settings.max_request_body_bytes`` explicitly. Keep this >= MAX_UPLOAD_BYTES
# (routes/deps.py, 5 MB) + multipart framing so legitimate imports never 413.
_DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024


async def _read_body_capped(request: Request, cap: int) -> bytes | None:
    """Buffer the request body via ``request.stream()``, bounded to ``cap``.

    Returns the full body, or ``None`` if it exceeds ``cap`` (caller emits a
    413). This mirrors what ``await request.body()`` does — iterate
    ``request.stream()`` and cache the result on ``request._body`` — because
    ``BaseHTTPMiddleware`` wraps every request in a ``_CachedRequest`` whose
    ``wrapped_receive`` (the receive the DOWNSTREAM app reads) replays from
    exactly that ``_body`` cache. The ONLY difference from ``body()`` is the
    running-total check: a chunk that would push past ``cap`` is dropped and
    ``None`` returned, so peak buffering is ``cap`` + one transport chunk
    rather than the whole (possibly huge) body. Reading the raw ``_receive``
    directly instead would leave ``_body`` unset and hand the downstream app an
    empty body.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            return None
        if chunk:
            chunks.append(chunk)
    body = b"".join(chunks)
    # Populate the cache _CachedRequest.wrapped_receive replays downstream.
    request._body = body
    return body


def derive_csrf_key(secret: str) -> bytes:
    """Derive the per-process HMAC signing key from ``session_secret``.

    ``key = HMAC-SHA256(session_secret, _KEY_DERIVATION_INFO)`` — a fixed,
    non-secret "info" string, so this is key SEPARATION (the CSRF key is
    provably different from ``session_secret`` itself and from any other
    key derived from it with a different info string), not a fresh secret.
    Called ONCE at :class:`CSRFMiddleware`'s ``__init__`` time; every
    ``generate_csrf_token``/``verify_csrf_token`` call after that operates
    on the derived ``bytes`` key, never the raw ``secret`` string.

    Defensive: reject empty / whitespace-only secrets with ``ValueError``
    rather than silently deriving a key from "". Belt-and-suspenders
    against callers that bypass ``Settings`` (direct construction in tests,
    scripts, etc.) — ``Settings`` itself enforces ``min_length=16``.
    """
    if not secret or not secret.strip():
        raise ValueError("CSRF signing requires a non-empty session_secret")
    return hmac.new(secret.encode("utf-8"), _KEY_DERIVATION_INFO, hashlib.sha256).digest()


def csrf_binding(request: Request, session_cookie_name: str) -> bytes:
    """Return the 32-byte binding for ``request``: a hash of the raw
    session-cookie value, or a fixed anon binding if absent/empty.

    Reads EXACTLY ``request.cookies.get(session_cookie_name)`` — the same
    Starlette-parsed accessor ``SessionMiddleware`` authenticates with
    (``session.py:44``; last-wins on a duplicate ``Cookie`` header), so CSRF
    and session auth can never disagree about which cookie value "is" the
    session. An empty string is treated as absent, mirroring
    ``session.py:45``'s ``if signed:`` truthy check.

    ``.encode("utf-8")`` cannot raise: Starlette decodes cookie values as
    latin-1 (every byte value maps to exactly one code point 0-255), and
    every ``str`` is UTF-8-encodable — so a non-ASCII/garbage session
    cookie hashes cleanly instead of ever reaching an unhandled exception.
    """
    cookie = request.cookies.get(session_cookie_name)
    if not cookie:
        return _ANON_BINDING
    return hashlib.sha256(cookie.encode("utf-8")).digest()


def generate_csrf_token(key: bytes, binding: bytes) -> str:
    """Return a fresh ``<nonce_hex>.<sig_hex>`` token bound to ``binding``.

    Nonce is 32 random bytes from ``secrets.token_hex`` (CSPRNG).
    Signature is HMAC-SHA256 over ``nonce_bytes || binding`` (NOT the hex
    string — we sign the underlying bytes so a future change to nonce
    encoding does not silently shift signatures), keyed by ``key`` (see
    :func:`derive_csrf_key` — never the raw ``session_secret``).
    """
    nonce_hex = secrets.token_hex(32)
    nonce_bytes = bytes.fromhex(nonce_hex)
    sig = hmac.new(key, nonce_bytes + binding, hashlib.sha256).hexdigest()
    return f"{nonce_hex}.{sig}"


def verify_csrf_token(token: str, key: bytes, binding: bytes) -> bool:
    """Return True iff ``token`` is a well-formed, correctly-signed,
    ``binding``-bound token.

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
    if len(nonce_bytes) != 32:
        # Nonce is always exactly 32 CSPRNG bytes (generate_csrf_token) — a
        # different length is never something this process minted.
        return False
    if not sig_hex.isascii():
        # hmac.compare_digest raises TypeError on non-ASCII str operands
        # (D1). sig_hex is never hex-validated before this point (unlike
        # nonce_hex, which goes through bytes.fromhex above), so a crafted
        # cookie can carry a non-ASCII signature — reject it as invalid
        # rather than letting the TypeError escape as an unhandled 500.
        return False
    expected = hmac.new(key, nonce_bytes + binding, hashlib.sha256).hexdigest()
    # Constant-time compare — see module docstring.
    return hmac.compare_digest(expected, sig_hex)


async def _extract_submitted_token(request: Request) -> str | None:
    """Pull a CSRF token from the request — header first, then form field.

    Header is checked first because HTMX callers with ``hx-headers`` set
    won't send a form body at all. Reading the form body is only attempted
    when no header is present, to avoid parsing JSON/multipart bodies for
    no reason.

    Note on body consumption: the caller (``CSRFMiddleware.dispatch``) has
    ALREADY buffered the body onto ``request._body`` (via
    ``_read_body_capped``), so ``request.form()`` here re-reads from that cache
    and downstream ``Form(...)`` handlers still see the fields.
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

    Error responses deliberately use ONE fixed body for every failure (a
    "Forbidden ... reload the page and try again" hint, identical whatever
    failed);
    the specific failure mode (cookie missing / cookie invalid / token
    missing / token mismatch) is logged at WARNING for operators but NOT
    leaked to the caller — distinguishing them client-side gave an attacker
    a free oracle on which half of the double-submit failed.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret: str,
        *,
        secure_cookie: bool,
        session_cookie_name: str,
        max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        super().__init__(app)
        # Derived ONCE here — every sign/verify call after this uses the
        # derived key, never the raw secret (see derive_csrf_key).
        self._key = derive_csrf_key(secret)
        self._secure_cookie = secure_cookie
        self._session_cookie_name = session_cookie_name
        self._max_body_bytes = max_body_bytes

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
            # A4: buffer the body under a size cap. ``_read_body_capped`` caches
            # it on ``request._body``, which _CachedRequest.wrapped_receive
            # replays to the downstream handler — so this both bounds the buffer
            # AND preserves the double-submit body replay the old
            # ``await request.body()`` provided.
            body = await _read_body_capped(request, self._max_body_bytes)
            if body is None:
                # Reject oversize bodies here — before the route, and before the
                # whole body is buffered — so an unauthenticated POST can't pin
                # RSS to an arbitrary body size.
                logger.warning(
                    "csrf: request body over %d-byte cap on %s %s — 413",
                    self._max_body_bytes,
                    request.method,
                    request.url.path,
                )
                return PlainTextResponse("Payload too large", status_code=413)

        # Computed ONCE per request — both the mint-or-reuse decision below
        # AND the unsafe-method verification (step 2) must agree on the
        # SAME binding, or a request could mint under one session view and
        # verify under another.
        binding = csrf_binding(request, self._session_cookie_name)

        # 1. Resolve the token for THIS request. Prefer a valid inbound
        #    cookie (valid under the CURRENT binding); mint a new one only
        #    if absent or invalid/stale. This keeps issuance idempotent
        #    across consecutive same-binding GETs (test:
        #    ``test_consecutive_gets_return_same_cookie``) while re-minting
        #    the moment the binding changes (e.g. anon -> post-login
        #    session; test: ``test_authenticated_get_with_anon_bound_cookie_remints``).
        inbound = request.cookies.get(CSRF_COOKIE_NAME)
        if inbound and verify_csrf_token(inbound, self._key, binding):
            token = inbound
            fresh = False
        else:
            token = generate_csrf_token(self._key, binding)
            fresh = True

        # Expose to downstream (templates read this via the csrf_token global).
        request.state.csrf_token = token

        # 2. Verify unsafe methods BEFORE invoking the route — a forgery
        #    must never reach the handler.
        if request.method in _UNSAFE_METHODS:
            # First-post-without-prior-GET, or a genuinely cookie-less
            # cross-site POST (no Strict cookie rides along): reject WITHOUT
            # minting/Set-Cookie on the 403 — minting here would let a
            # cross-site page reset the victim's cookie (rev 3 Sec-N1).
            if not inbound:
                return self._forbid("cookie missing", request)
            if not verify_csrf_token(inbound, self._key, binding):
                # Present but stale under the CURRENT binding (tampered, or
                # — the common case — minted before login/for a different
                # session): re-mint AND Set-Cookie on this 403 so the next
                # request already carries a valid cookie.
                reason = self._stale_cookie_reason(inbound, binding)
                return self._forbid(reason, request, set_cookie_token=token)
            submitted = await _extract_submitted_token(request)
            if not submitted:
                return self._forbid("token missing from request", request)
            if not submitted.isascii():
                # hmac.compare_digest raises TypeError on non-ASCII str
                # operands (D1). A legitimate token is always ascii hex +
                # "." — reject a non-ASCII submission as a plain mismatch
                # rather than letting the TypeError escape as an
                # unhandled 500.
                return self._forbid("token not ascii", request)
            # Constant-time compare of the two full token strings — we
            # already verified the cookie signature, so this is the
            # double-submit equality check. The cookie itself was valid
            # under the current binding (checked above), so NO re-mint here
            # — the double-submit mismatch alone doesn't mean the cookie is
            # stale.
            if not hmac.compare_digest(submitted, inbound):
                return self._forbid("token mismatch", request)

        # 3. Run the route, then attach the cookie if it was freshly minted.
        response = await call_next(request)
        if fresh:
            self._set_csrf_cookie(response, token)
        return response

    def _set_csrf_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=False,  # JS/Jinja must read — double-submit pattern
            samesite="strict",
            secure=self._secure_cookie,
            path="/",
        )

    def _stale_cookie_reason(self, inbound: str, binding: bytes) -> str:
        """Distinguish "token bound to a different session" from other
        mismatches, for the operator-facing log line only (never leaked to
        the caller — see the class docstring's oracle note).

        The only OTHER binding cheaply checkable without a session lookup
        is the fixed anon one: if ``inbound`` verifies under THAT while
        failing under the request's real (non-anon) binding, it's almost
        certainly a pre-login/logout token reused post-login — the common,
        benign "stale tab" case. Anything else (garbage, tampering, or the
        request's own binding already IS anon) falls through to the
        original generic reason.
        """
        if binding != _ANON_BINDING and verify_csrf_token(inbound, self._key, _ANON_BINDING):
            return "token bound to a different session"
        return "cookie invalid"

    def _forbid(
        self,
        reason: str,
        request: Request,
        *,
        set_cookie_token: str | None = None,
    ) -> Response:
        # Body is a fixed opaque string. The specific reason lives only in
        # the server log — distinguishing failure modes client-side hands
        # an attacker a free oracle on which check tripped. Set-Cookie's
        # mere PRESENCE is visible to the caller (rev 3 Sec-N2) — that's
        # fine: it only tells the cookie's own holder "your cookie half was
        # stale," which an attacker can only probe about a cookie they
        # already hold, not use as a cross-session oracle.
        logger.warning(
            "csrf: rejected %s %s (%s)",
            request.method,
            request.url.path,
            reason,
        )
        headers: dict[str, str] = {}
        if request.headers.get("HX-Request") == "true":
            # Every 403 to an HX-Request reloads the whole page rather than
            # swapping a bare "Forbidden" into the DOM — HX-Request cannot
            # be sent cross-site without a CORS preflight, which this app
            # never grants, so this is safe even on the cookie-less
            # cross-site-POST shape (rev 3 Sec-N1).
            headers["HX-Refresh"] = "true"
        # Still opaque about WHICH check failed; for a plain (non-HTMX) form
        # POST — after a deploy or a sign-in in another tab — say what to do.
        response = PlainTextResponse(
            "Forbidden. If you signed in or out in another tab, or the app was just "
            "updated, reload the page and try again.",
            status_code=403,
            headers=headers,
        )
        if set_cookie_token is not None:
            self._set_csrf_cookie(response, set_cookie_token)
        return response
