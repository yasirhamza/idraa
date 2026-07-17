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

HSTS is intentionally NOT set here. HSTS must only be sent over HTTPS
(OWASP guidance — sending it over plain HTTP is a no-op at best, confusing
at worst) and is the reverse-proxy layer's job (Caddy / nginx in prod).

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

_STATIC_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": CSP_POLICY,
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a fixed set of security headers to every outgoing response.

    Idempotent: if an inner layer already set one of these headers (e.g. a
    route explicitly overrides CSP for a PDF download), the existing value
    wins. This lets routes that legitimately need a different policy (future
    report-embed view) opt out without monkey-patching the middleware.

    Known gap: uncaught-exception (HTTP 500) responses do NOT carry these
    headers. Starlette's ``ServerErrorMiddleware`` wraps ``BaseHTTPMiddleware``
    from the outside, so a crashed request short-circuits to a static
    Starlette error page before ``dispatch`` can attach headers. The 500
    body is a fixed Starlette string (not a user-controlled injection
    surface), so this is informational rather than exploitable. TODO:
    migrate to a pure-ASGI middleware if a future hardening requirement
    (e.g. CSP on error pages for report-injection defense) demands it.
    """

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
        for name, value in _STATIC_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
