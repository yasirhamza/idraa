"""Blocking MFA-enrollment interstitial.

When AUTH_MFA_POLICY == "required" and the logged-in user has no strong factor
(mfa_enrolled_at is None), redirect every non-allowlisted request to
/account/security. Runs INNER to SessionMiddleware so request.state.user is
already populated; reads it with zero DB access (Session pinned the loaded
User, expire_on_commit=False).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from idraa.config import get_settings

_ALLOWLIST = (
    "/account/security",
    "/auth/step-up",
    "/login",
    "/logout",
    "/setup",
    "/healthz",
    "/static",
)


def _allowed(path: str) -> bool:
    # Segment-aware: "/static" or "/static/..." match, but "/staticfoo" does NOT
    # (don't regress the repo's anti-prefix-abuse convention).
    return any(path == p or path.startswith(p + "/") for p in _ALLOWLIST)


class EnrollmentGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if get_settings().auth_mfa_policy != "required":
            return await call_next(request)
        user = getattr(request.state, "user", None)
        if (
            user is not None
            and getattr(user, "mfa_enrolled_at", None) is None
            and not _allowed(request.url.path)
        ):
            if request.headers.get("HX-Request") == "true":
                # Tell HTMX to redirect the whole page, not swap a fragment.
                return Response(status_code=204, headers={"HX-Redirect": "/account/security"})
            return RedirectResponse("/account/security", status_code=303)
        return await call_next(request)
