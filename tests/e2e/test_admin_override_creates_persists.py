"""E2E: admin creates override on library entry, navigates back, override visible.

Spec §9.4.

Gap 3 note: the full authenticated admin override creation flow requires
a stable E2E DB (ephemeral per-run SQLite + CSRF-aware bootstrap). The current
infrastructure uses the shared dev ``idraa.db``, whose state is non-deterministic
across runs. The ``seed_admin_login_e2e`` and ``seed_library_entry_e2e`` fixtures
are stub-skips; callers below are skip-marked at fixture resolution time.

A dedicated E2E infrastructure pass (Phase 1.5b) will replace the stubs with
real implementations and remove the skip markers.

Implementation note on Playwright fixture pattern:
    This module uses ``async_playwright()`` directly (matching the precedent
    in ``test_healthz_e2e.py``) rather than the ``page`` fixture provided by
    ``pytest-playwright``.  The ``page`` fixture is sync-wrapped and conflicts
    with ``pytest-asyncio``'s event loop management when ``asyncio_mode="auto"``
    is set globally (RuntimeError: Runner.run() cannot be called from a running
    event loop).  Direct ``async_playwright()`` usage is the correct pattern
    for this project.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import async_playwright, expect

from tests.e2e.conftest import E2E_TIMEOUT_MS


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_admin_override_creation_and_persistence(
    e2e_base_url: str,
    seed_admin_login_e2e: Any,
    seed_library_entry_e2e: Any,
) -> None:
    """Admin creates override on library entry, navigates back, override visible.

    Skip condition: ``seed_admin_login_e2e`` and ``seed_library_entry_e2e``
    are stub fixtures that call ``pytest.skip`` — this test body is only
    reached once Phase 1.5b replaces the stubs.

    Phase 1.5b implementation notes:
    - seed_admin_login_e2e: async callable ``await seed_admin_login_e2e(page)``
      that logs the Playwright browser in as an admin user via GET /setup or
      POST /login.
    - seed_library_entry_e2e: an object with an ``id`` attribute corresponding
      to a library entry visible in the entry detail page.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        # seed_admin_login_e2e is an async callable: await seed_admin_login_e2e(page)
        # Phase 1.5b: implement as real admin login helper.
        await seed_admin_login_e2e(page)
        await page.goto(f"{e2e_base_url}/library/entries/{seed_library_entry_e2e.id}")

        await page.click("text=Create org override")
        await page.fill("input[name='tef_low']", "2.0")
        await page.fill("input[name='tef_mode']", "6.0")
        await page.fill("input[name='tef_high']", "18.0")
        await page.fill("textarea[name='reason']", "E2E test override")
        await page.click("button:has-text('Create')")

        await expect(page.locator("text=Override created")).to_be_visible()
        await page.goto(f"{e2e_base_url}/library/overrides/")
        await expect(page.locator("text=E2E test override")).to_be_visible()

        await browser.close()
