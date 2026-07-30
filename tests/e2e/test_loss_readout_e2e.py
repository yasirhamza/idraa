"""E2E (sigma-recal PR3 T5 Step 3): the ten-item hand-run Playwright checklist
for the live loss-dispersion readout (waterline chart, dispersion badges,
capacity-ceiling states, currency/JS-disabled degradation).

HAND-RUN ONLY. ``e2e``-marked so it is excluded from the default pytest
selection (``-m "not e2e and not slow and not ci_only"``, pyproject.toml)
and from CI — chart JS/CSS changes never run e2e in the merge path
(feedback_run_full_e2e_on_chart_changes; the Playwright CI job was removed).
Invoke explicitly:

    uv run pytest tests/e2e/test_loss_readout_e2e.py -m e2e -v

Checklist source: ``docs/superpowers/plans/2026-07-30-sigma-recal-pr3.md``,
Task 5 Step 3 (grep "THE checklist"). Ten items map 1:1 to the ten
``test_item0N_*`` functions below, so a test run reports PASS/FAIL per item.

Harness: each e2e module owns its own server (repo convention — see
``tests/e2e/test_chart_hydration_e2e.py``'s docstring, which explains why
this repo never uses pytest-playwright's sync ``page`` fixture). Copied here:
a module-scoped ``migrated_server_url`` fixture (ephemeral SQLite +
``alembic upgrade head`` + a real uvicorn subprocess) and a per-module
``_bootstrap_admin_and_login`` helper. Every test drives its own
``async_playwright()`` browser/context so state (login cookies, wizard
drafts, org settings) never leaks across tests through a shared page.

Disclosed approximations (SC-7-style honesty beats a fake pass):

  - Item 1's "touch" half: Playwright's ``Touchscreen`` API has no
    drag/swipe primitive (``tap()`` only — no OS-level touch gesture
    synthesis is exposed). This harness approximates a touch drag by
    dispatching real ``Touch``/``TouchEvent`` objects at the SVG element via
    ``page.evaluate`` (``browser.new_context(has_touch=True)``) — a
    same-shape event sequence, not a literal OS touch gesture.
  - Item 3 ("dark mode ... remain legible"): perceptual legibility is not
    machine-checkable. This test instead proves the chart is THEME-WIRED —
    the CSS custom properties the chart's SVG markup references
    (``--color-brand`` / ``--color-ink-2`` / ``--color-status-critical`` /
    ``--color-status-warning`` / ``--color-status-info``) resolve to
    different values under ``data-theme="dark"`` vs the light default, and
    the chart markup itself uses ``var(...)`` (never a hardcoded hex) — a
    proxy for "will re-theme correctly", not a screenshot legibility
    judgment.
  - Item 8 is an XFAIL, not a skip or a silent pass — a genuine, executed
    finding, disclosed rather than routed around: EVERY reachable
    distribution combination fails a JS-disabled submission on the CREATE
    form (``form.html``), not just the PERT-default one. PERT (the
    default, and TEF/Vuln's ONLY legal distribution — D12 "lognormal is
    strictly a loss distribution") needs its ``mode`` input, wrapped in
    ``<template x-if="dist === 'pert'">``; ``<template>`` content is never
    materialized into the live DOM without JS to clone it, regardless of
    server-rendered HTML, so ``tef_mode``/``pl_mode`` never reach the POST
    body — executed result: 422, raw ``KeyError('tef_mode')`` (not even a
    clean validation message). The seemingly-obvious workaround — select
    lognormal for PL/SL instead — ALSO fails: lognormal requires a
    capacity ``max`` (``validate_fair_distributions(require_loss_max=True)``),
    and that input is EQUALLY template-gated
    (``<template x-if="dist === 'lognormal'">`` wraps
    ``capacity_max_input``) — executed result: 422, "primary_loss.max is
    required for a lognormal loss distribution". This is a PRE-EXISTING
    gap (Epic B #326's per-node PERT|lognormal selector, which predates
    PR3) — PR3's OWN addition (the readout mount) degrades correctly via
    ``x-cloak``, confirmed by this same test. See the test's ``xfail``
    marker for the full evidence trail.

Ancillary finding (not a checklist item, does not block any PASS, disclosed
per the same honesty standard): every readout mount logs a transient browser
console error on init/recompute — ``Cannot read properties of undefined
(reading 'meanPx'|'capPx'|'medianPx'|'realizedMedianPx'|'clippedTailPath'|
'cloneNode')``, thrown from inside Alpine's own reactive-effect runner while
evaluating a ``stats.chart.*`` binding a tick before the guarding
``x-if="... && stats.chart"`` re-settles. It self-recovers within roughly a
second in every run observed here, but it IS why several assertions below use
``page.wait_for_function`` polling (``_wait_dd_settled``) instead of a single
``expect().not_to_have_text()`` call — one full run of this suite needed
longer than a 15s ``expect()`` budget to converge. Root cause not
investigated further (out of scope for this hand-run verification task);
worth a follow-up issue if it recurs.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import FloatRect, Page, async_playwright, expect

from tests.e2e.conftest import E2E_TIMEOUT_MS

# Playwright's `expect()` assertion timeout defaults to 5000ms regardless of
# `page.set_default_timeout()` (which only covers actions: click/fill/etc.).
# recompute() in loss_preview.js debounces 150ms, which is comfortably under
# 5000ms in isolation — but several tests below observed a slower first
# convergence (the readout computing a valid fit after a fresh fill) under
# real pytest load, occasionally exceeding 5000ms (executed: the same
# sequence resolved in ~500ms in a standalone repro script, but flaked past
# 5000ms inside the full suite). Widen the assertion timeout to match the
# harness's own E2E_TIMEOUT_MS rather than papering over with a fixed sleep.
expect.set_options(timeout=E2E_TIMEOUT_MS)

# ---------------------------------------------------------------------------
# Server + auth harness (copied convention — see module docstring).
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server_url() -> Iterator[str]:
    """Ephemeral SQLite migrated to head + uvicorn bound to it via DATABASE_URL."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="rf_e2e_loss_readout_")
    os.close(db_fd)  # SQLite reopens by path; a zero-length file is a valid fresh DB
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


_ADMIN_EMAIL = "admin@e2e-loss-readout.local"
_ADMIN_PASSWORD = "E2e-passw0rd!"  # test-local credential


async def _bootstrap_admin_and_login(page: Page, base: str) -> None:
    """Bootstrap the first admin (once per server) or log the returning
    admin back in on a fresh browser context. Copied convention from
    tests/e2e/test_run_execution_e2e.py / test_chart_hydration_e2e.py."""
    await page.goto(f"{base}/setup")
    has_setup_form = await page.locator("input[name='org_name']").count() > 0
    if not has_setup_form:
        await page.goto(f"{base}/login")
        await page.fill("input[name='email']", _ADMIN_EMAIL)
        await page.fill("input[name='password']", _ADMIN_PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(f"{base}/")
        return

    await page.fill("input[name='org_name']", "E2E Loss Readout Org")
    await page.locator("select[name='industry_type'] option").first.wait_for(state="attached")
    await page.select_option("select[name='industry_type']", index=0)
    await page.select_option("select[name='organization_size']", index=0)
    await page.fill("input[name='email']", _ADMIN_EMAIL)
    await page.fill("input[name='full_name']", "E2E Loss Readout Admin")
    await page.fill("input[name='password']", _ADMIN_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_url(f"{base}/")


async def _set_org_annual_revenue(page: Page, base: str, amount: int) -> None:
    """Set org.annual_revenue via the real /organization form. Every test
    that depends on the capacity cap (cfg.cap == annual_revenue * capacity_k,
    default k=1.0 — services/loss_capacity.py) sets this EXPLICITLY at its
    own start so tests are order-independent (no reliance on a value some
    earlier test happened to leave behind)."""
    await page.goto(f"{base}/organization")
    await page.fill("input[name='annual_revenue']", str(amount))
    await page.click("button[type='submit']")
    await page.wait_for_url(re.compile(r"/organization"))


async def _seed_eur_fx_rate(page: Page, base: str) -> None:
    """Seed an active EUR rate via the ADMIN-gated /fx-rates form so the
    scenario-form entry-currency <select> offers a non-USD option (Multi-
    currency P2: selectable_currencies only includes rated codes —
    routes/scenarios.py:544)."""
    await page.goto(f"{base}/fx-rates")
    await page.select_option("select[name='code']", "EUR")
    await page.fill("input[name='usd_rate']", "0.92")
    await page.fill("input[name='as_of_date']", "2026-07-01")
    await page.fill("input[name='source']", "e2e-fixture")
    await page.click("button:has-text('Save rate')")
    await page.wait_for_url(re.compile(r"/fx-rates"))


async def _fill_tef_vuln_defaults(page: Page) -> None:
    """Sane PERT TEF + Vuln values for the simple create form — required
    fields the readout tests below don't otherwise care about."""
    await page.fill("input[name='tef_low']", "0.5")
    await page.fill("input[name='tef_mode']", "1")
    await page.fill("input[name='tef_high']", "3")
    await page.fill("input[name='vuln_low']", "0.1")
    await page.fill("input[name='vuln_mode']", "0.2")
    await page.fill("input[name='vuln_high']", "0.4")


async def _goto_new_scenario_lognormal_pl(
    page: Page, base: str, *, name: str, pl_low: float, pl_high: float
) -> None:
    """Navigate to /scenarios/new (fresh hard load — picks up whatever
    cap/currency policy is current server-side) and drive PL to lognormal
    with the given p5/p95 pair. Leaves TEF/Vuln filled with sane defaults;
    does NOT submit."""
    await page.goto(f"{base}/scenarios/new")
    await page.fill("input[name='name']", name)
    await page.select_option("select[name='threat_category']", "ransomware")
    await page.select_option("select[name='asset_class']", "systems")
    await _fill_tef_vuln_defaults(page)
    await page.select_option("select[name='pl_dist']", "lognormal")
    await page.wait_for_function(
        "() => document.querySelectorAll(\"input[name='pl_mode']\").length === 0"
    )
    await page.fill("input[name='pl_low']", str(pl_low))
    await page.fill("input[name='pl_high']", str(pl_high))


async def _fill_money(page: Page, selector: str, value: str) -> None:
    """Fill a wizard SME-row money input (pl_low_N/pl_high_N/sl_low_N/
    sl_high_N — the ones wrapped in the per-row formatted-display Alpine
    component in _fair_params_form_inner.html: `x-data="{display: '',
    fmt(v){...}}"`, x-model="display"). Plain `locator.fill()` on these
    APPENDS rather than replaces (observed: filling "100000" onto a
    pre-seeded "21241.38" landed the input at "21241.38100000", not
    "100000" — reproducible, not a one-off flake) — the custom x-model
    binding fights Playwright's own clear-then-type sequence. An explicit
    `.clear()` first (which empties the field through a different code
    path) avoids it; the plain `pert_input` inputs on the expert/create
    form (native <input type="number">, no wrapper) do not need this."""
    loc = page.locator(selector)
    await loc.clear()
    await loc.fill(value)


async def _wait_dd_settled(page: Page, dt_selector: str) -> None:
    """Wait for a numbers-row <dd> (matched by an exact dt CSS selector) to
    settle past the invalid-fit dash state ("—").

    loss_preview.js's recompute() debounces 150ms, and — separately, an
    executed finding disclosed in the module docstring — the readout's
    Alpine effects occasionally throw a transient
    "Cannot read properties of undefined (reading '...')" console error
    while stats.chart is briefly undefined mid-update (observed on EVERY
    mount, self-recovering in isolation within ~1-2s, but one full pytest
    run of this suite saw it take past a 15s Playwright `expect()` budget).
    `page.wait_for_function` polls the LIVE DOM in-browser (no per-poll
    Python round trip) up to the harness's own E2E_TIMEOUT_MS, which is
    more resilient to that transient than a single `expect()` call."""
    await page.wait_for_function(
        "(sel) => { const el = document.querySelector(sel); "
        "return el && el.textContent.trim() !== '' && el.textContent.trim() !== '—'; }",
        arg=dt_selector,
        timeout=E2E_TIMEOUT_MS,
    )


async def _wizard_blank_flow_to_step4(page: Page, base: str, name: str) -> None:
    """Skip-library wizard flow to step 4 (Impact), mirroring
    tests/e2e/test_wizard_blank_flow.py's proven flow exactly. Row 0 for
    every fieldset is eager-seeded from the IRIS baseline
    (routes/scenarios.py step-3/4 GET), so step 3 needs no edits to pass
    validation and advance."""
    await page.goto(f"{base}/scenarios/new/wizard")
    await page.click("text=Skip — start blank")
    await page.fill("input[name='name']", name)
    await page.select_option("select[name='threat_category']", "ransomware")
    await page.select_option("select[name='threat_actor_type']", "cybercriminals")
    await page.select_option("select[name='asset_class']", "systems")
    await page.click("button:has-text('Next →')")
    await page.wait_for_selector("input[name='tef_low_0']")
    await page.click("button:has-text('Next →')")
    await page.wait_for_selector("input[name='pl_low_0']")


async def _svg_box(page: Page, dom_id: str) -> FloatRect:
    box = await page.locator(f"#{dom_id} svg").bounding_box()
    assert box is not None, f"#{dom_id} svg has no bounding box (not rendered/visible)"
    return box


async def _mouse_drag_waterline(page: Page, dom_id: str, frac_from: float, frac_to: float) -> None:
    box = await _svg_box(page, dom_id)
    y = box["y"] + box["height"] / 2
    x_from = box["x"] + box["width"] * frac_from
    x_to = box["x"] + box["width"] * frac_to
    await page.mouse.move(x_from, y)
    await page.mouse.down()
    await page.mouse.move(x_to, y, steps=8)
    await page.mouse.up()


async def _touch_drag_waterline(page: Page, dom_id: str, frac_from: float, frac_to: float) -> None:
    """Approximated touch drag — see module docstring's disclosed-
    approximation note. Dispatches real Touch/TouchEvent objects (requires
    a `has_touch=True` browser context) directly at the SVG element, since
    Playwright's Touchscreen API exposes tap() only."""
    box = await _svg_box(page, dom_id)
    y = box["y"] + box["height"] / 2
    x_from = box["x"] + box["width"] * frac_from
    x_to = box["x"] + box["width"] * frac_to
    await page.eval_on_selector(
        f"#{dom_id} svg",
        """(el, arg) => {
            const [x1, y1, x2, y2] = arg;
            const mk = (x, y) => new Touch({identifier: 1, target: el, clientX: x, clientY: y});
            el.dispatchEvent(new TouchEvent('touchstart', {
                touches: [mk(x1, y1)], bubbles: true, cancelable: true,
            }));
            el.dispatchEvent(new TouchEvent('touchmove', {
                touches: [mk(x2, y2)], bubbles: true, cancelable: true,
            }));
        }""",
        [x_from, y, x_to, y],
    )


def _launch_browser(p: Any, **kwargs: Any) -> Any:
    return p.chromium.launch(headless=True, **kwargs)


# ---------------------------------------------------------------------------
# Item 1 — Waterline drag (mouse + touch) updates the readout in both
# lognormal and capped_pert modes.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item01_waterline_drag_mouse_and_touch_both_modes(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900}, has_touch=True)
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        await _set_org_annual_revenue(page, base, 50_000_000)
        await _wizard_blank_flow_to_step4(page, base, "E2E waterline drag")

        # capped_pert is the default mode (loss_catastrophic unchecked).
        await _fill_money(page, "input[name='pl_low_0']", "100000")
        await _fill_money(page, "input[name='pl_high_0']", "5000000")
        # The Preview-ALE line ALSO contains "≈" (strict-mode collision) —
        # pin the waterline paragraph by its x-show expression instead.
        readout_p = page.locator("#loss-readout-pl p[x-show*='waterlineValue']")
        await expect(readout_p).not_to_be_visible()

        # Mouse drag: mousedown alone should already populate a reading.
        await _mouse_drag_waterline(page, "loss-readout-pl", 0.3, 0.3)
        await expect(readout_p).to_be_visible()
        text_a = await readout_p.inner_text()
        await _mouse_drag_waterline(page, "loss-readout-pl", 0.3, 0.7)
        text_b = await readout_p.inner_text()
        assert text_a != text_b, (
            f"capped_pert mouse drag did not change the waterline readout: {text_a!r}"
        )

        # Touch drag (approximated — see module docstring): same field, a
        # different drag range so a stale-value false pass is impossible.
        await _touch_drag_waterline(page, "loss-readout-pl", 0.2, 0.2)
        text_c = await readout_p.inner_text()
        await _touch_drag_waterline(page, "loss-readout-pl", 0.2, 0.8)
        text_d = await readout_p.inner_text()
        assert text_c != text_d, (
            f"capped_pert touch drag did not change the waterline readout: {text_c!r}"
        )

        # Flip to lognormal mode: cfg.mode is server-baked per
        # state.loss_shape at GET time (routes/scenarios.py:_build_readout_cfg
        # line ~4233), not client-reactive — see item 4's test for the full
        # rationale. Advance (Next, revenue is set so the D18 gate passes)
        # then Back to re-render step 4 from the persisted catastrophic state.
        await page.check("input[name='loss_catastrophic']")
        await page.click("button:has-text('Next →')")
        await page.wait_for_selector("text=← Back")
        await page.click("text=← Back")
        await page.wait_for_selector("input[name='pl_low_0']")
        # Lognormal readout numbers row uses "Median (preview" — confirms the
        # flip landed before dragging.
        await expect(
            page.locator("#loss-readout-pl dt:has-text('Median (preview')")
        ).to_be_visible()

        await _mouse_drag_waterline(page, "loss-readout-pl", 0.35, 0.35)
        text_e = await readout_p.inner_text()
        await _mouse_drag_waterline(page, "loss-readout-pl", 0.35, 0.65)
        text_f = await readout_p.inner_text()
        assert text_e != text_f, (
            f"lognormal mouse drag did not change the waterline readout: {text_e!r}"
        )

        await context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 2 — Per-row focus switch updates "previewing SME row N" + numbers/chart.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item02_per_row_focus_switch(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        await _set_org_annual_revenue(page, base, 50_000_000)
        await _wizard_blank_flow_to_step4(page, base, "E2E row focus switch")

        median_dd = page.locator("#loss-readout-pl dt:has-text('Realized median (preview)') + dd")
        row_label = page.locator("#loss-readout-pl span:has-text('previewing')")
        seed_median = await median_dd.inner_text()

        # recompute() debounces 150ms (loss_preview.js), and the row-grid's
        # @blur commits row.low/row.high before dispatching loss-row-input —
        # explicit .blur() + expect().not_to_have_text() (auto-retrying)
        # waits for the ACTUAL numbers-row update instead of racing the
        # debounce with an immediate inner_text() snapshot (that race is
        # exactly what produced a false-negative on the first run of this
        # test: both "row 0" and "row 1" reads returned the stale
        # server-seeded value).
        await _fill_money(page, "input[name='pl_low_0']", "100000")
        await _fill_money(page, "input[name='pl_high_0']", "5000000")
        await page.locator("input[name='pl_high_0']").blur()
        await expect(row_label).to_contain_text("1")
        await expect(median_dd).not_to_have_text(seed_median)
        median_row0 = await median_dd.inner_text()

        # Add a second SME row (client-side rows.push — no HTMX round trip)
        # and give it a clearly different (low, high) pair.
        await page.click("button:has-text('+ Add SME estimate')")
        await page.wait_for_selector("input[name='pl_low_1']")
        await _fill_money(page, "input[name='pl_low_1']", "2000000")
        await _fill_money(page, "input[name='pl_high_1']", "80000000")
        await page.locator("input[name='pl_high_1']").blur()

        await expect(row_label).to_contain_text("2")
        await expect(median_dd).not_to_have_text(median_row0)
        median_row1 = await median_dd.inner_text()
        assert median_row1 != median_row0, (
            "focusing row 2 (idx=1) did not change the readout's numbers row"
        )

        # Switch focus back to row 0 — label and numbers must revert.
        await page.locator("input[name='pl_low_0']").focus()
        await expect(row_label).to_contain_text("1")
        await expect(median_dd).to_have_text(median_row0)

        await context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 3 — Dark mode: chart colors remain theme-wired (see disclosed
# approximation in the module docstring).
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item03_dark_mode_chart_theme_wiring(migrated_server_url: str) -> None:
    base = migrated_server_url
    tokens = [
        "--color-brand",
        "--color-ink-2",
        "--color-status-critical",
        "--color-status-warning",
        "--color-status-info",
    ]
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")

        # Light-theme pass (default — base.html's data-theme starts "light"
        # and no localStorage override is set in this context).
        light_context = await browser.new_context(viewport={"width": 1280, "height": 900})
        light_page = await light_context.new_page()
        light_page.set_default_timeout(E2E_TIMEOUT_MS)
        await _bootstrap_admin_and_login(light_page, base)
        await _goto_new_scenario_lognormal_pl(
            light_page, base, name="E2E dark mode light pass", pl_low=100_000, pl_high=5_000_000
        )
        assert (
            await light_page.evaluate("document.documentElement.getAttribute('data-theme')")
            == "light"
        )
        light_values = {
            t: (
                await light_page.evaluate(
                    "(t) => getComputedStyle(document.documentElement).getPropertyValue(t).trim()",
                    t,
                )
            )
            for t in tokens
        }
        for t, v in light_values.items():
            assert v, f"{t} resolved empty under light theme"
        # Chart markup references the tokens via var(...) — never a hardcoded
        # hex — so re-theming is a pure CSS concern, proven by the value
        # diff below.
        light_html = await light_page.locator("#loss-readout-pl svg").inner_html()
        for t in tokens:
            assert f"var({t})" in light_html, f"chart markup does not reference {t}"
        await light_context.close()

        # Dark-theme pass: force via the SAME persisted-preference mechanism
        # the app itself uses (base.html reads localStorage.idraa.theme
        # pre-paint) so this exercises the real app code path, not a test-only
        # CSS override.
        dark_context = await browser.new_context(viewport={"width": 1280, "height": 900})
        await dark_context.add_init_script(
            "try { localStorage.setItem('idraa.theme', 'dark'); } catch (e) {}"
        )
        dark_page = await dark_context.new_page()
        dark_page.set_default_timeout(E2E_TIMEOUT_MS)
        await _bootstrap_admin_and_login(dark_page, base)
        await _goto_new_scenario_lognormal_pl(
            dark_page, base, name="E2E dark mode dark pass", pl_low=100_000, pl_high=5_000_000
        )
        assert (
            await dark_page.evaluate("document.documentElement.getAttribute('data-theme')")
            == "dark"
        )
        dark_values = {
            t: (
                await dark_page.evaluate(
                    "(t) => getComputedStyle(document.documentElement).getPropertyValue(t).trim()",
                    t,
                )
            )
            for t in tokens
        }
        for t, v in dark_values.items():
            assert v, f"{t} resolved empty under dark theme"
            assert v != light_values[t], (
                f"{t} did not change between light ({light_values[t]!r}) and "
                f"dark ({v!r}) — the chart would render identically in both themes"
            )
        await dark_context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 4 — Catastrophic vs capped toggle flips the readout between lognormal
# and capped_pert.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item04_catastrophic_toggle_flips_readout_mode(migrated_server_url: str) -> None:
    """cfg.mode is server-baked from state.loss_shape at step-4 GET time
    (routes/scenarios.py ~line 4233: `mode = "lognormal" if
    state.loss_shape == "catastrophic" else "capped_pert"`), not a live
    client-reactive binding to the `catastrophic` Alpine checkbox var — no
    hx-post/hx-trigger exists on the checkbox itself. So "flips ... live"
    is verified here as: toggle -> advance (persists loss_shape,
    server-side-gated on org revenue being set, D18) -> return to step 4 ->
    the readout re-renders in the new mode. This is disclosed explicitly
    (not literally "instant, no navigation") rather than asserted as a
    silent pass."""
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        await _set_org_annual_revenue(page, base, 50_000_000)
        await _wizard_blank_flow_to_step4(page, base, "E2E catastrophic toggle")
        await _fill_money(page, "input[name='pl_low_0']", "100000")
        await _fill_money(page, "input[name='pl_high_0']", "5000000")

        # Default (unchecked) is capped_pert: realized-median/sampled-mean
        # labels, no cap line ever. Playwright's has_text string form is
        # CASE-INSENSITIVE, so plain "Median (preview" would also match
        # "Realized median (preview)" (lowercase "median") — a regex
        # (case-sensitive by default, no IGNORECASE) avoids that collision.
        await expect(
            page.locator("#loss-readout-pl dt:has-text('Realized median (preview)')")
        ).to_be_visible()
        await expect(
            page.locator("#loss-readout-pl dt", has_text=re.compile(r"Median \(preview"))
        ).to_have_count(0)

        await page.check("input[name='loss_catastrophic']")
        await page.click("button:has-text('Next →')")
        await page.wait_for_selector("text=← Back")
        await page.click("text=← Back")
        await page.wait_for_selector("input[name='pl_low_0']")

        # Now lognormal: median+mean+cap-line/shaded-tail vocabulary,
        # realized-median vocabulary gone.
        await expect(
            page.locator("#loss-readout-pl dt:has-text('Median (preview')")
        ).to_be_visible()
        await expect(page.locator("#loss-readout-pl dt:has-text('Mean (preview')")).to_be_visible()
        await expect(
            page.locator("#loss-readout-pl dt:has-text('Realized median (preview)')")
        ).to_have_count(0)

        # Flip back to capped — same Next/Back round trip, unchecked.
        await page.uncheck("input[name='loss_catastrophic']")
        await page.click("button:has-text('Next →')")
        await page.wait_for_selector("text=← Back")
        await page.click("text=← Back")
        await page.wait_for_selector("input[name='pl_low_0']")
        await expect(
            page.locator("#loss-readout-pl dt:has-text('Realized median (preview)')")
        ).to_be_visible()

        await context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 5 — Invalid-input dash state.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item05_invalid_input_dash_state(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        # Inverted: high < low -> lognormal_from_quantiles domain violation
        # -> fit.mu === null -> stats.valid = false.
        await _goto_new_scenario_lognormal_pl(
            page, base, name="E2E dash state", pl_low=5_000_000, pl_high=1_000_000
        )

        for label in ("Median (preview)", "Mean (preview)", "P95 (preview)", "P99 (preview)"):
            dd = page.locator(f"#loss-readout-pl dt:has-text('{label}') + dd")
            await expect(dd).to_have_text("—")
        # sigma always renders the "(platform default X.XX)" suffix even
        # when invalid — only the fmtSigma(stats.sigma) prefix dashes out.
        sigma_dd = page.locator("#loss-readout-pl dt:has-text('σ (preview)') + dd")
        await expect(sigma_dd).to_contain_text("—")
        await expect(sigma_dd).to_contain_text("platform default")

        svg_class = await page.locator("#loss-readout-pl svg").get_attribute("class")
        assert svg_class is not None and "opacity-30" in svg_class and "grayscale" in svg_class, (
            f"invalid-fit chart is not greyed out: class={svg_class!r}"
        )

        await context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 6 — capClamped copy: a cap at/below the fitted median.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item06_cap_clamped_copy(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        # cap == annual_revenue * k(1.0) == $1,000,000. The lognormal
        # readout's quantile basis on the expert form is p5/p95 (labels
        # "5th pct"/"95th pct"), NOT p50/p95 — median = sqrt(p5 * p95) =
        # sqrt(5,000,000 * 20,000,000) = $10,000,000 > cap -> capClamped
        # (cap <= exp(mu)). Executed (side-by-side, issue #90 convention):
        # hand value $10,000,000 vs the browser's own "Median (preview..." —
        # not asserted directly here, but the capClamped badge firing is the
        # observable consequence and IS asserted below.
        await _set_org_annual_revenue(page, base, 1_000_000)
        await _goto_new_scenario_lognormal_pl(
            page, base, name="E2E cap clamped", pl_low=5_000_000, pl_high=20_000_000
        )

        # Scoped to span.badge (not the #loss-readout-pl container itself,
        # which is always visible and whose textContent includes hidden
        # x-cloak descendants regardless of x-show — has_text on the
        # container would pass even if the badge stayed hidden).
        await expect(
            page.locator(
                "#loss-readout-pl span.badge", has_text="cap fully clamps this distribution"
            )
        ).to_be_visible()

        await context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 7 — Capacity-ceiling "will be rejected" state, distinct from the 2.2
# advisory badge.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item07_capacity_ceiling_hard_reject_state(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        # cap == $5,000,000. Quantile basis is p5/p95 (see item 6's comment
        # on the p50-vs-p5 correction — the first version of this test used
        # p50=$500,000/p95=$50,000,000 and landed the median AT the cap
        # (both capClamped and ceilingExceeded fired simultaneously, a
        # degenerate case, caught by running it rather than trusting hand
        # math alone — issue #90 side-by-side convention). Corrected:
        # p5=$10,000, p95=$40,000,000 ->
        #   median = sqrt(p5 * p95) = sqrt(4e11) ~= $632,456 (well below
        #     cap -> NOT capClamped, cap/median ~= 7.91)
        #   sigma = ln(p95/p5) / (2*Z95) = ln(4000)/3.2897 ~= 2.521
        #   sigmaCeiling = ln(cap/median)/Z95 = ln(7.91)/1.6449 ~= 1.256
        # sigma(2.521) >= sigmaCeiling(1.256) -> hard-reject badge; sigma
        # also > warnThreshold(2.2) -> the 2.2 advisory badge fires too, so
        # this proves the two are DISTINCT, simultaneously-visible states.
        await _set_org_annual_revenue(page, base, 5_000_000)
        await _goto_new_scenario_lognormal_pl(
            page, base, name="E2E capacity ceiling", pl_low=10_000, pl_high=40_000_000
        )

        # Scoped to span.badge — see item 6's comment on why has_text on the
        # #loss-readout-pl container itself would not discriminate visibility.
        ceiling_badge = page.locator(
            "#loss-readout-pl span.badge",
            has_text="exceeds the capacity floor — saving will be rejected",
        )
        advisory_badge = page.locator("#loss-readout-pl span.badge", has_text="advisory ceiling")
        await expect(ceiling_badge).to_be_visible()
        await expect(advisory_badge).to_be_visible()
        # Not the same element/copy.
        ceiling_text = await ceiling_badge.first.inner_text()
        advisory_text = await advisory_badge.first.inner_text()
        assert ceiling_text != advisory_text

        # Not the capClamped state (cap sits above the median here). The
        # badge is x-show-gated (present in the DOM, hidden via CSS), so
        # the correct check is visibility, not DOM element count.
        await expect(
            page.locator(
                "#loss-readout-pl span.badge", has_text="cap fully clamps this distribution"
            )
        ).not_to_be_visible()

        await context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 8 — JS-disabled load: pl/sl inputs still submit, no readout mounted.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason=(
        "PRE-EXISTING gap (Epic B #326, predates PR3), not a readout "
        "regression: PERT's 'mode' input AND the lognormal capacity-cap "
        "'max' input are both wrapped in <template x-if=...> in form.html "
        "(<template> content is inert without JS to clone it into the "
        "live DOM, regardless of server-rendered HTML). Every reachable "
        "combination fails: (a) PERT (the default, and TEF/Vuln's ONLY "
        "legal distribution per D12 'lognormal is strictly a loss "
        "distribution') needs 'mode', which is template-gated -> 422 "
        "raw KeyError('tef_mode') on submit; (b) lognormal for PL/SL "
        "needs 'max' (validate_fair_distributions require_loss_max=True), "
        "also template-gated -> 422 'primary_loss.max is required for a "
        "lognormal loss distribution'. So this test intentionally attempts "
        "the checklist's literal claim (successful no-JS submission) and "
        "is EXPECTED TO FAIL until that gap is fixed — see the full "
        "report for both executed error payloads."
    ),
)
async def test_item08_js_disabled_progressive_enhancement(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")

        # A normal (JS-enabled) context bootstraps the admin first — the
        # /setup + /login flows themselves are plain HTML forms and would
        # work JS-disabled too, but reusing the already-covered bootstrap
        # path keeps this test focused on the scenario form itself.
        setup_context = await browser.new_context()
        setup_page = await setup_context.new_page()
        setup_page.set_default_timeout(E2E_TIMEOUT_MS)
        await _bootstrap_admin_and_login(setup_page, base)
        cookies = await setup_context.cookies()
        await setup_context.close()

        js_off_context = await browser.new_context(java_script_enabled=False)
        # Cookie (from .cookies()) and SetCookieParam (expected by
        # .add_cookies(), not publicly exported from playwright.async_api)
        # are structurally near-identical TypedDicts; mypy treats them as
        # invariant.
        await js_off_context.add_cookies(cookies)  # type: ignore[arg-type]
        page = await js_off_context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await page.goto(f"{base}/scenarios/new")
        await page.fill("input[name='name']", "E2E JS disabled")
        await page.select_option("select[name='threat_category']", "ransomware")
        await page.select_option("select[name='asset_class']", "systems")

        # PERT (the default, and TEF/Vuln's only legal distribution per D12)
        # needs tef_mode/pl_mode, both wrapped in
        # <template x-if="dist === 'pert'"> — confirmed absent from the DOM.
        assert await page.locator("input[name='pl_mode']").count() == 0
        assert await page.locator("input[name='tef_mode']").count() == 0
        await page.fill("input[name='tef_low']", "0.5")
        await page.fill("input[name='tef_high']", "3")
        await page.fill("input[name='vuln_low']", "0.1")
        await page.fill("input[name='vuln_mode']", "0.2")
        await page.fill("input[name='vuln_high']", "0.4")
        await page.fill("input[name='pl_low']", "100000")
        await page.fill("input[name='pl_high']", "5000000")

        # No readout mounted: the wrapper carries x-cloak, and Alpine never
        # ran to remove it, so [x-cloak]{display:none!important} keeps it
        # hidden (app.css:208) regardless of what's inside. This half of
        # the checklist claim DOES hold regardless of the submission gap
        # below.
        assert await page.locator("#loss-readout-pl").count() == 1, (
            "readout mount should still be present in the SSR'd DOM "
            "(x-cloak hides it via CSS, it isn't removed server-side)"
        )
        await expect(page.locator("#loss-readout-pl")).not_to_be_visible()

        await page.click("button:has-text('Create scenario')")
        # EXPECTED (per checklist item 8): the form submits and redirects to
        # the new scenario. ACTUAL (executed): the server 422s — pure PERT
        # defaults raise a raw KeyError('tef_mode'), confirming tef_mode
        # never reached the POST body. This assertion is the one expected
        # to fail (xfail above), preserving the checklist's literal claim as
        # the test's intent rather than quietly asserting the gap as
        # correct behavior.
        await page.wait_for_url(re.compile(r"/scenarios/[0-9a-f-]+$"))
        await expect(page.locator("text=E2E JS disabled")).to_be_visible()

        await js_off_context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 9 — Prefill/overlay button click followed by typing: readout stays
# alive with fresh props after the outerHTML swap (SC-6 regression).
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item09_prefill_button_swap_then_typing(migrated_server_url: str) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        # org_industry/org_revenue_tier (calibration_context_from_org) both
        # need to be truthy for the "Reset impact to baseline" button to
        # render — revenue_tier derives from annual_revenue.
        await _set_org_annual_revenue(page, base, 50_000_000)
        await _wizard_blank_flow_to_step4(page, base, "E2E prefill swap then type")

        # The prefill buttons live inside a collapsed <details> disclosure
        # (UAT 2026-07-21: "prefill actions demoted into a collapsed
        # disclosure") — closed by default, so the button is not actually
        # visible/clickable until the <summary> is opened.
        await page.click("summary:has-text('Prefill options')")
        prefill_button = page.locator("button:has-text('Reset impact to baseline')")
        await expect(prefill_button).to_be_visible()

        # Row 0 is ALREADY IRIS-baseline-seeded on first visit (eager seed,
        # routes/scenarios.py step-3/4 GET) — clicking "Reset impact to
        # baseline" against that same baseline would be a no-op change, so
        # the swap wouldn't be OBSERVABLE via a before/after value diff.
        # Change it first so the reset has something to visibly reset.
        await _fill_money(page, "input[name='pl_low_0']", "1234567")
        await page.locator("input[name='pl_low_0']").blur()
        await expect(page.locator("input[name='pl_low_0']")).to_have_value(re.compile("1,234,567"))
        before_value = await page.locator("input[name='pl_low_0']").input_value()

        await prefill_button.click()
        # outerHTML swap replaces the whole #fair-params-inner subtree
        # (a NEW pl_low_0 node) — wait for evidence the swap actually
        # happened rather than a fixed sleep.
        await page.wait_for_function(
            "(prev) => { const el = document.querySelector(\"input[name='pl_low_0']\"); "
            "return el && el.value !== prev; }",
            arg=before_value,
        )
        await page.wait_for_selector("#loss-readout-pl")

        median_dd = page.locator("#loss-readout-pl dt:has-text('Realized median (preview)') + dd")
        before_type = await median_dd.inner_text()

        # Type into the FRESH (post-swap) row inputs — the SC-6 regression
        # this item guards is exactly "the new mount is dead after a swap".
        await _fill_money(page, "input[name='pl_low_0']", "9000000")
        await _fill_money(page, "input[name='pl_high_0']", "40000000")
        await page.locator("input[name='pl_low_0']").blur()

        await expect(median_dd).not_to_have_text(before_type)
        after_type = await median_dd.inner_text()
        assert after_type != "—", "readout after the swap did not pick up live typing"

        await context.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Item 10 — HARD-LOAD Alpine binding (T3.b regression class): direct
# URL/F5, never boosted navigation.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_item10_hard_load_alpine_binding_currency_and_pin_panel(
    migrated_server_url: str,
) -> None:
    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc}")
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        await _seed_eur_fx_rate(page, base)
        await _set_org_annual_revenue(page, base, 50_000_000)

        # --- CREATE form half: page.goto is a genuine hard load (first
        # navigation on this page/context to this URL — never htmx-boosted,
        # since there is no prior in-app navigation to intercept it). ---
        await _goto_new_scenario_lognormal_pl(
            page, base, name="E2E hard load currency", pl_low=100_000, pl_high=5_000_000
        )
        # First <div> in the numbers <dl> is always Median when
        # entryCurrencyIsUsd (stable regardless of the capped/uncapped label
        # variant) — nth-child CSS selector for the in-browser poll helper.
        median_dd_css = "#loss-readout-pl dl > div:nth-child(1) dd"
        median_row = page.locator(median_dd_css)
        await _wait_dd_settled(page, median_dd_css)

        await page.select_option("select#entry_currency", "EUR")
        # Scoped to the specific <p> (not the #loss-readout-pl container
        # itself — see item 6/7's comment on why has_text there would not
        # discriminate visibility).
        await expect(
            page.locator(
                "#loss-readout-pl p",
                has_text="hidden while the entry currency above isn’t USD",
            )
        ).to_be_visible()
        await expect(median_row).not_to_be_visible()

        # --- EDIT form half: create a real lognormal-PL, unpinned scenario,
        # then hit /edit via page.goto directly (hard load, not a link
        # click from the view page — the T3.b regression this guards was
        # specifically an Alpine root missing on a from-scratch document
        # parse, invisible via hx-boost's MutationObserver side effect). ---
        await page.select_option("select#entry_currency", "USD")
        await page.click("button:has-text('Create scenario')")
        await page.wait_for_url(re.compile(r"/scenarios/[0-9a-f-]+$"))
        scenario_url = page.url
        scenario_id = scenario_url.rstrip("/").rsplit("/", 1)[-1]

        await page.goto(f"{base}/scenarios/{scenario_id}/edit")
        pin_panel = page.locator("[data-testid='loss-pin-panel-pl']")
        await expect(pin_panel).to_be_visible()
        pin_median_css = "#loss-readout-pl-pin dl > div:nth-child(1) dd"
        pin_median = page.locator(pin_median_css)
        # The panel prefills p50/p95 FROM the scenario's current stored fit
        # (a sensible starting point for pinning) — so it already reads a
        # real value on load, not the dash state (that's item 5's fixture,
        # not this one). Capture the prefilled reading, then prove typing
        # updates it live.
        await _wait_dd_settled(page, pin_median_css)
        prefilled_reading = await pin_median.inner_text()

        await page.fill("#pl_pin_p50", "2000000")
        await page.fill("#pl_pin_p95", "9000000")
        await expect(pin_median).not_to_have_text(prefilled_reading)
        first_reading = await pin_median.inner_text()

        await page.fill("#pl_pin_p50", "3000000")
        await expect(pin_median).not_to_have_text(first_reading)

        await context.close()
        await browser.close()
