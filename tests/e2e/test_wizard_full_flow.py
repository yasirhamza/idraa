"""E2E: full 6-step wizard with OT/ICS library entry -> Scenario created.

Spec §9.3.

Gap 3 note: the full authenticated wizard flow requires a stable E2E DB
(ephemeral per-run SQLite + CSRF-aware bootstrap). The current infrastructure
uses the shared dev ``idraa.db``, whose state is non-deterministic across
runs.  The ``seed_user_login_e2e`` and ``seed_ot_library_entry_e2e`` fixtures
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


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_wizard_full_flow_creates_scenario(
    e2e_base_url: str,
    seed_user_login_e2e: Any,
    seed_ot_library_entry_e2e: Any,
) -> None:
    """Full 6-step wizard: pick OT/ICS library entry -> scenario created with library_pin.

    Skip condition: ``seed_user_login_e2e`` and ``seed_ot_library_entry_e2e``
    are stub fixtures that call ``pytest.skip`` — this test body is only
    reached once Phase 1.5b replaces the stubs.

    Phase 1.5b implementation notes:
    - seed_user_login_e2e: async callable ``await seed_user_login_e2e(page)``
      that logs the Playwright browser in via GET /setup or POST /login.
    - seed_ot_library_entry_e2e: an object with a ``.name`` attribute that
      matches an entry visible in the wizard step-1 card grid.
    - URL assertion uses ``wait_for_url`` rather than ``to_have_url`` because
      ``to_have_url`` does exact matching; the scenario ID is unknown up front.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(15_000)

        # seed_user_login_e2e is an async callable: await seed_user_login_e2e(page)
        # Phase 1.5b: implement as real login helper.
        await seed_user_login_e2e(page)
        await page.goto(f"{e2e_base_url}/scenarios/new/wizard")

        # Step 1: pick library entry card by name
        entry_name = seed_ot_library_entry_e2e.name
        await page.click(f"text={entry_name}")
        await page.click("button:has-text('Next →')")

        # Step 2: basic info is pre-filled from library; override name for assertion
        await expect(page.locator("input[name='name']")).not_to_be_empty()
        await page.fill("input[name='name']", "E2E full flow")
        await page.click("button:has-text('Next →')")

        # Step 3: Likelihood — pre-filled TEF+Vuln baseline (indexed SME-row
        # shape); advance without edits.
        await expect(page.locator("input[name='tef_low_0']")).not_to_be_empty()
        await page.click("button:has-text('Next →')")

        # Step 4: Impact — pre-filled PL+SL baseline (indexed SME-row shape);
        # advance without edits.
        await expect(page.locator("input[name='pl_low_0']")).not_to_be_empty()
        await page.click("button:has-text('Next →')")

        # Step 5: skip controls
        await page.click("button:has-text('Next →')")

        # Step 6: review — name + library attribution banner must be visible
        await expect(page.locator("text=E2E full flow")).to_be_visible()
        await expect(page.locator("text=Started from library")).to_be_visible()
        await page.click("button:has-text('Save scenario')")

        # Redirected to /scenarios/{id} after finalize
        await page.wait_for_url("**/scenarios/**")
        await expect(page.locator("text=E2E full flow")).to_be_visible()

        await browser.close()
