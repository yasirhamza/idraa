"""Unit test: setup_guard's segment-aware allowlist rejects prefix-abuse URLs.

The related test ``tests/unit/test_app_middleware_order.py`` covers the
middleware STACK wiring; this file covers the guard's PATH-MATCHING logic.
Split out for clarity: wire order and allowlist shape are two different
invariants, and "setup_guard allows /setupXYZ" is a separate review signal
from "SessionMiddleware is inside CSRFMiddleware".

Background: the original guard used ``path.startswith("/setup")`` which
silently allowed ``/setupXYZ`` / ``/loginAttack`` — a path an attacker
could craft to bypass the un-seeded redirect. 1.1.5.a FIX 3 replaced that
with the (``_ALLOW_EXACT``, ``_ALLOW_DIR_PREFIXES``) pair. This test pins
the pair so a future "just inline it back" refactor fails loudly.
"""

from __future__ import annotations

import pytest

from idraa.app import _path_allowed


@pytest.mark.parametrize(
    "path",
    [
        "/setup",
        "/healthz",
        "/login",
        "/setup/anything",
        "/static/css/app.css",
        "/login/sub-path",
    ],
)
def test_allowed_paths(path: str) -> None:
    assert _path_allowed(path), f"{path!r} should be allowlisted"


@pytest.mark.parametrize(
    "path",
    [
        "/setupXYZ",
        "/loginAttack",
        "/healthzcheck",
        "/setup.json",
        "/login-as-admin",
        "/api/docs",  # /api dropped from allowlist in 1.1.5.a FIX 2
        "/api/openapi.json",
        "/no-such-route",
        "/",
    ],
)
def test_rejected_paths(path: str) -> None:
    assert not _path_allowed(path), f"{path!r} should NOT be allowlisted"
