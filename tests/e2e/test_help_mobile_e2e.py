"""E2E (mobile): the in-app Help section is usable on a phone viewport.

Exercises the three Help surfaces at a 390px-wide Chromium viewport:
  1. The sidebar "Help" link (reached via the mobile hamburger drawer) navigates
     to ``/help`` and the registry-driven index renders (cluster headings +
     article cards).
  2. On ``/analyses/new`` the inline help "?" trigger
     (``[aria-label="Help"]`` / ``hx-get="/help/run-and-read-analyses"``) opens
     the HTMX-swapped slide-over drawer: ``#help-drawer-body`` gets an
     ``<article>`` and ``#help-drawer`` becomes visible. Pressing Escape (and,
     separately, clicking the ✕ ``[aria-label="Close help"]``) hides it again.
  3. ``/help/getting-started`` rendered as a full page (direct nav) shows the
     "Help" breadcrumb + the article ``<h1>`` and has no horizontal overflow on
     the article container at 390px.

Harness mirrors ``tests/e2e/test_wizard_mobile_e2e.py`` EXACTLY: an ephemeral
per-run SQLite migrated to ``head``, a uvicorn subprocess bound via
``DATABASE_URL``, and a ``try/except PlaywrightError -> pytest.skip`` guard so
this SKIPS cleanly where Chromium is not installed.
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
async def test_help_section_usable_on_mobile_viewport(migrated_server_url: str) -> None:
    """Sidebar Help nav, the HTMX drawer on /analyses/new, and the full-page
    article all render and behave on a 390px phone viewport."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright, expect

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

        # 0. Bootstrap first admin + login -> land on the dashboard.
        await _bootstrap_admin_and_login(page, base)

        # --- 1. Sidebar "Help" link navigates to /help; the index renders. ---
        # On <md the sidebar is hidden behind the hamburger; open it first.
        await page.click("label[aria-label='Open navigation']")
        help_link = page.locator("#sidebar a[href='/help']")
        await help_link.scroll_into_view_if_needed()
        await help_link.click()
        await page.wait_for_url(f"{base}/help")

        # Index is registry-driven: cluster headings + article cards.
        await expect(page.get_by_role("heading", name="Idraa — Help")).to_be_visible()
        # "Getting started" is both a cluster <h2> and an article card <a>.
        assert await page.locator("a[href='/help/getting-started']").count() >= 1
        assert await page.locator("a[href='/help/run-and-read-analyses']").count() >= 1

        # --- 2. HTMX slide-over drawer on /analyses/new. ---
        await page.goto(f"{base}/analyses/new")
        trigger = page.locator("button[aria-label='Help'][hx-get='/help/run-and-read-analyses']")
        await expect(trigger).to_be_visible()

        drawer = page.locator("#help-drawer")
        # Drawer is x-cloak/x-show hidden before the trigger fires.
        await expect(drawer).to_be_hidden()

        # Wait until Alpine has registered the drawer store, otherwise the
        # @click="$store.helpDrawer.show()" handler isn't wired yet and the
        # click only fires the HTMX swap (article lands but drawer stays hidden).
        await page.wait_for_function(
            "() => window.Alpine && Alpine.store('helpDrawer') !== undefined"
        )

        await trigger.click()
        # HTMX swaps the article into #help-drawer-body; Alpine reveals #help-drawer.
        await expect(page.locator("#help-drawer-body article")).to_be_visible()
        await expect(drawer).to_be_visible()
        # The swapped body is the run-and-read article (its <h1>).
        await expect(page.locator("#help-drawer-body article h1")).to_have_text(
            "Run & read analyses"
        )

        # 2a. Escape closes the drawer.
        await page.keyboard.press("Escape")
        await expect(drawer).to_be_hidden()

        # 2b. Re-open, then close via the ✕ button.
        await trigger.click()
        await expect(drawer).to_be_visible()
        await page.click("button[aria-label='Close help']")
        await expect(drawer).to_be_hidden()

        # --- 3. Full-page article at /help/getting-started, readable at 390px. ---
        await page.goto(f"{base}/help/getting-started")
        # Breadcrumb "Help" link + the article heading.
        await expect(
            page.locator("nav[aria-label='Breadcrumb'] a", has_text="Help")
        ).to_be_visible()
        article = page.locator("article")
        await expect(article.locator("h1")).to_have_text("Getting started")

        # No horizontal overflow on the article container at 390px.
        overflow = await page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert overflow <= 1, (
            f"the help article scrolls horizontally on a 390px viewport by {overflow}px "
            "— the article does not fit a phone screen"
        )

        await context.close()
        await browser.close()


# Desktop width — clears the xl (1280px) breakpoint where the three-column
# docs-hub layout (help/article_page.html) activates.
_DESKTOP_VIEWPORT = {"width": 1440, "height": 900}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_article_nav_disclosure_closed_on_mobile_toc_hidden(
    migrated_server_url: str,
) -> None:
    """At 390px (help-overhaul P1 T4): the article tree is a CLOSED <details>
    disclosure (Arch-I3) — summary visible, tree links revealed only on
    click — and the on-page TOC column never renders at all."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright, expect

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

        await _bootstrap_admin_and_login(page, base)
        await page.goto(f"{base}/help/run-and-read-analyses")

        # The on-page TOC lives in a `hidden xl:block` column — never visible
        # below the xl breakpoint.
        await expect(page.locator("[data-help-toc]")).to_be_hidden()

        details = page.locator("details.help-nav-shell")
        summary = details.locator("summary")
        await expect(summary).to_be_visible()
        await expect(summary).to_have_text("All help articles")
        assert await details.get_attribute("open") is None  # closed by default

        nav_link = details.locator("a[href='/help/getting-started']")
        await expect(nav_link).to_be_hidden()

        await summary.click()
        await expect(nav_link).to_be_visible()

        await context.close()
        await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_three_column_layout_and_scrollspy_on_desktop(migrated_server_url: str) -> None:
    """At 1440px (help-overhaul P1 T4): the article tree, article content,
    and the on-page TOC all render simultaneously (three columns), and
    scrolling toggles which TOC link carries `help-toc-link-active`."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright, expect

    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:  # browser binary not installed
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context(viewport=_DESKTOP_VIEWPORT)
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        await page.goto(f"{base}/help/run-and-read-analyses")

        # Three columns simultaneously visible at xl; the mobile disclosure
        # wrapper (the OTHER rendered copy of _nav.html) is hidden entirely.
        await expect(page.locator("[data-help-nav]:visible")).to_be_visible()
        await expect(page.locator("[data-help-toc]:visible")).to_be_visible()
        await expect(page.locator("article h1")).to_be_visible()
        await expect(page.locator("details.help-nav-shell")).to_be_hidden()

        # Scrollspy: scrolling a heading to the top of the viewport toggles
        # the active class onto ITS toc link (and off whichever link had it).
        # Both targets are chosen far enough from their neighboring headings
        # (>270px, the observer's rootMargin-shrunk top band at this viewport
        # height) that landing on one never ALSO pulls a neighbor into the
        # same intersection batch — that would make the neighbor "win" the
        # active class since the handler applies every isIntersecting entry
        # in a batch in order. The article's LAST heading is avoided too:
        # scrollIntoView on it can't reach block:'start' once the remaining
        # scrollable height is shorter than the viewport.
        first_target = "reading-the-outputs"
        second_target = "random-seed-and-reproducibility"

        await page.evaluate(
            f"document.getElementById('{first_target}').scrollIntoView({{block: 'start'}})"
        )
        first_link = page.locator(f"[data-toc-link='{first_target}']")
        await expect(first_link).to_have_class(re.compile(r"help-toc-link-active"))

        await page.evaluate(
            f"document.getElementById('{second_target}').scrollIntoView({{block: 'start'}})"
        )
        second_link = page.locator(f"[data-toc-link='{second_target}']")
        await expect(second_link).to_have_class(re.compile(r"help-toc-link-active"))
        await expect(first_link).not_to_have_class(re.compile(r"help-toc-link-active"))

        await context.close()
        await browser.close()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_boosted_navigation_preserves_page_chrome(migrated_server_url: str) -> None:
    """Arch-B1: a real hx-boost CLICK from /help to an article, then a
    next/prev card click, must never strip the sidebar/page chrome down to a
    bare drawer partial."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright, expect

    base = migrated_server_url
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except PlaywrightError as exc:  # browser binary not installed
            pytest.skip(
                "Playwright Chromium not installed "
                f"(run `uv run playwright install chromium`): {exc}"
            )
        context = await browser.new_context(viewport=_DESKTOP_VIEWPORT)
        page = await context.new_page()
        page.set_default_timeout(E2E_TIMEOUT_MS)

        await _bootstrap_admin_and_login(page, base)
        await page.goto(f"{base}/help")
        await expect(page.locator("#sidebar")).to_be_visible()

        # Real click (not page.goto) — exercises hx-boost's AJAX nav path.
        await page.click("a[href='/help/build-a-scenario']")
        await page.wait_for_url(f"{base}/help/build-a-scenario")
        await expect(page.locator("#sidebar")).to_be_visible()
        await expect(page.locator("article h1")).to_have_text("Build a scenario")

        # Next-card click.
        await page.click("a.help-pn[href='/help/run-and-read-analyses']")
        await page.wait_for_url(f"{base}/help/run-and-read-analyses")
        await expect(page.locator("#sidebar")).to_be_visible()
        await expect(page.locator("article h1")).to_have_text("Run & read analyses")

        # Prev-card click, back to build-a-scenario.
        await page.click("a.help-pn[href='/help/build-a-scenario']")
        await page.wait_for_url(f"{base}/help/build-a-scenario")
        await expect(page.locator("#sidebar")).to_be_visible()
        await expect(page.locator("article h1")).to_have_text("Build a scenario")

        await context.close()
        await browser.close()
