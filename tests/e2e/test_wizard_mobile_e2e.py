"""E2E (mobile tranche 2d): the scenario wizard is usable on a phone viewport.

De-risks the SME-estimate card-stack restructure in
``scenarios/wizard/_fair_params_form_inner.html`` — the most UAT-scarred
template in the app — by driving the real wizard in a 390px-wide Chromium
context through to the Likelihood step (step 3, the SME estimate grid) and
asserting:
  - the page does not scroll horizontally (the card-stack actually fits), and
  - the per-row controls (SME combobox, Low/High inputs) and their mobile
    labels render and are interactive (Alpine wired up the x-for rows).

Harness mirrors ``tests/e2e/test_scenario_import_export_e2e.py`` EXACTLY: an
ephemeral per-run SQLite migrated to ``head``, a uvicorn subprocess bound via
``DATABASE_URL``, and a ``try/except PlaywrightError -> pytest.skip`` guard so
this SKIPS cleanly where Chromium is not installed.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator

import httpx
import pytest

from tests.e2e.conftest import E2E_TIMEOUT_MS


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server_url() -> Iterator[str]:
    """Ephemeral SQLite migrated to head + uvicorn bound to it via DATABASE_URL."""
    db_path = tempfile.mktemp(suffix=".db", prefix="rf_e2e_")  # noqa: S306 — test-local ephemeral DB
    db_url = f"sqlite+aiosqlite:///{db_path}"
    env = {**os.environ, "DATABASE_URL": db_url}

    mig = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert mig.returncode == 0, f"alembic upgrade head failed:\n{mig.stdout}\n{mig.stderr}"

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
        env=env,
    )

    deadline = time.time() + 15
    ready = False
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=0.5).status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    if not ready:
        proc.terminate()
        raise RuntimeError("uvicorn did not come up within 15s")

    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        with contextlib.suppress(OSError):
            os.unlink(db_path)


_ADMIN_EMAIL = "admin@e2e.local"
_ADMIN_PASSWORD = "E2e-passw0rd!"  # test-local credential


async def _bootstrap_admin_and_login(page, base: str) -> None:
    """Authenticate the browser, bootstrapping the first admin if needed."""
    await page.goto(f"{base}/setup")
    has_setup_form = await page.locator("input[name='org_name']").count() > 0
    if not has_setup_form:
        await page.goto(f"{base}/login")
        await page.fill("input[name='email']", _ADMIN_EMAIL)
        await page.fill("input[name='password']", _ADMIN_PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(f"{base}/")
        return

    await page.fill("input[name='org_name']", "E2E Org")
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


# Phone viewport — iPhone 12/13/14 logical width.
_MOBILE_VIEWPORT = {"width": 390, "height": 844}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_wizard_sme_step_fits_mobile_viewport(migrated_server_url: str) -> None:
    """Drive the wizard to the Likelihood (SME-estimate) step on a 390px phone
    viewport; assert no horizontal overflow and that the card-stacked row
    controls + their mobile labels render and are interactive."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:  # browser binary not installed
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context(viewport=_MOBILE_VIEWPORT)
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        # 1. Bootstrap first admin + login.
        await _bootstrap_admin_and_login(page, base)

        # 2. Enter the wizard; step 1 must render (un-gated) — no "Switch device".
        await page.goto(f"{base}/scenarios/new/wizard/step/1")
        await page.wait_for_selector("button[name='skip_library']")
        assert "Switch device" not in (await page.content()), (
            "wizard step 1 must be un-gated on mobile"
        )

        # 3. Skip the library -> step 2.
        await page.click("button[name='skip_library']")
        await page.wait_for_selector("input[name='name']")

        # 4. Fill the required basics and advance -> step 3 (Likelihood / SME grid).
        await page.fill("input[name='name']", "E2E Mobile Wizard Scenario")
        await page.select_option("select[name='threat_category']", index=1)
        await page.click("button[type='submit']:has-text('Next')")

        # Step 3 renders the SME estimate grid. Wait for the "+ Add SME estimate"
        # control (present once the Alpine x-for rows mount).
        await page.wait_for_selector("button:has-text('Add SME estimate')")

        # 5. No horizontal overflow at 390px (the whole point of the card-stack).
        overflow = await page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert overflow <= 1, (
            f"page scrolls horizontally on a 390px viewport by {overflow}px "
            "— the wizard does not fit a phone screen"
        )

        # 6. The card-stacked row controls render: at least one SME combobox
        #    input, and a per-cell mobile label is actually visible (the md:hidden
        #    label, NOT the hidden-on-mobile md:grid desktop column header).
        assert await page.locator("input[placeholder*='SME name']").count() >= 1
        visible_low_labels = await page.get_by_text("Low (5%)", exact=True).evaluate_all(
            "els => els.filter(e => e.offsetParent !== null).length"
        )
        assert visible_low_labels >= 1, "a mobile 'Low (5%)' field label should be visible"

        # 7. The Low/High inputs are interactive (Alpine x-model wired up).
        first_low = page.locator("input[name='tef_low_0']")
        await first_low.fill("0.2")
        assert await first_low.input_value() == "0.2"

        await context.close()
        await browser.close()
