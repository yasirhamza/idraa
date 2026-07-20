"""Design-language Phase 1 acceptance tests (issue #59).

Task 1: logomark macro rendered in the sidebar (authenticated shell) and on
the login page (unauthenticated, ``with_wordmark=True``), plus the favicon
served at ``/static/favicon.svg``. Later tasks in the same epic extend this
module with typography/forms/readout assertions — keep this module the
single home for design-language P1 acceptance tests rather than scattering
one-off test files per task.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_sidebar_renders_logomark(
    authed_analyst: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """The dashboard shell (sidebar) renders the logomark macro's SVG."""
    client, _ = authed_analyst
    r = await client.get("/")
    assert r.status_code == 200
    assert "data-logomark" in r.text
    assert "M3 7 C 11 8, 12 24, 29 26" in r.text


async def test_login_and_favicon(client: AsyncClient) -> None:
    """The login page renders the logomark (with wordmark) + favicon is served."""
    r = await client.get("/login")
    assert r.status_code == 200
    assert "data-logomark" in r.text

    r2 = await client.get("/static/favicon.svg")
    assert r2.status_code == 200
    assert "svg" in r2.text
