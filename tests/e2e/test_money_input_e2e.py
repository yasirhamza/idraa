"""E2E — Excel-like money entry on the wizard's PL/SL fields (owner UAT 2026-07-31).

The old UX stripped commas on focus (a value swap that discarded the click
position and threw the caret to the end — "hostile to leftmost digit
changes") and rejected pastes carrying currency symbols or spaces. The new
behavior (static/js/money_input.js + the wizard partial's handlers):

- commas regroup live on every keystroke, caret anchored to the digit being
  edited (leftmost included);
- no focus-time value mutation at all;
- pastes sanitize ("$1 000 000" -> "1,000,000") instead of failing the
  pattern;
- blur still commits the canonical 2-decimal display.

Self-bootstraps via /setup (convention copied from test_loss_readout_e2e);
drives the wizard blank flow to step 4 where the eager-seeded PL row exists.
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
from playwright.async_api import Page, async_playwright, expect

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

E2E_TIMEOUT_MS = 15_000

_ADMIN_EMAIL = "admin@e2e-money-input.local"
_ADMIN_PASSWORD = "E2e-passw0rd!"  # test-local credential


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server_url() -> Iterator[str]:
    """Ephemeral SQLite migrated to head + uvicorn bound to it via DATABASE_URL.

    Own-server convention copied from test_loss_readout_e2e — the session
    e2e_base_url fixture serves the AMBIENT (unmigrated) DB and its /setup is
    claimed by whichever module bootstraps first.
    """
    db_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="rf_e2e_money_input_")
    os.close(db_fd)
    db_url = f"sqlite+aiosqlite:///{db_path}"
    env = {**os.environ, "DATABASE_URL": db_url, "AUTH_MFA_POLICY": "optional"}

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


async def _bootstrap_admin_and_login(page: Page, base: str) -> None:
    """Copied convention from tests/e2e/test_loss_readout_e2e.py."""
    await page.goto(f"{base}/setup")
    has_setup_form = await page.locator("input[name='org_name']").count() > 0
    if not has_setup_form:
        await page.goto(f"{base}/login")
        await page.fill("input[name='email']", _ADMIN_EMAIL)
        await page.fill("input[name='password']", _ADMIN_PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(f"{base}/")
        return

    await page.fill("input[name='org_name']", "E2E Money Input Org")
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Money Input Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


async def _goto_wizard_step_4(page: Page, base: str) -> None:
    """Blank flow: skip library -> step 2 basics -> step 3 (eager-seeded) ->
    step 4 Impact, where pl_low_0 exists pre-filled."""
    await page.goto(f"{base}/scenarios/new/wizard")
    await page.click("text=Skip — start blank")
    await page.fill("input[name='name']", "E2E money entry")
    await page.select_option("select[name='threat_category']", "ransomware")
    await page.select_option("select[name='threat_actor_type']", "cybercriminals")
    await page.select_option("select[name='asset_class']", "systems")
    await page.click("button:has-text('Next →')")
    await expect(page.locator("input[name='tef_low_0']")).not_to_be_empty()
    await page.click("button:has-text('Next →')")
    await expect(page.locator("input[name='pl_low_0']")).not_to_be_empty()


async def test_money_entry_is_excel_like(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        await _goto_wizard_step_4(page, base)

        field = page.locator("input[name='pl_low_0']")

        # 1. Commas regroup LIVE while typing (not only after blur).
        await field.click()
        await field.press("ControlOrMeta+a")
        await page.keyboard.type("1234567")
        assert await field.input_value() == "1,234,567"

        # 2. Leftmost-digit edit: caret to 0 -> type a digit. Pre-fix, the
        #    focus/reformat dance threw the caret; now the digit lands at the
        #    FRONT and the caret sits right after it. (Caret set via
        #    setSelectionRange: the Home key does not move a text caret in
        #    macOS Chromium, and the assertion is about reformat-time caret
        #    preservation, not key bindings.)
        await field.evaluate("el => el.setSelectionRange(0, 0)")
        await page.keyboard.type("9")
        assert await field.input_value() == "91,234,567"
        caret = await field.evaluate("el => el.selectionStart")
        assert caret == 1, f"caret jumped to {caret}, expected to stay at 1"

        # 3. Mid-number edit across a comma boundary: caret stays anchored to
        #    its digit while the grouping shifts around it.
        await field.evaluate("el => el.setSelectionRange(0, 0)")
        await field.press("Delete")  # drop the leading 9 -> 1,234,567
        assert await field.input_value() == "1,234,567"
        caret = await field.evaluate("el => el.selectionStart")
        assert caret == 0

        # 4. Paste tolerance: symbols/spaces sanitize instead of rejecting.
        await field.evaluate(
            "el => { el.value = '$1 000 000'; "
            "el.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        assert await field.input_value() == "1,000,000"

        # 5. Blur commits the canonical 2-decimal display; HTML pattern
        #    validation passes (validity.valid) so submit is not blocked.
        await field.press("Tab")
        assert await field.input_value() == "1,000,000.00"
        assert await field.evaluate("el => el.validity.valid") is True

        # 6. No focus-time value swap: re-focusing leaves the text untouched.
        await field.click()
        assert await field.input_value() == "1,000,000.00"

        # 7. Magnitude-suffixed paste must fail LOUDLY, never launder
        #    (review B1: a blanket sanitize turned "$1.5M" into 1.5 — a
        #    silent 10^6 understatement). The raw text stays, the pattern
        #    marks it invalid, and blur blanks it (strict Number()).
        await field.evaluate(
            "el => { el.value = '$1.5M'; el.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        assert await field.input_value() == "$1.5M"  # left exactly as pasted
        assert await field.evaluate("el => el.validity.valid") is False
        await field.press("Tab")
        assert await field.input_value() == ""  # loud blank, required blocks

        # 8. Backspace over a group separator eats the digit to its left
        #    (review I1: regrouping resurrected the comma, so it was a
        #    visible no-op and the SECOND press ate the wrong digit).
        await field.click()
        await field.press("ControlOrMeta+a")
        await page.keyboard.type("1000")
        assert await field.input_value() == "1,000"
        await field.evaluate("el => el.setSelectionRange(2, 2)")  # after ","
        await field.press("Backspace")
        assert await field.input_value() == "000"  # the "1" died with its comma
        caret = await field.evaluate("el => el.selectionStart")
        assert caret == 0

        # 9. THROUGH-A-BLUR shapes (round-2 blocker: fmt() writes el.value via
        #    x-model with no input event, so the eat's prev-tracking desynced
        #    after every blur — both symptoms below reproduced pre-fix).
        #    9a. Selection-delete of the cents must NOT eat extra digits:
        #        "1,000.00" minus selected ".00" is "1,000", never "100".
        await field.press("ControlOrMeta+a")
        await page.keyboard.type("1000")
        await field.press("Tab")
        assert await field.input_value() == "1,000.00"
        await field.click()
        await field.evaluate("el => el.setSelectionRange(5, 8)")  # select ".00"
        await field.press("Backspace")
        assert await field.input_value() == "1,000"
        #    9b. The separator-eat still works on the FIRST delete after a
        #        blur (pre-fix it silently no-opped once per focus cycle).
        await field.press("Tab")
        assert await field.input_value() == "1,000.00"
        await field.click()
        await field.evaluate("el => el.setSelectionRange(2, 2)")  # after ","
        await field.press("Backspace")
        assert await field.input_value() == "000.00"  # digit died with comma

        # 10. European dot-grouped paste must fail LOUDLY, never launder
        #     (round-2 important: the old first-dot collapse turned
        #     "€1.500.000,00" into 1.50 — same 10^6 class as B1).
        await field.evaluate(
            "el => { el.value = '€1.500.000,00'; "
            "el.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        assert await field.input_value() == "€1.500.000,00"  # left as pasted
        assert await field.evaluate("el => el.validity.valid") is False
        await field.press("Tab")
        assert await field.input_value() == ""  # strict Number() -> loud blank

        await context.close()
        await browser.close()
