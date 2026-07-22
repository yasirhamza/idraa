"""E2E: admin walks the full register-import journey through a real browser.

Mirrors ``tests/e2e/test_scenario_import_export_e2e.py`` harness EXACTLY: an
ephemeral per-run SQLite migrated to ``head`` via ``alembic upgrade head`` in a
subprocess (the ``qualitative_mapping_bands`` canonical table is SEEDED by
that migration — no band-seeding fixture needed here), a uvicorn subprocess
bound to it via ``DATABASE_URL``, the ``_bootstrap_admin_and_login`` helper,
and the ``try/except PlaywrightError -> pytest.skip`` graceful-skip guard so
the test SKIPS cleanly (rather than hard-failing) where the Chromium binary is
not installed.

The ``e2e`` marker deselects this module from the default ``uv run pytest``
hot loop (pyproject ``-m "not e2e and not slow and not ci_only"``) — per the
chart-e2e-changes convention, this suite must be run explicitly
(``uv run pytest tests/e2e/test_register_import_journey.py -q``) before
shipping register-import UI changes.

Journey (epic #34 P1c Task 6 + Task 8, through a real browser):
    1. Bootstrap the first admin + login.
    2. GET /register-import — the upload page renders.
    3. Upload a single-sheet xlsx (Title/Likelihood/Impact/Category, one row
       whose Likelihood/Impact/Category values EXACTLY match canonical band
       labels case-insensitively) via the real
       ``input[type=file][name=file]``.
    4. Single sheet -> redirected straight to the column-map step; map all
       four headers to their targets and continue.
    5. Bind step: assert the value-bind step's PRE-SELECTION (spec §5 exact
       case-insensitive match, zero heuristics) picked "high" for both the
       likelihood and impact selects and "ransomware" for the category
       select, entirely from the server-rendered `selected` option — no
       explicit selection made by this test. Submit as-is.
    6. Preview: one row would-create, Convert button enabled.
    7. Convert -> the report page renders directly (200, not a redirect —
       the token is single-use and deleted on success).
    8. Report page links resolve: follow the created scenario's link and
       assert the detail page actually loads (200, scenario name present).
"""

from __future__ import annotations

import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator

import httpx
import openpyxl
import pytest

from tests.e2e.conftest import E2E_TIMEOUT_MS


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server_url() -> Iterator[str]:
    """Ephemeral SQLite migrated to head + uvicorn bound to it via DATABASE_URL.

    Copied verbatim from ``tests/e2e/test_scenario_import_export_e2e.py`` —
    runs the real migration chain on an ephemeral DB (which seeds the 10
    canonical ``qualitative_mapping_bands`` rows per
    ``e6882513a026_qualitative_mapping_bands.py`` — this test relies on that
    seed rather than inserting its own bands), launches uvicorn against it,
    yields the base URL, and tears down the process + file.
    """
    db_path = tempfile.mktemp(suffix=".db", prefix="rf_e2e_")  # noqa: S306 — test-local ephemeral DB
    db_url = f"sqlite+aiosqlite:///{db_path}"
    env = {**os.environ, "DATABASE_URL": db_url, "AUTH_MFA_POLICY": "optional"}

    # 1. Migrate the ephemeral DB to head (alembic/env.py reads DATABASE_URL via
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

    Copied verbatim from ``tests/e2e/test_scenario_import_export_e2e.py``. On
    the FIRST call against the module-scoped DB ``/setup`` renders the
    bootstrap form; on subsequent calls the DB already has a user and
    ``GET /setup`` 303s to ``/`` — we then log in via ``/login``. The
    bootstrapped user is ADMIN (register-import routes are
    ``require_role(UserRole.ADMIN)``).
    """
    await page.goto(f"{base}/setup")
    has_setup_form = await page.locator("input[name='org_name']").count() > 0
    if not has_setup_form:
        await page.goto(f"{base}/login")
        await page.fill("input[name='email']", _ADMIN_EMAIL)
        await page.fill("input[name='password']", _ADMIN_PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(f"{base}/")
        return

    await page.fill("input[name='org_name']", "E2E Register Import Org")
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


# A single-sheet xlsx: Title/Likelihood/Impact/Category headers, one row.
# "High" and "Ransomware" are EXACT case-insensitive matches to canonical
# band/category labels (seeded frequency+magnitude bands both have a "high"
# label; ThreatCategory has "ransomware") — chosen deliberately so the
# value-bind step's pre-selection (spec §5: exact case-insensitive match
# ONLY, zero heuristics) fires on every one of the three groups without this
# test making any manual selection.
_E2E_SCENARIO_NAME = "E2E Vendor Phishing Exposure"
_HEADERS = ["Title", "Likelihood", "Impact", "Category"]
_TARGETS = ["title", "likelihood", "impact", "category"]


def _xlsx_bytes() -> bytes:
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Register"
    ws.append(_HEADERS)
    ws.append([_E2E_SCENARIO_NAME, "High", "High", "Ransomware"])
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_register_import_journey_upload_to_report(migrated_server_url: str) -> None:
    """Admin uploads a register, maps columns, confirms pre-selected
    bindings, previews, converts, and the report's scenario link resolves."""
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
        # Desktop viewport (plan Task 8 requirement) — register-import's
        # multi-fieldset bind/column-map pages are desktop-first admin UI
        # (Global Constraints: wrapped in only_on_md() like library_overrides).
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        # 1. Bootstrap first admin + login.
        await _bootstrap_admin_and_login(page, base)

        # 2. The upload page renders.
        await page.goto(f"{base}/register-import")
        assert "Import register" in (await page.content())

        # 3. Upload the xlsx via the real file input (name="file").
        await page.set_input_files(
            "input[type=file][name=file]",
            files=[
                {
                    "name": "e2e_register.xlsx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "buffer": _xlsx_bytes(),
                }
            ],
        )
        await page.click("button[type=submit]")

        # 4. Single sheet -> redirected straight to /columns (hx-boost swaps
        #    the response into the DOM; wait for the column-map form rather
        #    than racing page.content() against the in-flight swap).
        await page.wait_for_selector("select[name='target_0']")
        for i, target in enumerate(_TARGETS):
            await page.select_option(f"select[name='target_{i}']", target)
        await page.click("button[type=submit]")

        # 5. Bind step: assert pre-selection fired from the server-rendered
        #    `selected` option — no explicit selection here.
        await page.wait_for_selector("select[name='likelihood_target_0']")
        assert await page.input_value("select[name='likelihood_target_0']") == "high"
        assert await page.input_value("select[name='impact_target_0']") == "high"
        assert await page.input_value("select[name='category_target_0']") == "ransomware"
        await page.click("form[action$='/bind'] button[type=submit]")

        # 6. Preview: one row would-create, Convert enabled.
        await page.wait_for_selector("form[action$='/convert']")
        preview_body = await page.content()
        assert _E2E_SCENARIO_NAME in preview_body
        assert "1 to create" in preview_body
        convert_button = page.locator("form[action$='/convert'] button[type=submit]")
        assert await convert_button.is_enabled()

        # 7. Convert -> report renders directly (200, not a redirect). Wait
        #    on the "Created" section heading, NOT a bare
        #    `a[href^='/scenarios/']` selector — the sidebar's persistent
        #    "ATT&CK coverage" nav link (href="/scenarios/attack-coverage")
        #    matches that prefix on EVERY authenticated page, including the
        #    still-loading preview page, so it would resolve the wait
        #    immediately against stale content instead of the report page.
        await convert_button.click()
        await page.wait_for_selector("h2:text-is('Created')")
        report_body = await page.content()
        assert _E2E_SCENARIO_NAME in report_body
        assert "created" in report_body.lower()

        # 8. Report links resolve: follow the created scenario's link (the
        #    desktop table's row link, scoped to the "Created" section's own
        #    table — `table a[...]` also disambiguates from the DOM-present-
        #    but-hidden mobile card's link to the same href, which would
        #    otherwise violate Playwright's strict-locator single-match
        #    rule) and confirm the detail page actually loads.
        scenario_link = page.locator("table a[href^='/scenarios/']").first
        href = await scenario_link.get_attribute("href")
        assert href
        await page.goto(f"{base}{href}")
        detail_body = await page.content()
        assert _E2E_SCENARIO_NAME in detail_body

        await browser.close()
