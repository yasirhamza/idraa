"""ASGI middleware: compute org-wide maintenance-badge count per request.

Stashes the integer count on ``request.state.maintenance_badge_count``
so the navbar template can render the maintenance badge. Runs AFTER
SessionMiddleware so ``request.state.user`` is populated. Anonymous users
or DB errors default to 0 — never breaks page rendering.

(Issue #87, generalization of the previous unconfirmed-only count.
Combines unconfirmed assignments + $0-cost controls per the design doc.)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from idraa.db import get_session

_log = logging.getLogger(__name__)


class MaintenanceBadgeCountMiddleware(BaseHTTPMiddleware):
    """Set ``request.state.maintenance_badge_count`` on every request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.maintenance_badge_count = 0

        user = getattr(request.state, "user", None)
        org_id = getattr(user, "organization_id", None) if user is not None else None
        if user is not None and org_id is not None:
            try:
                from idraa.services.controls_maintenance import (
                    maintenance_badge_count,
                )

                async with get_session() as db:
                    # Phase-1 perf trade (issue #109): fires on every authenticated
                    # request and materializes full rows under the count. Acceptable
                    # at single-org / O(few-dozen-controls) volume per the module
                    # docstring; revisit when scaling past phase-1.
                    count = await maintenance_badge_count(db, org_id=org_id)
                    request.state.maintenance_badge_count = int(count)
            except Exception:
                _log.debug(
                    "MaintenanceBadgeCountMiddleware: count failed; defaulting to 0",
                    exc_info=True,
                )
                request.state.maintenance_badge_count = 0

        return await call_next(request)
