"""E2E regression: tapping a field on the control form must not steal focus
into a sub-function ("subcontrol") combobox button.

Bug (mobile UAT): on ``/controls/new`` the sub-function combobox root carried
``@click.outside="close()"``, and ``close()`` unconditionally returned focus to
its trigger button via ``$nextTick``. Alpine's ``.outside`` fires on EVERY click
outside the element regardless of open state, so tapping any other field (Name,
Coverage, …) made every assignment row's combobox yank focus to its own button.
With 2+ rows the last microtask won, so focus landed on an arbitrary row's
sub-function entry — "focus jumps unpredictably to the subcontrol entry". On a
phone every interaction is a tap, so the theft fired on every field tap.

This drives a 390px Chromium context, adds a second assignment row, taps the
Name field, and asserts focus STAYS on Name (never jumps to a ``role=combobox``
sub-function button).

Harness mirrors ``tests/e2e/test_scenario_form_dist_mobile_e2e.py`` EXACTLY:
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
async def test_controls_form_field_tap_keeps_focus_mobile(migrated_server_url: str) -> None:
    """Tapping the Name field on /controls/new must not move focus into any
    sub-function combobox button, with 2 assignment rows present."""
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
        page.set_default_timeout(15_000)

        # 1. Bootstrap first admin + login.
        await _bootstrap_admin_and_login(page, base)

        # 2. Open the control form (un-gated on mobile, tranche 2b).
        await page.goto(f"{base}/controls/new")
        await page.wait_for_selector("input[name='name']")

        # 3. Add a second assignment row so there are 2 combobox instances —
        #    this is what makes the focus theft land "unpredictably".
        await page.click("button:has-text('Add assignment')")
        await page.wait_for_function(
            "() => document.querySelectorAll('#assignments .assignment-row').length === 2"
        )

        # 4. Tap the Name field as a user would on a phone, then let any
        #    queued $nextTick focus handlers run.
        await page.click("input[name='name']")
        await page.fill("input[name='name']", "Endpoint EDR")
        await page.wait_for_timeout(200)

        # 5. Focus must still be on Name — never stolen into a sub-function
        #    combobox button (role="combobox") on any row.
        active = await page.evaluate(
            "() => { const a = document.activeElement; return {"
            " name: a && a.getAttribute('name'),"
            " role: a && a.getAttribute('role'),"
            " id: a && a.id }; }"
        )
        assert active["role"] != "combobox", (
            f"focus was stolen into a sub-function combobox button ({active['id']}) "
            "after tapping the Name field"
        )
        assert active["name"] == "name", (
            f"focus left the Name field after a tap; landed on {active!r}"
        )

        # 6. Conversely, the legitimate ARIA refocus must survive: opening a
        #    combobox and dismissing it with Escape returns focus to ITS OWN
        #    trigger button (close({refocus:true})). Guards against an
        #    over-correction that strips the refocus entirely.
        await page.click("#sf-input-0")  # toggle() -> openPanel(); focus -> search
        await page.wait_for_function(
            "() => document.activeElement === document.querySelector("
            "\"#assignments .assignment-row[data-row-index='0'] "
            "input[aria-label='Filter sub-functions']\")"
        )
        await page.keyboard.press("Escape")
        await page.wait_for_function(
            "() => document.activeElement === document.getElementById('sf-input-0')"
        )

        await context.close()
        await browser.close()
