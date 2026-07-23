"""Security response headers — app-layer hardening.

Emits defense-in-depth HTTP headers on every response:

- ``X-Content-Type-Options: nosniff`` — kills MIME sniffing attacks.
- ``X-Frame-Options: DENY`` — legacy clickjacking defense (still honoured by
  some user agents; duplicated by CSP ``frame-ancestors 'none'`` below).
- ``Referrer-Policy: strict-origin-when-cross-origin`` — leaks the origin
  only, never the full path/query, on cross-origin nav.
- ``Content-Security-Policy`` — scopes script/style/image/etc sources to
  ``'self'`` only: every front-end asset (HTMX, Alpine, Tailwind, DaisyUI) is
  self-hosted, so no external origin is granted anywhere in the policy.
  Violations fail-closed at the browser.

- ``Permissions-Policy`` — conservative deny-all for a handful of sensitive
  browser features this app never uses (geolocation, camera, mic, payment,
  USB).

HSTS (``Strict-Transport-Security``) is app-layer and prod-gated at
construction time (``enable_hsts=True`` when ``settings.environment ==
"prod"``), NOT keyed on ``request.url.scheme`` — Fly terminates TLS at the
edge, so the app never observes ``https://`` on the request itself, and
dev/test both run plain http:// where sending HSTS would be a confusing
no-op (OWASP guidance). ``includeSubDomains`` covers deployments that also
serve a ``www.`` subdomain of the same apex.

Error responses (uncaught-exception 500s) carry the same header set: see
``security_header_map()`` below, applied by the base-``Exception`` handler
registered in ``idraa.app.create_app`` (Starlette's ``ServerErrorMiddleware``
sits outside ``BaseHTTPMiddleware``, so ``dispatch`` alone never sees a
crashed request — the exception handler is what re-applies headers there).

CSP compromises (documented so the reviewer does not have to re-derive):

- NO external origin is granted in any directive: Tailwind + DaisyUI CSS
  (``/static/css/tailwind.css``, ``/static/vendor/daisyui-*.min.css``), and —
  as of the unpkg vendoring — HTMX + Alpine
  (``/static/vendor/htmx-*.min.js``, ``/static/vendor/alpinejs-*.min.js``)
  are all served same-origin. ``https://unpkg.com`` is GONE from
  ``script-src``; the app is fully air-gap-capable.
- ``'unsafe-eval'`` on ``script-src`` is RETAINED: it is required by
  Alpine.js's expression evaluator. The standard Alpine build (vendored
  ``alpinejs-3.14.1.min.js``, used across many templates) compiles every
  ``x-data``/``@click``/``x-model`` expression via ``new AsyncFunction``,
  which CSP blocks without ``'unsafe-eval'``. Fast-follow (#487): drop this
  once migrated to the CSP-safe ``@alpinejs/csp`` build.
- ``'unsafe-inline'`` on ``script-src`` remains for small inline ``<script>``
  blocks in ``base.html`` (pre-paint theme bootstrap, HTMX event wiring).
- ``'unsafe-inline'`` on ``style-src`` covers dynamic styles DaisyUI and
  Tailwind inject at runtime.
- ``data:`` on ``img-src`` covers DaisyUI/Tailwind form-control icon
  backgrounds — the compiled CSS embeds these as inline ``data:image/svg``
  backgrounds, so this grant cannot be dropped without breaking DaisyUI form
  controls (epic #547 P3 verified this before removing the last CDN-era
  justification for the grant).
- ``data:`` on ``font-src`` covers inlined font payloads (DaisyUI icons).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=(), payment=(), usb=()"

# HSTS max-age is 1 year (31536000s), the widely-recommended floor for
# preload-eligible policies. includeSubDomains covers a future www.<apex>
# subdomain of the same deployment; the RP-ID/origin itself stays
# config-driven elsewhere (no domain is hardcoded here).
HSTS_VALUE = "max-age=31536000; includeSubDomains; preload"

_STATIC_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": CSP_POLICY,
    "Permissions-Policy": PERMISSIONS_POLICY,
}


def security_header_map(enable_hsts: bool) -> dict[str, str]:
    """Build the header set this app emits, optionally including HSTS.

    Shared by :class:`SecurityHeadersMiddleware` (normal responses) and the
    base-``Exception`` (500) handler registered in ``idraa.app.create_app``,
    so both paths emit byte-identical headers.
    """
    if enable_hsts:
        return {**_STATIC_HEADERS, "Strict-Transport-Security": HSTS_VALUE}
    return dict(_STATIC_HEADERS)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a fixed set of security headers to every outgoing response.

    Idempotent: if an inner layer already set one of these headers (e.g. a
    route explicitly overrides CSP for a PDF download), the existing value
    wins. This lets routes that legitimately need a different policy (future
    report-embed view) opt out without monkey-patching the middleware.

    ``enable_hsts`` is resolved ONCE at construction (``idraa.app`` passes
    ``settings.environment == "prod"``) and the resulting header map is
    precomputed — dispatch never re-reads settings or the request scheme.
    """

    def __init__(self, app: ASGIApp, *, enable_hsts: bool = False) -> None:
        super().__init__(app)
        self._headers = security_header_map(enable_hsts)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Layer defense-in-depth headers onto the downstream response.

        Uses ``setdefault`` so a downstream route can override these
        (Starlette header lookup is case-insensitive) for legitimate edge
        cases like a PDF-embed endpoint; the CSP-value assertion in
        ``tests/integration/test_security_headers.py`` prevents a silent
        override from shipping by accident.
        """
        response = await call_next(request)
        for name, value in self._headers.items():
            response.headers.setdefault(name, value)
        return response
