"""E2E (deterministic): browse -> filter ot_integrity -> open new entry detail,
plus the scenario wizard offers ``ot_integrity`` as a selectable threat category.

This is the user-simulated Playwright test for the Scenario Library Content
Extension (44-entry library). Unlike the older ``test_library_browse_filtering.py``
skip-stub (which depended on the shared non-deterministic dev ``idraa.db`` and a
deferred "Phase 1.5b" auth/DB infra), this module builds the minimal deterministic
E2E infrastructure inline so the test actually RUNS:

    1. An ephemeral per-run SQLite file is migrated to ``head`` via
       ``alembic upgrade head`` in a subprocess — this runs the REAL migration
       chain, including the CHECK-widening migration and the additive
       insert-if-absent seed migration, so all 44 library entries (incl. the 13
       extension entries and the 3 ``ot_integrity`` ones) are seeded, AND the
       control-library catalog is seeded (so the P2c "Recommended controls"
       panel resolves).
    2. uvicorn is launched bound to that ephemeral DB via ``DATABASE_URL`` env.
    3. The Playwright browser bootstraps the first admin via GET /setup -> POST
       /setup (creates org + admin, sets the ``idraa_session`` cookie, 303 -> /),
       then exercises the real browse / filter / detail / wizard surfaces.

The seeded DB is the load-bearing difference from ``live_server_url`` (which
sets no ``DATABASE_URL`` and therefore uses ``create_all`` against the shared
dev DB — ``create_all`` does NOT run the seed migrations, so it would carry zero
library entries).

Implementation note on the Playwright fixture pattern:
    This module uses ``async_playwright()`` directly (matching the precedent in
    ``test_healthz_e2e.py`` / ``test_library_browse_filtering.py``) rather than
    the sync ``page`` fixture from ``pytest-playwright``, which conflicts with
    ``pytest-asyncio``'s event loop when ``asyncio_mode="auto"`` is set globally.

Browser-availability guard:
    ``chromium.launch()`` is wrapped so that, in an environment where the
    Playwright Chromium binary is not installed (``uv run playwright install
    chromium`` not run), the test SKIPS cleanly rather than hard-failing. The
    ``e2e`` marker already deselects this module from the default ``uv run
    pytest`` hot loop (pyproject ``-m "not e2e and not slow and not ci_only"``).
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

# The 3 ot_integrity entries (from data/seed_library_entries_extension.json) and
# a non-matching ransomware entry name — used by the filter assertions.
_OT_INTEGRITY_NAME = "Manipulation-of-View"  # process-view-manipulation
_OT_INTEGRITY_OTHER_NAMES = (
    "Field Instrument",  # field-instrument-spoofing
    "Pipeline SCADA",  # pipeline-scada-integrity
)
_RANSOMWARE_NAME = "Municipal Ransomware"  # public-sector-targeted-intrusion (NOT ot_integrity)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server_url() -> Iterator[str]:
    """Ephemeral SQLite migrated to head (44 entries) + uvicorn bound to it.

    Runs the real migration chain (widening + additive seed) so the 13 extension
    entries — including the 3 ``ot_integrity`` ones — and the control catalog are
    seeded deterministically. Yields the base URL; tears down the process + file.
    """
    db_path = tempfile.mktemp(suffix=".db", prefix="rf_e2e_")  # noqa: S306 — test-local ephemeral DB
    db_url = f"sqlite+aiosqlite:///{db_path}"
    env = {**os.environ, "DATABASE_URL": db_url}

    # 1. Migrate the ephemeral DB to head (seeds all 44 entries + control catalog
    #    via the real migration chain; alembic/env.py reads DATABASE_URL via
    #    get_settings()).
    mig = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert mig.returncode == 0, f"alembic upgrade head failed:\n{mig.stdout}\n{mig.stderr}"

    # 2. uvicorn bound to the migrated ephemeral DB.
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
    """Authenticate the Playwright browser, bootstrapping the first admin if needed.

    ``migrated_server_url`` is module-scoped (shared DB), so on the FIRST call
    ``/setup`` renders the bootstrap form; on subsequent calls the DB already has
    a user and ``GET /setup`` 303s to ``/`` — we then log in via ``/login``.

    Setup field names match ``src/idraa/routes/setup.py`` / ``setup/wizard.html``;
    login field names match ``auth/login.html``. The ``_csrf`` hidden field is
    rendered by ``csrf_field()`` and submitted automatically by the browser.
    """
    await page.goto(f"{base}/setup")
    # The setup form is only rendered on a fresh (user-less) DB; once a user
    # exists, GET /setup 303s away. ``migrated_server_url`` is module-scoped, so
    # the first caller bootstraps and later callers must log in. Branch on the
    # actual presence of the bootstrap form rather than a brittle URL compare
    # (the post-redirect landing page may itself redirect onward).
    has_setup_form = await page.locator("input[name='org_name']").count() > 0
    if not has_setup_form:
        # Already bootstrapped — log in via /login instead.
        await page.goto(f"{base}/login")
        await page.fill("input[name='email']", _ADMIN_EMAIL)
        await page.fill("input[name='password']", _ADMIN_PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(f"{base}/")
        return

    # Fresh DB — bootstrap the first admin via the setup wizard.
    await page.fill("input[name='org_name']", "E2E Org")
    # industry_type / organization_size are enum <select>s — pick the first
    # option (any valid enum value works for this journey).
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_user_browses_filters_ot_integrity_and_opens_detail(
    migrated_server_url: str,
) -> None:
    """Real operator journey: bootstrap -> browse -> filter ot_integrity ->
    open a new ot_integrity entry's detail (FAIR detail + P2c Recommended controls).
    """
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
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(15_000)

        # 1. Bootstrap first admin + login.
        await _bootstrap_admin_and_login(page, base)

        # 2. Browse the library — the 44 seeded entries render.
        await page.goto(f"{base}/library")
        initial_count = await page.locator(".card").count()
        assert initial_count > 0, "library should render seeded entry cards"

        # 3. Filter by the NEW ot_integrity effect (sidebar checkbox -> HTMX
        #    swaps #library-cards via hx-trigger="change from:input").
        #    browse.html renders the filter sidebar TWICE — a mobile collapsible
        #    (hidden by default) + the desktop <aside> (the HTMX-wired one). At
        #    the default md+ viewport the desktop one is the visible/active one,
        #    so scope the checkbox to the <aside> to avoid the hidden duplicate.
        await page.check("aside input[name='threat_event_type'][value='ot_integrity']")
        await page.wait_for_timeout(800)  # HTMX swap settle

        body = await page.content()
        # All 3 ot_integrity entries appear...
        assert _OT_INTEGRITY_NAME in body, (
            "process-view-manipulation should appear under ot_integrity"
        )
        for other in _OT_INTEGRITY_OTHER_NAMES:
            assert other in body, f"{other!r} (an ot_integrity entry) should appear"
        # ...and a non-matching ransomware entry does NOT.
        assert _RANSOMWARE_NAME not in body, (
            "a non-ot_integrity (ransomware) entry must be filtered OUT"
        )

        # 4. Open the ot_integrity entry detail; assert it renders incl. the
        #    P2c "Recommended controls" panel. Click the card's detail link
        #    (scoped to #library-cards so the filtered grid, not a sidebar
        #    label, is the click target) and wait for the /library/entries/<id>
        #    navigation.
        card_link = page.locator("#library-cards a[href^='/library/entries/']").filter(
            has_text=_OT_INTEGRITY_NAME
        )
        await card_link.first.click()
        await page.wait_for_url("**/library/entries/**")
        detail = await page.content()
        assert _OT_INTEGRITY_NAME in detail, "detail page renders the entry name"
        assert "Recommended controls" in detail, (
            "P2c recommended-controls panel must render for the ot_integrity entry"
        )

        await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_wizard_offers_ot_integrity_threat_category(
    migrated_server_url: str,
) -> None:
    """Closes NTH-1: the scenario wizard step-2 threat-category <select> renders
    ``ot_integrity`` as a real, selectable option (render-time, not source-grep).
    """
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(15_000)

        await _bootstrap_admin_and_login(page, base)

        # Drive the wizard to step 2 (basic info), where the threat-category
        # dropdown lives. Step 1 is the library picker; "Skip — start blank"
        # advances to step 2.
        await page.goto(f"{base}/scenarios/new/wizard")
        await page.click("text=Skip — start blank")

        select = page.locator("select[name='threat_category']")
        await select.wait_for(state="visible")
        option_values = await select.locator("option").evaluate_all(
            "opts => opts.map(o => o.value)"
        )
        assert "ot_integrity" in option_values, (
            f"wizard threat-category select must offer ot_integrity; got {option_values}"
        )
        # Prove it is genuinely selectable (not disabled / detached).
        await page.select_option("select[name='threat_category']", "ot_integrity")
        selected = await select.input_value()
        assert selected == "ot_integrity"

        await browser.close()
