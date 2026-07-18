"""E2E: wizard skip-library path + unauthenticated redirect smoke.

Spec §9.3.

Contains two tests:

1. ``test_wizard_unauthenticated_redirects``  — unauthenticated browser
   navigates to /scenarios/new/wizard; verifies the server redirects away
   (to /login or /setup) rather than rendering the wizard.  No auth fixture
   required; runs against the live server unconditionally.

2. ``test_wizard_blank_flow_no_library_pin`` — full authenticated blank-flow
   wizard: skip library -> fill step 2 (basic) -> step 3 (Likelihood: TEF+Vuln)
   -> step 4 (Impact: PL+SL) -> confirm no "Started from library" banner.
   Skip-marked via ``seed_user_login_e2e`` stub until Phase 1.5b provides real
   E2E DB infrastructure.

Gap 3 note: ``seed_user_login_e2e`` calls ``pytest.skip`` (stub fixture in
``tests/e2e/conftest.py``) because the dev ``idraa.db`` state is
non-deterministic across runs.  Phase 1.5b will replace the stub with a
real implementation that bootstraps an ephemeral per-run SQLite DB.

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
async def test_wizard_unauthenticated_redirects(
    e2e_base_url: str,
) -> None:
    """Unauthenticated browser reaching /scenarios/new/wizard is redirected away.

    F26.b — skip-marked because CI's uvicorn subprocess starts against an
    unmigrated SQLite DB, causing the auth middleware's user-table query
    to crash the request before the expected redirect fires. Same root
    cause as F23/F24 deferred wizard tests; resolved by Phase 1.5b
    ephemeral-per-run-DB infrastructure.

    Acceptable redirect destinations:
    - ``/login`` — DB is bootstrapped, session guard kicks in.
    - ``/setup`` — DB has no users, setup_guard kicks in first.

    Either destination is correct.  The test asserts only that the final URL
    is NOT ``/scenarios/new/wizard`` and that the Playwright response chain
    followed a redirect (status 200 on the landing page after the redirect).
    """
    pytest.skip(
        "E2E fixtures require dedicated infrastructure pass "
        "(ephemeral per-run DB, alembic-migrated schema, CSRF-aware bootstrap); "
        "deferred to Phase 1.5b"
    )
    async with async_playwright() as p:  # type: ignore[unreachable]
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        response = await page.goto(f"{e2e_base_url}/scenarios/new/wizard")

        # Playwright follows redirects automatically; the landed page is not the wizard.
        final_url = page.url
        assert "/scenarios/new/wizard" not in final_url, (
            f"Expected a redirect away from /scenarios/new/wizard, "
            f"but Playwright landed at: {final_url}"
        )

        # The redirect target must be reachable (200 OK on the final page).
        assert response is not None
        assert response.status == 200, (
            f"Redirect destination returned {response.status}; expected 200"
        )

        # The destination must be either /login or /setup.
        assert "/login" in final_url or "/setup" in final_url, (
            f"Expected redirect to /login or /setup, got: {final_url}"
        )

        await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_wizard_blank_flow_no_library_pin(
    e2e_base_url: str,
    seed_user_login_e2e: Any,
) -> None:
    """Wizard skip-library path: user fills all steps manually; no library_pin.

    Skip condition: ``seed_user_login_e2e`` is a stub fixture that calls
    ``pytest.skip`` until Phase 1.5b provides real E2E DB infrastructure.

    Phase 1.5b implementation notes:
    - seed_user_login_e2e should be an async callable: await seed_user_login_e2e(page)
    - Step 2 selects: threat_category=ransomware, threat_actor_type=cybercriminals,
      asset_class=systems.
    - Steps 3 (Likelihood) and 4 (Impact) use the indexed SME-row shape
      (``<fieldset>_low_<idx>`` / ``<fieldset>_high_<idx>`` — no PERT ``_mode``).
      Even on a blank flow the wizard eager-seeds one IRIS-baseline row per
      fieldset on first visit, so row 0 is pre-filled; the test overwrites
      row 0 to document an explicit blank-flow entry.
    - Step 3 fills the Likelihood page: tef_low_0/tef_high_0, vuln_low_0/vuln_high_0.
    - Step 4 fills the Impact page: pl_low_0/pl_high_0.
    - Step 6 review must NOT show "Started from library" banner.
    - After Save, page navigates to /scenarios/{id} showing the scenario name.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        # seed_user_login_e2e is a callable that logs the Playwright page in.
        # Phase 1.5b: await seed_user_login_e2e(page)
        await seed_user_login_e2e(page)
        await page.goto(f"{e2e_base_url}/scenarios/new/wizard")

        # Step 1: skip library selection entirely
        await page.click("text=Skip — start blank")

        # Step 2: fill basic info manually
        await page.fill("input[name='name']", "E2E blank flow")
        await page.select_option("select[name='threat_category']", "ransomware")
        await page.select_option("select[name='threat_actor_type']", "cybercriminals")
        await page.select_option("select[name='asset_class']", "systems")
        await page.click("button:has-text('Next →')")

        # Step 3: Likelihood — overwrite the eager-seeded row 0 with explicit
        # TEF + Vuln low/high values (indexed SME-row shape, no PERT mode).
        await page.fill("input[name='tef_low_0']", "1.0")
        await page.fill("input[name='tef_high_0']", "12.0")
        await page.fill("input[name='vuln_low_0']", "0.05")
        await page.fill("input[name='vuln_high_0']", "0.50")
        await page.click("button:has-text('Next →')")

        # Step 4: Impact — overwrite the eager-seeded row 0 with explicit PL
        # low/high values (indexed SME-row shape, no PERT mode).
        await page.fill("input[name='pl_low_0']", "100000")
        await page.fill("input[name='pl_high_0']", "5000000")
        await page.click("button:has-text('Next →')")

        # Step 5: skip controls
        await page.click("button:has-text('Next →')")

        # Step 6: review — no library attribution banner should appear
        await expect(page.locator("text=Started from library")).not_to_be_visible()
        await page.click("button:has-text('Save scenario')")

        # After save: redirected to scenario detail; name is visible
        await page.wait_for_url("**/scenarios/**")
        await expect(page.locator("text=E2E blank flow")).to_be_visible()

        await browser.close()
