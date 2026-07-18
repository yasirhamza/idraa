"""E2E: full run lifecycle — execute, watch it complete, download the PDF, delete.

Closes the run-execution / PDF / deletion e2e gaps from the whole-project
evaluation. Mirrors the ``tests/e2e/test_scenario_import_export_e2e.py``
harness EXACTLY: ephemeral per-run SQLite migrated to ``head``, a uvicorn
subprocess bound via ``DATABASE_URL``, ``_bootstrap_admin_and_login``, and
the ``PlaywrightError -> pytest.skip`` guard for missing Chromium.

Journey:
    1. Bootstrap the first admin + login.
    2. Create a scenario (via the proven CSV-import path — setup, not the
       subject under test).
    3. /analyses/new: pick the scenario, set iterations ABOVE the sync
       threshold so the BACKGROUND dispatch path runs, submit -> HX-Redirect
       lands on /runs/{id}.
    4. The status-poll fragment (htmx every-1s) self-updates QUEUED/RUNNING
       -> "Completed" without a manual reload; the results panel renders.
    5. GET /reports/run/{id} through the browser session -> 200 + %PDF magic.
    6. "Delete run" (native confirm() accepted) -> 303 to /?deleted=1; the
       run URL then 404s.
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

    Copied verbatim from ``tests/e2e/test_scenario_import_export_e2e.py`` (the
    established harness convention — each e2e module owns its server).
    """
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
    """Copied verbatim from ``tests/e2e/test_scenario_import_export_e2e.py``."""
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


_E2E_SCENARIO_NAME = "E2E Run Lifecycle Scenario"
_E2E_CSV = (
    "name,description,scenario_type,threat_category,threat_actor_type,attack_vector,"
    "asset_class,version,status,distribution,tef_low,tef_mode,tef_high,vuln_low,"
    "vuln_mode,vuln_high,pl_low,pl_mode,pl_high,sl_low,sl_mode,sl_high\n"
    f"{_E2E_SCENARIO_NAME},,custom,ransomware,cybercriminals,,systems,1.0,active,PERT,"
    "0.1,0.5,2,0.2,0.35,0.6,100000,1000000,15000000,,,\n"
).encode()

# Above the sync-inline threshold (services/runs.py executes < 1000 inline) so
# the BACKGROUND dispatch + status-poll path is what this test exercises;
# small enough to complete in well under the poll timeout.
_E2E_MC_ITERATIONS = "2000"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_run_execution_pdf_and_delete_journey(migrated_server_url: str) -> None:
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

        # 2. Setup: create a scenario via the proven CSV-import path.
        await page.goto(f"{base}/scenarios/import")
        await page.set_input_files(
            "input[type=file][name=file]",
            files=[{"name": "e2e.csv", "mimeType": "text/csv", "buffer": _E2E_CSV}],
        )
        await page.click("button[type=submit]")
        await page.wait_for_selector("form[action='/scenarios/import/confirm']")
        await page.click("form[action='/scenarios/import/confirm'] button[type=submit]")
        await page.wait_for_url(f"{base}/scenarios")

        # 3. New analysis: pick the scenario, background-path iterations, submit.
        await page.goto(f"{base}/analyses/new")
        await page.check("input[name='scenario_ids']")
        await page.fill("input[name='mc_iterations']", _E2E_MC_ITERATIONS)
        await page.click("button[type=submit]")
        # POST /analyses replies 204 + HX-Redirect: /runs/{id} — htmx navigates.
        await page.wait_for_url(f"{base}/runs/*")
        run_id = page.url.rstrip("/").rsplit("/", 1)[-1]

        # 4. The htmx status poll (every 1s, self-stopping) flips the fragment
        #    to the Completed alert WITHOUT a manual reload, then renders the
        #    results panel.
        await page.wait_for_selector(
            "#run-status-inner .alert-success",
            timeout=60_000,
        )
        body = await page.content()
        assert "Completed" in body
        assert "annualized" in body.lower() or "residual" in body.lower(), (
            "results panel should render risk figures after completion"
        )

        # 5. PDF download through the SAME browser session (shared cookies):
        #    200 + %PDF magic bytes.
        pdf = await page.request.get(f"{base}/reports/run/{run_id}")
        assert pdf.ok, f"PDF route returned {pdf.status}"
        pdf_bytes = await pdf.body()
        assert pdf_bytes[:5] == b"%PDF-", "report download must be a real PDF"
        assert len(pdf_bytes) > 5_000, "suspiciously small PDF — renderer likely failed"

        # 6. Delete the run (native confirm() accepted) -> dashboard redirect,
        #    then the run URL 404s and its PDF route is gone too.
        page.on("dialog", lambda d: d.accept())
        # T7c run-detail redesign: delete moved into the top-right action menu
        # -- open it first (the form is x-show-hidden until then).
        await page.click("button[aria-haspopup='menu']")
        await page.click("form[action='/runs/" + run_id + "/delete'] button[type=submit]")
        await page.wait_for_url(f"{base}/?deleted=1")

        gone = await page.request.get(f"{base}/runs/{run_id}")
        assert gone.status == 404, "deleted run detail must 404"
        pdf_gone = await page.request.get(f"{base}/reports/run/{run_id}")
        assert pdf_gone.status == 404, "deleted run's PDF route must 404"

        await browser.close()
