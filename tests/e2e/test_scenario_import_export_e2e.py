"""E2E: admin imports a scenario from a CSV upload, confirms the round-trip.

Mirrors ``tests/e2e/test_library_extension_e2e.py`` harness EXACTLY: an
ephemeral per-run SQLite migrated to ``head`` via ``alembic upgrade head`` in a
subprocess, a uvicorn subprocess bound to it via ``DATABASE_URL``, the
``_bootstrap_admin_and_login`` helper, and the ``try/except PlaywrightError ->
pytest.skip`` graceful-skip guard so the test SKIPS cleanly (rather than
hard-failing) where the Chromium binary is not installed.

The ``e2e`` marker deselects this module from the default ``uv run pytest`` hot
loop (pyproject ``-m "not e2e and not slow and not ci_only"``).

Journey (the file-upload-as-scenario-create path):
    1. Bootstrap the first admin + login.
    2. GET /scenarios/import — the import page renders.
    3. Upload a known-good CSV (all required columns, vuln <= 1, PERT) via the
       real ``input[type=file][name=file]`` with an in-memory buffer.
    4. Preview shows a ``create`` badge for the scenario name.
    5. Confirm via the ``/scenarios/import/confirm`` form -> the scenario then
       appears in ``/scenarios``.
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
    """Ephemeral SQLite migrated to head + uvicorn bound to it via DATABASE_URL.

    Copied verbatim from ``tests/e2e/test_library_extension_e2e.py`` — runs the
    real migration chain on an ephemeral DB, launches uvicorn against it, yields
    the base URL, and tears down the process + file.
    """
    db_path = tempfile.mktemp(suffix=".db", prefix="rf_e2e_")  # noqa: S306 — test-local ephemeral DB
    db_url = f"sqlite+aiosqlite:///{db_path}"
    env = {**os.environ, "DATABASE_URL": db_url}

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

    Copied verbatim from ``tests/e2e/test_library_extension_e2e.py``. On the
    FIRST call against the module-scoped DB ``/setup`` renders the bootstrap
    form; on subsequent calls the DB already has a user and ``GET /setup`` 303s
    to ``/`` — we then log in via ``/login``.
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

    await page.fill("input[name='org_name']", "E2E Org")
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


# A known-good CSV: all required columns, vuln triplet <= 1, PERT distribution.
# Header order matches scenario_import_parsers.CSV_HEADERS (and, by the
# round-trip invariant, scenario_export.CSV_EXPORT_HEADERS).
_E2E_SCENARIO_NAME = "E2E Imported Scenario"
_E2E_CSV = (
    "name,description,scenario_type,threat_category,threat_actor_type,attack_vector,"
    "asset_class,version,status,distribution,tef_low,tef_mode,tef_high,vuln_low,"
    "vuln_mode,vuln_high,pl_low,pl_mode,pl_high,sl_low,sl_mode,sl_high\n"
    f"{_E2E_SCENARIO_NAME},,custom,ransomware,cybercriminals,,systems,1.0,active,PERT,"
    "0.1,0.5,2,0.2,0.35,0.6,100000,1000000,15000000,,,\n"
).encode()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_scenario_import_export_round_trip(migrated_server_url: str) -> None:
    """Admin uploads a known-good CSV, previews a ``create``, confirms, and the
    new scenario then appears in the scenarios list."""
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
        page.set_default_timeout(E2E_TIMEOUT_MS)

        # 1. Bootstrap first admin + login.
        await _bootstrap_admin_and_login(page, base)

        # 2. The import page renders.
        await page.goto(f"{base}/scenarios/import")
        assert "import" in (await page.content()).lower()

        # 3. Upload the known-good CSV via the real file input (name="file").
        await page.set_input_files(
            "input[type=file][name=file]",
            files=[{"name": "e2e.csv", "mimeType": "text/csv", "buffer": _E2E_CSV}],
        )
        await page.click("button[type=submit]")

        # 4. Preview shows a 'create' badge for the scenario name. The form is
        #    HTMX-boosted (global <body hx-boost="true">), so the POST response
        #    (the preview page) is swapped into the DOM without a full navigation
        #    — wait for the preview's confirm form to appear rather than racing
        #    page.content() against the in-flight swap.
        await page.wait_for_selector("form[action='/scenarios/import/confirm']")
        body = await page.content()
        assert _E2E_SCENARIO_NAME in body, "preview should list the uploaded scenario name"
        assert "create" in body.lower(), "preview should show a 'create' action badge"

        # 5. Confirm -> the confirm route applies the import and redirects to
        #    /scenarios. The form is HTMX-boosted, so the boosted POST follows
        #    the redirect and updates the URL — wait for that landing before
        #    re-navigating, so the in-flight POST isn't raced by the goto.
        await page.click("form[action='/scenarios/import/confirm'] button[type=submit]")
        await page.wait_for_url(f"{base}/scenarios")
        await page.goto(f"{base}/scenarios")
        assert _E2E_SCENARIO_NAME in (await page.content()), (
            "the confirmed scenario must appear in /scenarios"
        )

        await browser.close()
