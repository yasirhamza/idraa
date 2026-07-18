"""E2E (Epic B #326): the per-node distribution selector works on a phone.

Drives the simple scenario form (``/scenarios/new``) in a 390px-wide Chromium
context and asserts:
  - the page does not scroll horizontally (the dist-selector + low/mode/high
    grid stacks via grid-cols-1 sm:grid-cols-3), and
  - selecting "lognormal" on the TEF node's ``tef_dist`` <select> hides the
    ``tef_mode`` input (Alpine x-if removes it from the DOM), while low/high
    remain — the visible affordance the selector exists to provide.

Harness mirrors ``tests/e2e/test_wizard_mobile_e2e.py`` EXACTLY: an ephemeral
per-run SQLite migrated to ``head``, a uvicorn subprocess bound via
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
async def test_scenario_form_dist_selector_mobile(migrated_server_url: str) -> None:
    """At 390px the FAIR-distribution block fits the viewport and switching the
    TEF node to 'lognormal' removes the TEF mode input from the DOM."""
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

        # 2. Open the simple scenario form (un-gated on mobile, tranche 2b).
        await page.goto(f"{base}/scenarios/new")
        await page.wait_for_selector("select[name='tef_dist']")
        assert "Switch device" not in (await page.content()), (
            "scenario form must be un-gated on mobile"
        )

        # 3. No horizontal overflow at 390px (the dist grid stacks to 1 col).
        overflow = await page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert overflow <= 1, (
            f"page scrolls horizontally on a 390px viewport by {overflow}px "
            "— the distribution block does not fit a phone screen"
        )

        # 4. Default is PERT: the TEF mode input is present and visible.
        tef_mode = page.locator("input[name='tef_mode']")
        assert await tef_mode.count() == 1
        assert await tef_mode.is_visible()

        # 5. Switch the TEF node to lognormal — x-if removes the mode input.
        await page.select_option("select[name='tef_dist']", "lognormal")
        await page.wait_for_function(
            "() => document.querySelectorAll(\"input[name='tef_mode']\").length === 0"
        )
        assert await page.locator("input[name='tef_mode']").count() == 0, (
            "tef_mode must be removed from the DOM under lognormal so it never submits"
        )
        # Low / high stay (they are the p5/p95 pair under lognormal).
        assert await page.locator("input[name='tef_low']").count() == 1
        assert await page.locator("input[name='tef_high']").count() == 1

        # 6. Switch back to PERT — the mode input reappears.
        await page.select_option("select[name='tef_dist']", "pert")
        await page.wait_for_function(
            "() => document.querySelectorAll(\"input[name='tef_mode']\").length === 1"
        )
        assert await page.locator("input[name='tef_mode']").count() == 1

        await context.close()
        await browser.close()
