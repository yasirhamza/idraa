"""Regression test: middleware must stay in RequestFraming -> uat_basic_auth -> setup_guard -> SecurityHeaders -> CSRF -> Session -> EnrollmentGuard -> MaintenanceBadgeCount order."""

from __future__ import annotations

from typing import cast

from starlette.middleware.base import BaseHTTPMiddleware

from idraa.app import create_app
from idraa.middleware.csrf import CSRFMiddleware
from idraa.middleware.enrollment_guard import EnrollmentGuardMiddleware
from idraa.middleware.maintenance_count import MaintenanceBadgeCountMiddleware
from idraa.middleware.request_framing import RequestFramingMiddleware
from idraa.middleware.security_headers import SecurityHeadersMiddleware
from idraa.middleware.session import SessionMiddleware


def test_middleware_wire_order() -> None:
    """user_middleware is ordered outermost-first (last add_middleware runs first on request).

    Required order on the wire (Phase 1.5.5, extended by Strong Auth P1 Task 9):
        request  -> RequestFraming -> uat_basic_auth -> setup_guard -> SecurityHeaders ->
                    CSRF -> Session -> EnrollmentGuard -> MaintenanceBadgeCount -> route
        response <- (reverse)

    Eight entries — one per ``add_middleware`` call plus two ``app.middleware("http")``
    decorators (``setup_guard`` and ``uat_basic_auth``). Both function-style
    middlewares are wrapped as ``BaseHTTPMiddleware`` by Starlette's decorator,
    so indices 1 and 2 are both ``BaseHTTPMiddleware``.

    Why RequestFraming is OUTERMOST (index 0, registered last): it refuses
    ambiguous request framing (desync defence in depth) before any other layer
    reads the request, the Basic pre-gate included.

    Why uat_basic_auth is next (index 1, registered after setup_guard):

    - It is the UAT pre-gate: an unauthenticated visitor sees the HTTP Basic
      Auth prompt before any DB roundtrip. Registered AFTER setup_guard via a
      second ``app.middleware("http")`` call, which makes it the outermost
      layer in LIFO ordering.
    - No-ops when ``UAT_BASIC_AUTH_PASSWORD`` is unset (dev, test, local docker).
    - ``/healthz`` is unconditionally exempt so the platform health probe always passes.

    Why setup_guard is third (index 2):

    - It short-circuits unseeded-app requests with a 307 redirect before
      the downstream stack burns DB round trips.

    Why EnrollmentGuard sits between Session and MaintenanceBadgeCount (index 6):

    - It reads ``request.state.user`` set by SessionMiddleware, so it MUST
      run AFTER Session on the request path (i.e., be INNER to Session).
    - LIFO: ``add_middleware(EnrollmentGuardMiddleware)`` is called AFTER
      ``add_middleware(MaintenanceBadgeCountMiddleware)`` but BEFORE
      ``add_middleware(Session)``, so it lands between the two.

    Why MaintenanceBadgeCount is INNERMOST (index 7, registered first):

    - LIFO: ``add_middleware(MaintenanceBadgeCountMiddleware)`` is called BEFORE
      every other ``add_middleware`` call, so it ends up at the innermost
      position (highest index, closest to the route).
    """
    app = create_app()
    # ``Middleware.cls`` is typed as ``_MiddlewareFactory[P]`` (a Protocol); at
    # runtime it is the concrete middleware class we passed to ``add_middleware``.
    # Cast to ``type`` so mypy accepts the identity comparison below.
    classes = [cast(type, m.cls) for m in app.user_middleware]
    # user_middleware is outermost-first: the LAST registration is INDEX 0.
    # RequestFraming is registered last → index 0 (refuses ambiguous framing
    # before anything reads the request); uat_basic_auth → 1; setup_guard → 2. Both appear as BaseHTTPMiddleware (function-style wrapper).
    assert classes == [
        RequestFramingMiddleware,  # desync guard (outermost, pure ASGI)
        BaseHTTPMiddleware,  # uat_basic_auth
        BaseHTTPMiddleware,  # setup_guard
        SecurityHeadersMiddleware,
        CSRFMiddleware,
        SessionMiddleware,
        EnrollmentGuardMiddleware,
        MaintenanceBadgeCountMiddleware,
    ], classes
