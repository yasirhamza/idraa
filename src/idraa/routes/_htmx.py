"""HTMX request detection helper.

HTMX sets the ``HX-Request: true`` header on swaps and form posts. Routes
that branch on htmx-vs-direct-nav check this header. Centralized here so
the comparison string is not duplicated across handlers.
"""

from __future__ import annotations

from fastapi import Request


def is_htmx_request(request: Request) -> bool:
    """Return True iff the request carries ``HX-Request: true``."""
    return request.headers.get("HX-Request") == "true"
