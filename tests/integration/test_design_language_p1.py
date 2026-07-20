"""Design-language Phase 1 acceptance tests (issue #59).

Task 1: logomark macro rendered in the sidebar (authenticated shell) and on
the login page (unauthenticated, ``with_wordmark=True``), plus the favicon
served at ``/static/favicon.svg``.

Task 2: breadcrumb-as-eyebrow macro classes (mono/uppercase/tracked, leading
hairline rule, brand-colored current page) and the body-gradient token in
``app.css``. Later tasks in the same epic extend this module with
forms/readout assertions — keep this module the single home for
design-language P1 acceptance tests rather than scattering one-off test
files per task.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

APP_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "idraa" / "static" / "css" / "app.css"


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


async def test_breadcrumb_is_eyebrow(
    authed_analyst: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """The breadcrumb macro renders as the deck's mono/uppercase/tracked
    "eyebrow" — a leading hairline rule span, and the current page in the
    brand color — via macro classes, not a `header nav[aria-label]` element
    selector (that approach is deleted from app.css in this task)."""
    client, _ = authed_analyst
    r = await client.get("/scenarios")
    assert r.status_code == 200
    assert "uppercase" in r.text
    assert "tracking-[0.14em]" in r.text
    assert "text-brand" in r.text


async def test_body_gradient_token() -> None:
    """app.css ports the preview's ambient brand-glow gradient behind the
    app shell, expressed entirely through the --color-brand token."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    assert "radial-gradient" in css
    assert "var(--color-brand)" in css
