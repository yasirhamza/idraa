"""E2E-specific fixtures. Starts the uvicorn server as a subprocess for tests to hit."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def live_server_url() -> Iterator[str]:
    """Start uvicorn in a subprocess on an ephemeral port, yield the URL, tear down."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "idraa.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
    )

    # Wait up to 10s for the server to respond
    deadline = time.time() + 10
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/healthz", timeout=0.5)
            if r.status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    if not ready:
        proc.terminate()
        raise RuntimeError("uvicorn did not come up within 10s")

    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# Convenience alias — plan tests reference e2e_base_url for readability.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_base_url(live_server_url: str) -> str:
    """Alias for ``live_server_url`` used by plan-named fixtures."""
    return live_server_url


# ---------------------------------------------------------------------------
# Deferred seed fixtures — Gap 3 (DB isolation) applies.
#
# The E2E server runs against the dev SQLite (idraa.db).  Unit tests use
# per-test temp DBs.  Seeding the E2E server requires HTTP round-trips against
# the live server, but the dev DB state is non-deterministic between runs
# (may have zero users, or users from a prior manual dev session).  Rather
# than implement fragile state-detection logic in a session-scoped fixture,
# these fixtures are intentionally left as stubs that skip their callers.
#
# A dedicated E2E infrastructure pass (Phase 1.5b) will:
#   1. Launch the E2E server against an ephemeral per-run SQLite file.
#   2. Bootstrap via POST /setup (CSRF-aware httpx session).
#   3. Provide stable seed_user_login_e2e / seed_ot_library_entry_e2e callables.
# ---------------------------------------------------------------------------

_E2E_SEED_SKIP_REASON = (
    "E2E fixtures require dedicated infrastructure pass (ephemeral per-run DB, "
    "CSRF-aware bootstrap); deferred to Phase 1.5b"
)


@pytest.fixture
def seed_user_login_e2e() -> Any:
    """Stub — callers are skip-marked; real implementation deferred to Phase 1.5b.

    Phase 1.5b plan: this fixture will return an async callable
    ``async def login(page) -> None`` that:
      1. GETs /setup (to receive csrf_token cookie) if no users exist, or
         GETs /login if the DB is already bootstrapped.
      2. POSTs credentials with the CSRF double-submit token.
      3. Waits for the session cookie to land on the Playwright context.
    The fixture will require the E2E server to be launched against an
    ephemeral SQLite file (not the shared dev idraa.db) so state is
    deterministic across runs.
    """
    pytest.skip(_E2E_SEED_SKIP_REASON)


@pytest.fixture
def seed_ot_library_entry_e2e() -> Any:
    """Stub — callers are skip-marked; real implementation deferred to Phase 1.5b.

    Phase 1.5b plan: this fixture will return a NamedTuple / SimpleNamespace
    with at least ``name`` and ``id`` populated, seeded via a POST to the
    library admin endpoint (or directly into the ephemeral DB) before the
    wizard step-1 card grid renders.
    """
    pytest.skip(_E2E_SEED_SKIP_REASON)


@pytest.fixture
def seed_library_entries_e2e() -> Any:
    """Stub — callers are skip-marked; real implementation deferred to Phase 1.5b.

    Phase 1.5b plan: this fixture will return a list of seeded library entries,
    populated via POST to the library admin endpoint (or directly into the
    ephemeral DB) before browse filtering tests run. Each entry should have
    at least ``id`` and ``name`` populated, and entries should span multiple
    threat_actor_type values to enable filtering tests.
    """
    pytest.skip(_E2E_SEED_SKIP_REASON)


@pytest.fixture
def seed_library_entry_e2e() -> Any:
    """Stub — callers are skip-marked; real implementation deferred to Phase 1.5b.

    Phase 1.5b plan: this fixture will return a NamedTuple / SimpleNamespace
    with at least ``id`` and ``name`` populated, seeded via a POST to the
    library admin endpoint (or directly into the ephemeral DB) before admin
    override creation tests run.
    """
    pytest.skip(_E2E_SEED_SKIP_REASON)


@pytest.fixture
def seed_admin_login_e2e() -> Any:
    """Stub — callers are skip-marked; real implementation deferred to Phase 1.5b.

    Phase 1.5b plan: this fixture will return an async callable
    ``async def login(page) -> None`` that:
      1. GETs /setup (to receive csrf_token cookie) if no users exist, or
         GETs /login if the DB is already bootstrapped.
      2. POSTs admin credentials with the CSRF double-submit token.
      3. Waits for the session cookie to land on the Playwright context.
    The fixture will require the E2E server to be launched against an
    ephemeral SQLite file (not the shared dev idraa.db) so state is
    deterministic across runs.
    """
    pytest.skip(_E2E_SEED_SKIP_REASON)
