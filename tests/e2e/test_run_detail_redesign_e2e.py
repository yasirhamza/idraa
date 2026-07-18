"""E2E: aggregate run -> Summary view (redesign P1 T8) journey + 390px pass.

Closes the redesign's browser-coverage gap: T1-T7 rebuilt the aggregate
run-detail page into the Summary view (verdict strip, trust chips, dist
table, LEC/EPC toggle, scenario dumbbell, control ledger + Shapley matrix
disclosure, caveat chips + panel, controls snapshot) — this module drives
the REAL rendered page through a browser and asserts the whole thing holds
together end-to-end, plus a 390px mobile pass (attribution is desktop-only,
`hidden sm:block`).

Harness copied EXACTLY from ``tests/e2e/test_run_execution_e2e.py``
(module-scoped ``migrated_server_url`` uvicorn fixture: ephemeral SQLite +
``alembic upgrade head`` + real uvicorn subprocess) and the 390px viewport
convention from ``tests/e2e/test_wizard_mobile_e2e.py``
(``_MOBILE_VIEWPORT = {"width": 390, "height": 844}``). Same
``PlaywrightError -> pytest.skip`` guard for missing Chromium.
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
from typing import Any

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

    Copied verbatim from ``tests/e2e/test_run_execution_e2e.py`` — each e2e
    module owns its server.
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

    await page.fill("input[name='org_name']", "E2E Org")
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


# Two scenarios in one CSV import -> a single "Confirm import (2 scenarios)"
# click creates both -> selecting both on /analyses/new produces an AGGREGATE
# run (2+ scenario_ids).
_E2E_SCENARIO_1 = "E2E Redesign Scenario Alpha"
_E2E_SCENARIO_2 = "E2E Redesign Scenario Beta"
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


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_run_detail_summary_view_journey(migrated_server_url: str) -> None:
    """Aggregate run -> Summary view renders; toggle, theme, and mobile checks."""
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
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        # 1. Bootstrap first admin + login.
        await _bootstrap_admin_and_login(page, base)

        # 2. Create TWO scenarios via the proven CSV-import path (setup, not
        #    the subject under test).
        await page.goto(f"{base}/scenarios/import")
        await page.set_input_files(
            "input[type=file][name=file]",
            files=[{"name": "e2e.csv", "mimeType": "text/csv", "buffer": _E2E_CSV}],
        )
        await page.click("button[type=submit]")
        await page.wait_for_selector("form[action='/scenarios/import/confirm']")
        body = await page.content()
        assert _E2E_SCENARIO_1 in body and _E2E_SCENARIO_2 in body, (
            "preview should list both uploaded scenario names"
        )
        await page.click("form[action='/scenarios/import/confirm'] button[type=submit]")
        await page.wait_for_url(f"{base}/scenarios")

        # 3. /analyses/new: check BOTH scenarios -> 2 scenario_ids -> AGGREGATE.
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

        # 4. Land on /runs/{id}; wait for the verdict strip (sync-threshold
        #    iterations means the run is already COMPLETED on first render,
        #    but the status-poll fragment may still need a beat to settle).
        await page.wait_for_selector("#verdict-strip")

        # 5. Assert the redesigned Summary view's stable component ids render.
        for sel in (
            "#trust-chips",
            "#dist-table",
            "#exceedance",
            "#scenario-dumbbell",
            "#caveats",
        ):
            assert await page.locator(sel).count() == 1, f"{sel} should render exactly once"
            assert await page.locator(sel).is_visible(), f"{sel} should be visible"

        # 6. Display labels (methodology label map, e2e-level pin).
        page_text = await page.content()
        assert "Typical case (median)" in page_text
        assert "1-in-10 year (VaR 90%)" in page_text

        # 7. Caveat chip navigation: click the first 'a.cv'; assert the URL
        #    fragment and that the target <li> lands in the viewport.
        first_chip = page.locator("a.cv").first
        chip_href = await first_chip.get_attribute("href")
        assert chip_href and chip_href.startswith("#cv-")
        await first_chip.click()
        await page.wait_for_url(lambda url: url.endswith(chip_href))
        target_id = chip_href.lstrip("#")
        target = page.locator(f"#{target_id}")
        assert await target.count() == 1
        in_viewport = await target.evaluate(
            "el => { const r = el.getBoundingClientRect();"
            " return r.top >= 0 && r.top <= window.innerHeight; }"
        )
        assert in_viewport, f"caveat target #{target_id} should be scrolled into view"

        # 8. LEC/EPC toggle: click the 'Probability curve' tab; assert
        #    '#epc-pane' becomes visible with a non-zero-width first-party SVG
        #    chart (epic #547 P1 — dual cards are server-rendered SVG now, no
        #    resize-on-reveal needed; see exceedance_chart.html).
        lec_pane = page.locator("#lec-pane")
        epc_pane = page.locator("#epc-pane")
        assert await lec_pane.is_visible()
        # Scoped to role="button" (the Alpine tab <a>s) to avoid ambiguity
        # against any in-figure text nodes (series labels, tick labels).
        await page.get_by_role("button", name="Probability curve").click()
        await page.wait_for_selector("#epc-pane:visible")
        assert await epc_pane.is_visible()
        assert not await lec_pane.is_visible()
        epc_chart = epc_pane.locator('[data-chart="dual-epc"] svg').first
        await epc_chart.wait_for(state="visible")
        epc_width = await epc_chart.evaluate("el => el.getBoundingClientRect().width")
        assert epc_width > 0, "the EPC SVG must render at non-zero width once shown"

        # Flip back to LEC for the theme-flip step below.
        await page.get_by_role("button", name="Loss exceedance").click()
        await page.wait_for_selector("#lec-pane:visible")

        # 9. Theme flip: assert data-theme=dark + a series stroke color
        #    changed. SVG series colors are CSS custom properties
        #    (var(--chart-inherent)/var(--chart-residual), chart_palette.py)
        #    so the browser recomputes them on the data-theme attribute flip
        #    with no JS restyle needed (unlike the retired chart-vendor cards).
        lec_series = lec_pane.locator(
            '[data-chart="dual-lec"] svg[data-y-scale="linear"] path[data-series="without"]'
        ).first
        color_before = await lec_series.evaluate("el => getComputedStyle(el).stroke")
        await page.click("button[data-theme-set='dark']")
        await page.wait_for_function(
            "document.documentElement.getAttribute('data-theme') === 'dark'"
        )
        await page.wait_for_function(
            """(prev) => {
                const el = document.querySelector(
                    '#lec-pane [data-chart="dual-lec"] svg[data-y-scale="linear"] path[data-series="without"]'
                );
                if (!el) return false;
                return getComputedStyle(el).stroke !== prev;
            }""",
            arg=color_before,
        )
        color_after = await lec_series.evaluate("el => getComputedStyle(el).stroke")
        assert color_after != color_before, (
            "the LEC 'without' series stroke should change on theme flip (--chart-inherent CSS var)"
        )
        # Flip back to light.
        await page.click("button[data-theme-set='light']")
        await page.wait_for_function(
            "document.documentElement.getAttribute('data-theme') === 'light'"
        )

        # 10. Overflow menu: open the '⋯' action menu; assert 'Delete run…' is
        #     present. Fire (and dismiss) the confirm() dialog on "Purge sample
        #     arrays…" to guard Sec-B1 (single-quoted onsubmit attribute) AND
        #     the T8 e2e finding that the handler must call `window.confirm`,
        #     not bare `confirm` -- the destructive items' own hidden
        #     `<input name="confirm">` shadows the bare global inside the
        #     form's onsubmit scope chain, so an unqualified call throws and
        #     the dialog silently never fires (fixed in action_menu.html).
        #     Dismissing means the form is never actually submitted, so the
        #     run survives intact for the mobile pass below.
        dialogs: list[str] = []

        async def _record_and_dismiss(dialog: Any) -> None:
            dialogs.append(dialog.message)
            await dialog.dismiss()

        page.on("dialog", _record_and_dismiss)

        await page.click("button[aria-haspopup='menu']")
        menu = page.locator("div[role='menu']")
        await menu.wait_for(state="visible")
        assert await menu.get_by_role("menuitem", name="Delete run…").count() == 1
        purge_item = menu.get_by_role("menuitem", name="Purge sample arrays…")
        await purge_item.click()
        # Poll briefly for the async dialog handler to have fired.
        deadline = time.time() + 5
        while not dialogs and time.time() < deadline:
            await page.wait_for_timeout(100)
        assert dialogs, "the Purge sample arrays confirm() dialog should have fired"
        assert "Purge sample arrays" in dialogs[0]

        # The run must still be there (dialog was dismissed, not accepted).
        still_there = await page.request.get(f"{base}/runs/{run_id}")
        assert still_there.ok

        # 11. Mobile pass: new 390x844 context, same URL.
        mobile_context = await browser.new_context(viewport={"width": 390, "height": 844})
        mobile_page = await mobile_context.new_page()
        mobile_page.set_default_timeout(E2E_TIMEOUT_MS)
        # Re-auth on the new context (cookies are per-context).
        await _bootstrap_admin_and_login(mobile_page, base)
        await mobile_page.goto(f"{base}/runs/{run_id}")
        await mobile_page.wait_for_selector("#verdict-strip")

        overflow = await mobile_page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        if overflow > 1:  # DEBUG: toggle sections to find the document-extender
            report = await mobile_page.evaluate(
                """() => {
                    const base = document.documentElement.scrollWidth;
                    const out = [];
                    const sects = [...document.querySelectorAll('section[id], div[id], header, nav, main > *')];
                    for (const el of sects.slice(0, 40)) {
                        const d = el.style.display;
                        el.style.display = 'none';
                        const w = document.documentElement.scrollWidth;
                        el.style.display = d;
                        if (w < base) out.push((el.id || el.tagName) + ' removes ' + (base - w) + 'px');
                    }
                    return out;
                }"""
            )
            print("DOC-EXTENDERS:", *report, sep="\n  ")
        assert overflow <= 1, f"page scrolls horizontally on a 390px viewport by {overflow}px"
        assert await mobile_page.locator("#verdict-strip").is_visible()
        # Attribution (#control-ledger) is desktop-only (`hidden sm:block`).
        assert await mobile_page.locator("#control-ledger").count() == 1
        assert not await mobile_page.locator("#control-ledger").is_visible(), (
            "#control-ledger must be hidden at the 390px mobile breakpoint"
        )

        await mobile_context.close()
        await context.close()
        await browser.close()
