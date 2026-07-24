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


def is_boosted(request: Request) -> bool:
    """True for hx-boost full-page navigations. Boosted requests ALSO send
    HX-Request, so drawer-partial negotiation must be
    `is_htmx_request(request) and not is_boosted(request)` — negotiating on
    HX-Request alone swaps the bare partial into <body> and destroys the
    page chrome (Arch-B1, reproduced live; pre-existing /help/{slug} bug)."""
    return request.headers.get("HX-Boosted") == "true"
