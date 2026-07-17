"""E2E: filter changes in sidebar update card grid via HTMX (no page reload).

Spec §9.4.

Gap 3 note: the full authenticated library browse flow requires a stable E2E DB
(ephemeral per-run SQLite + CSRF-aware bootstrap). The current infrastructure
uses the shared dev ``idraa.db``, whose state is non-deterministic across
runs. The ``seed_user_login_e2e`` and ``seed_library_entries_e2e`` fixtures
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
from playwright.async_api import async_playwright


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_library_filter_updates_grid_via_htmx(
    e2e_base_url: str,
    seed_user_login_e2e: Any,
    seed_library_entries_e2e: Any,
) -> None:
    """Filter changes in sidebar update card grid via HTMX (no page reload).

    Skip condition: ``seed_user_login_e2e`` and ``seed_library_entries_e2e``
    are stub fixtures that call ``pytest.skip`` — this test body is only
    reached once Phase 1.5b replaces the stubs.

    Phase 1.5b implementation notes:
    - seed_user_login_e2e: async callable ``await seed_user_login_e2e(page)``
      that logs the Playwright browser in via GET /setup or POST /login.
    - seed_library_entries_e2e: list of seeded library entries for the test.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(15_000)

        # seed_user_login_e2e is an async callable: await seed_user_login_e2e(page)
        # Phase 1.5b: implement as real login helper.
        await seed_user_login_e2e(page)
        await page.goto(f"{e2e_base_url}/library")
        initial_count = await page.locator(".card").count()
        assert initial_count > 0

        # Check threat_actor=cybercriminals — grid narrows
        await page.check("input[name='threat_actor_type'][value='cybercriminals']")
        await page.wait_for_timeout(500)  # HTMX hx-trigger="change"
        filtered_count = await page.locator(".card").count()
        assert filtered_count <= initial_count

        # Search input narrows further
        await page.fill("input[name='q']", "ransomware")
        await page.wait_for_timeout(500)
        search_count = await page.locator(".card").count()
        assert search_count <= filtered_count

        await browser.close()
