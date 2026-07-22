"""E2E: LEC/EPC SVG chart hydration islands (epic #547 P1, Task 6).

Server geometry (``services/chart_svg.py``) and route wiring
(``services/dashboard_view_model.appetite_strip``) are already
unit/httpx-integration tested (``tests/unit/test_chart_svg.py``,
``tests/integration/test_dual_svg_charts.py``) — this module only proves
``static/js/charts.js`` wires up in a REAL browser: the p-slider readout
starts at the EXACT tolerance probability embedded in the JSON, the log-y
toggle swaps the visible LEC svg variant, LEC + EPC hover tooltips appear,
AND the LEC readout's with-controls loss at the initial tolerance
probability string-equals Card 3's server-rendered ``loss_at_tol_prob``
(golden-value agreement, plan-gate Arch-N1) — proving client (charts.js
``lossAtP``) and server (``dashboard_view_model.interpolate_loss_at_probability``)
interpolation can never silently diverge.

Harness copied from ``tests/e2e/test_run_detail_redesign_e2e.py`` (module-
scoped ``migrated_server_url`` uvicorn fixture: ephemeral SQLite +
``alembic upgrade head`` + real uvicorn subprocess, ``_bootstrap_admin_and_login``
copied verbatim from ``tests/e2e/test_run_execution_e2e.py``). The plan that
authored this task named the fixture parameters ``page: Page`` /
``completed_aggregate_run_url: str``, implying pytest-playwright's built-in
sync ``page`` fixture — but that fixture pattern is used NOWHERE in this
codebase; every existing e2e module drives its own ``async_playwright()``
browser instance instead (grep ``tests/e2e/*.py`` for ``sync_api`` returns
zero hits, ``async_api`` returns every module). This module follows the
established convention: ``completed_aggregate_run_url`` is still produced
(same name, same shape — a URL string), just built via a throwaway
``async_playwright()`` session rather than a sync ``page`` fixture. Same
``PlaywrightError -> pytest.skip`` guard for missing Chromium used
throughout the other e2e modules.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright, expect

from tests.e2e.conftest import E2E_TIMEOUT_MS


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server_url() -> Iterator[str]:
    """Ephemeral SQLite migrated to head + uvicorn bound to it via DATABASE_URL.

    Copied verbatim from ``tests/e2e/test_run_detail_redesign_e2e.py`` — each
    e2e module owns its server.
    """
    db_path = tempfile.mktemp(suffix=".db", prefix="rf_e2e_chart_")  # noqa: S306 — test-local ephemeral DB
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


_ADMIN_EMAIL = "admin@e2e-chart.local"
_ADMIN_PASSWORD = "E2e-passw0rd!"  # test-local credential


async def _bootstrap_admin_and_login(page: Page, base: str) -> None:
    """Copied verbatim from ``tests/e2e/test_run_execution_e2e.py``."""
    await page.goto(f"{base}/setup")
    has_setup_form = await page.locator("input[name='org_name']").count() > 0
    if not has_setup_form:
        await page.goto(f"{base}/login")
        await page.fill("input[name='email']", _ADMIN_EMAIL)
        await page.fill("input[name='password']", _ADMIN_PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(f"{base}/")
        return

    await page.fill("input[name='org_name']", "E2E Chart Org")
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Chart Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


async def _set_org_loss_tolerance(page: Page, base: str) -> None:
    """Set the org loss tolerance ($8M @ 5%) via the real ``/organization``
    form so the appetite verdict strip (Card 3) + LEC tolerance marker both
    render. Values match ``tests/integration/test_dual_svg_charts.py``'s
    ``client_completed_aggregate_with_tolerance`` fixture (0.05 -> "5.0%"
    via ``_fmt_pct`` / ``fmtP``)."""
    await page.goto(f"{base}/organization")
    await page.fill("input[name='loss_tolerance_amount']", "8000000")
    await page.fill("input[name='loss_tolerance_probability']", "0.05")
    await page.click("button[type='submit']")
    await page.wait_for_url(re.compile(r"/organization"))


# Two scenarios in one CSV import -> a single "Confirm import (2 scenarios)"
# click creates both -> selecting both on /analyses/new produces an AGGREGATE
# run (2+ scenario_ids). Copied/renamed from test_run_detail_redesign_e2e.py's
# fixture CSV so the aggregate curve spans a realistic multi-million-dollar
# range around the $8M tolerance amount.
_E2E_SCENARIO_1 = "E2E Chart Hydration Scenario Alpha"
_E2E_SCENARIO_2 = "E2E Chart Hydration Scenario Beta"
_E2E_CSV = (
    "name,description,scenario_type,threat_category,threat_actor_type,attack_vector,"
    "asset_class,version,status,distribution,tef_low,tef_mode,tef_high,vuln_low,"
    "vuln_mode,vuln_high,pl_low,pl_mode,pl_high,sl_low,sl_mode,sl_high\n"
    f"{_E2E_SCENARIO_1},,custom,ransomware,cybercriminals,,systems,1.0,active,PERT,"
    "0.1,0.5,2,0.2,0.35,0.6,100000,1000000,15000000,,,\n"
    f"{_E2E_SCENARIO_2},,custom,social_engineering,cybercriminals,,systems,1.0,active,PERT,"
    "0.2,0.6,3,0.15,0.3,0.5,50000,500000,8000000,,,\n"
).encode()

# Below the sync-inline threshold (services/runs.py: mc_iterations < 1000 runs
# synchronously) so the run completes without needing to wait on the htmx
# status-poll fragment -- the page lands already COMPLETED.
_E2E_MC_ITERATIONS = "500"

# Milestone-gate finding 2 (epic #547 P2): a single distinctly-named scenario
# for the SINGLE-run fixture below. Distinct name (not "Alpha"/"Beta") so a
# Playwright label:has-text() selector uniquely finds its checkbox on
# /analyses/new even when the aggregate fixture's scenarios already exist in
# the same org (fixture instantiation order across this module is not fixed).
_E2E_SCENARIO_SINGLE = "E2E Chart Hydration Scenario Solo"
_E2E_CSV_SINGLE = (
    "name,description,scenario_type,threat_category,threat_actor_type,attack_vector,"
    "asset_class,version,status,distribution,tef_low,tef_mode,tef_high,vuln_low,"
    "vuln_mode,vuln_high,pl_low,pl_mode,pl_high,sl_low,sl_mode,sl_high\n"
    f"{_E2E_SCENARIO_SINGLE},,custom,ransomware,cybercriminals,,systems,1.0,active,PERT,"
    "0.1,0.5,2,0.2,0.35,0.6,100000,1000000,15000000,,,\n"
).encode()


async def _create_completed_single_run(page: Page, base: str) -> str:
    """CSV-import ONE scenario, launch a SINGLE run (exactly one scenario
    checkbox checked -> services/runs.py dispatches run_type=single for a
    lone scenario_id), return its run id. Checkbox is targeted by the
    scenario's own label text (not "check every checkbox" like
    _create_completed_aggregate_run) so this stays a SINGLE run even if
    other scenarios already exist in the org."""
    await page.goto(f"{base}/scenarios/import")
    await page.set_input_files(
        "input[type=file][name=file]",
        files=[{"name": "e2e_chart_single.csv", "mimeType": "text/csv", "buffer": _E2E_CSV_SINGLE}],
    )
    await page.click("button[type=submit]")
    await page.wait_for_selector("form[action='/scenarios/import/confirm']")
    await page.click("form[action='/scenarios/import/confirm'] button[type=submit]")
    await page.wait_for_url(f"{base}/scenarios")

    await page.goto(f"{base}/analyses/new")
    await page.locator(
        f'label:has-text("{_E2E_SCENARIO_SINGLE}") input[name="scenario_ids"]'
    ).check()
    await page.fill("input[name='mc_iterations']", _E2E_MC_ITERATIONS)
    await page.click("button[type=submit]")
    # POST /analyses replies 204 + HX-Redirect: /runs/{id} — htmx navigates.
    # mc_iterations is below the sync-inline threshold, so the page lands
    # already COMPLETED (no verdict-strip on a SINGLE run's results panel —
    # that's an aggregate-only component — so wait on the single-run curve
    # figure instead).
    await page.wait_for_url(f"{base}/runs/*")
    run_id = page.url.rstrip("/").rsplit("/", 1)[-1]
    await page.wait_for_selector('[data-chart-hydrate="curve"]')
    return run_id


async def _create_completed_aggregate_run(page: Page, base: str) -> str:
    """CSV-import 2 scenarios, launch an AGGREGATE run, return its run id."""
    await page.goto(f"{base}/scenarios/import")
    await page.set_input_files(
        "input[type=file][name=file]",
        files=[{"name": "e2e_chart.csv", "mimeType": "text/csv", "buffer": _E2E_CSV}],
    )
    await page.click("button[type=submit]")
    await page.wait_for_selector("form[action='/scenarios/import/confirm']")
    await page.click("form[action='/scenarios/import/confirm'] button[type=submit]")
    await page.wait_for_url(f"{base}/scenarios")

    await page.goto(f"{base}/analyses/new")
    checkboxes = page.locator("input[name='scenario_ids']")
    count = await checkboxes.count()
    assert count >= 2, "both imported scenarios must appear in the analysis form"
    for i in range(count):
        await checkboxes.nth(i).check()
    await page.fill("input[name='mc_iterations']", _E2E_MC_ITERATIONS)
    await page.click("button[type=submit]")
    # POST /analyses replies 204 + HX-Redirect: /runs/{id} — htmx navigates.
    await page.wait_for_url(f"{base}/runs/*")
    run_id = page.url.rstrip("/").rsplit("/", 1)[-1]
    await page.wait_for_selector("#verdict-strip")
    return run_id


@pytest.fixture(scope="module")
def completed_aggregate_run_url(migrated_server_url: str) -> Iterator[str]:
    """URL of a COMPLETED aggregate run, org loss tolerance set to $8M @ 5%.

    Built once per module via a throwaway ``async_playwright()`` browser
    (bootstrap admin -> set org tolerance -> CSV-import 2 scenarios -> launch
    a sync-inline AGGREGATE run) so both test functions below share the same
    seeded run instead of repeating the ~10s setup flow twice.
    """

    async def _build() -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 900})
                page = await context.new_page()
                page.set_default_timeout(E2E_TIMEOUT_MS)
                await _bootstrap_admin_and_login(page, migrated_server_url)
                await _set_org_loss_tolerance(page, migrated_server_url)
                run_id = await _create_completed_aggregate_run(page, migrated_server_url)
                await context.close()
                return f"{migrated_server_url}/runs/{run_id}"
            finally:
                await browser.close()

    try:
        url = asyncio.run(_build())
    except PlaywrightError as exc:  # browser binary not installed
        pytest.skip(
            f"Playwright Chromium not installed (run `uv run playwright install chromium`): {exc}"
        )
    yield url


@pytest.fixture(scope="module")
def completed_single_run_url(migrated_server_url: str) -> Iterator[str]:
    """URL of a COMPLETED SINGLE-scenario run (run_type=single), org loss
    tolerance set to $8M @ 5%.

    Mirrors ``completed_aggregate_run_url``'s fixture pattern exactly
    (throwaway ``async_playwright()`` browser: bootstrap admin -> set org
    tolerance -> CSV-import 1 scenario -> launch a sync-inline run selecting
    exactly ONE scenario checkbox) but drives run_type=single instead of
    aggregate. A SINGLE run is required here because it renders a DIFFERENT
    set of P2 hydration islands than the aggregate fixture above:
    ``data-chart-hydrate="curve"`` (the single-run LEC/EPC line charts,
    ``macros/chart.html``'s ``loss_exceedance_curve`` /
    ``exceedance_probability_curve``) and ``data-chart-hydrate="bars"``
    (``risk_comparison_bar`` — always rendered for a completed run,
    regardless of whether the scenario has any mitigating controls, since
    ``run_view_model._build_risk_comparison`` derives it from base/residual
    ALE alone). The aggregate fixture's page instead renders the DUAL
    hydrateLec/hydrateEpc modes, already covered by
    ``test_lec_slider_toggle_tooltip`` / ``test_epc_hover_only_tooltip``.
    """

    async def _build() -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 900})
                page = await context.new_page()
                page.set_default_timeout(E2E_TIMEOUT_MS)
                await _bootstrap_admin_and_login(page, migrated_server_url)
                await _set_org_loss_tolerance(page, migrated_server_url)
                run_id = await _create_completed_single_run(page, migrated_server_url)
                await context.close()
                return f"{migrated_server_url}/runs/{run_id}"
            finally:
                await browser.close()

    try:
        url = asyncio.run(_build())
    except PlaywrightError as exc:  # browser binary not installed
        pytest.skip(
            f"Playwright Chromium not installed (run `uv run playwright install chromium`): {exc}"
        )
    yield url


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_lec_slider_toggle_tooltip(
    migrated_server_url: str, completed_aggregate_run_url: str
) -> None:
    """Slider readout starts at the EXACT tol probability, log toggle swaps
    LEC variants, LEC hover tooltip appears, and (Arch-N1) the readout's
    with-controls loss at init string-equals Card 3's server-rendered
    ``loss_at_tol_prob``."""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)
        # Re-auth on the new context (cookies are per-context).
        await _bootstrap_admin_and_login(page, migrated_server_url)

        await page.goto(completed_aggregate_run_url)
        fig = page.locator('[data-chart-hydrate="lec"]').first
        await expect(fig).to_be_visible()
        readout = fig.locator('[data-role="p-readout"]').first
        # P11: initial readout is computed from the EXACT tolerance probability
        # (fixture org.loss_tolerance_probability = 0.05 -> fmtP -> "5.0%").
        await expect(readout).to_contain_text("at 5.0%")

        # Arch-N1 GOLDEN-VALUE AGREEMENT: the readout's with-controls loss at
        # the initial tol_prob (client charts.js lossAtP, formatted via the
        # kpi_card-matching fmtMoneyReadout) must string-equal the
        # SERVER-computed strip.loss_at_tol_prob rendered in Card 3
        # (interpolate_loss_at_probability) — same linear-in-probability
        # convention AND the same money-formatting convention, so a mismatch
        # means client/server interpolation OR money-formatting diverged.
        # Readout is "at 5.0%: <without> → <with> [ (X% lower)]"; take the
        # with-controls figure (after "→", before any " (").
        readout_text = await readout.inner_text()
        readout_with_loss = readout_text.split("→")[1].split("(")[0].strip()
        card3_value_locator = (
            page.locator('[data-testid="appetite-strip"]')
            .get_by_text(re.compile(r"Loss at .*% exceedance"))
            .locator("xpath=following-sibling::p[1]")
        )
        card3_value = (await card3_value_locator.inner_text()).strip()
        assert readout_with_loss == card3_value, (
            f"client readout with-controls loss {readout_with_loss!r} != "
            f"server Card-3 loss_at_tol_prob {card3_value!r}"
        )

        before = await readout.inner_text()
        await fig.locator('[data-role="p-slider"]').first.fill("30")
        await expect(readout).not_to_have_text(before)

        # log toggle swaps the visible svg
        await fig.locator('[data-role="y-log"]').first.click()
        await expect(fig.locator('svg[data-y-scale="log"]').first).to_be_visible()
        await expect(fig.locator('svg[data-y-scale="linear"]').first).to_be_hidden()

        # LEC hover tooltip
        await fig.locator('svg[data-y-scale="log"]').first.hover(position={"x": 400, "y": 190})
        tip = fig.locator(".chart-tooltip").first
        await expect(tip).to_be_visible()
        assert re.search(r"Loss ≥", await tip.inner_text())

        await context.close()
        await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_epc_hover_only_tooltip(
    migrated_server_url: str, completed_aggregate_run_url: str
) -> None:
    """EPC figure has no slider/toggle (hover-only hydration mode) and shows
    an axis-appropriate ("P ≥") tooltip on hover."""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)
        await _bootstrap_admin_and_login(page, migrated_server_url)

        await page.goto(completed_aggregate_run_url)
        epc = page.locator('[data-chart-hydrate="epc"]').first
        # EPC has no slider/toggle.
        await expect(epc.locator('[data-role="p-slider"]')).to_have_count(0)

        # The EPC pane is behind the "Probability curve" tab
        # (runs/components/exceedance_chart.html's Alpine x-show/x-cloak
        # toggle) — hydration already ran on load (query selectors don't
        # require visibility), but the pane must be REVEALED before Playwright
        # can hover a point inside it.
        await page.get_by_role("button", name="Probability curve").click()
        await page.wait_for_selector("#epc-pane:visible")

        await epc.locator("svg").first.hover(position={"x": 400, "y": 190})
        tip = epc.locator(".chart-tooltip").first
        await expect(tip).to_be_visible()
        assert re.search(r"P ≥", await tip.inner_text())

        await context.close()
        await browser.close()


@pytest.mark.e2e
@pytest.mark.anyio
async def test_download_data_csv(
    migrated_server_url: str, completed_aggregate_run_url: str
) -> None:
    """The 'Download data' button (restored after the chart-vendor->SVG port)
    exports the LEC figure's points as CSV: trace,x,y header + both series,
    served as a real browser download."""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900}, accept_downloads=True
        )
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)
        await _bootstrap_admin_and_login(page, migrated_server_url)
        await page.goto(completed_aggregate_run_url)
        fig = page.locator('[data-chart-hydrate="lec"]').first
        await expect(fig).to_be_visible()
        async with page.expect_download() as dl_info:
            await fig.locator('[data-role="csv"]').first.click()
        download = await dl_info.value
        assert download.suggested_filename == "loss-exceedance.csv"
        path = await download.path()
        assert path is not None
        text = path.read_text(encoding="utf-8-sig")  # strip the UTF-8 BOM
        assert text.splitlines()[0] == "trace,x,y"
        assert "Without controls" in text and "With controls" in text
        await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_curve_hover_tooltip(migrated_server_url: str, completed_single_run_url: str) -> None:
    """epic #547 P2 milestone-gate finding 2: hydrateCurve (single LEC/EPC
    hover tooltip, ``data-chart-hydrate="curve"``) has ZERO prior browser
    coverage — the P1 e2e tests above only drive the DUAL ``lec``/``epc``
    hydration modes. This drives the SINGLE-run results panel
    (``runs/_results_panel.html``'s ``loss_exceedance_curve`` macro, the
    FIRST ``[data-chart-hydrate="curve"]`` figure on the page) and asserts
    the same hover-tooltip contract as the dual LEC card's tooltip
    (``test_lec_slider_toggle_tooltip``): pointermove over the curve shows
    the styled ``.chart-tooltip`` with a "Loss ≥" line — but via
    ``hydrateCurve``'s hover-only code path (no crosshair/slider/toggle)."""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)
        await _bootstrap_admin_and_login(page, migrated_server_url)

        await page.goto(completed_single_run_url)
        fig = page.locator('[data-chart-hydrate="curve"]').first
        await expect(fig).to_be_visible()
        # Hover-only mode: none of the dual LEC card's controls exist here.
        await expect(fig.locator('[data-role="p-slider"]')).to_have_count(0)

        await fig.locator("svg").first.hover(position={"x": 400, "y": 190})
        tip = fig.locator(".chart-tooltip").first
        await expect(tip).to_be_visible()
        assert re.search(r"Loss ≥", await tip.inner_text())

        await context.close()
        await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_bars_hover_tooltip(migrated_server_url: str, completed_single_run_url: str) -> None:
    """epic #547 P2 milestone-gate finding 2: hydrateBars (bar hover
    tooltip, ``data-chart-hydrate="bars"``) has ZERO prior browser coverage.
    Drives the SINGLE-run results panel's ``risk_comparison_bar`` (always
    rendered for a completed run — unlike ``control_effectiveness_bar``,
    which needs mitigating controls on the scenario to render bars instead
    of the "No controls" alert). Hovers the FIRST bar (the Base ALE bar,
    guaranteed non-zero width; the Reduction bar can be zero-width when the
    scenario has no controls, which this fixture's scenario does not)."""
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)
        await _bootstrap_admin_and_login(page, migrated_server_url)

        await page.goto(completed_single_run_url)
        bars = page.locator('[data-chart-hydrate="bars"][data-chart="risk-comparison"]')
        await expect(bars).to_be_visible()
        bar = bars.locator('rect[data-role="bar"]').first
        await bar.hover()
        tip = bars.locator(".chart-tooltip").first
        await expect(tip).to_be_visible()
        assert (await tip.inner_text()).strip() != ""

        await context.close()
        await browser.close()
