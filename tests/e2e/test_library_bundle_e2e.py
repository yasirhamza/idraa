"""E2E (deterministic): admin bundle import/export round-trip journey (P3).

User-simulated Playwright test for the P3 scenario-library bundle import +
export feature. Mirrors the harness of ``test_library_extension_e2e.py`` EXACTLY:

    1. An ephemeral per-run SQLite file is migrated to ``head`` via
       ``alembic upgrade head`` in a subprocess (real migration chain incl. the
       ``source`` provenance column + the additive seed migration).
    2. uvicorn is launched bound to that ephemeral DB via ``DATABASE_URL`` env.
    3. The Playwright browser bootstraps the first admin via GET /setup -> POST
       /setup (creates org + admin, sets ``idraa_session``, 303 -> /), then drives
       the real export / import / preview / confirm / provenance-badge surfaces.

Journey:
    1. admin logs in (bootstrap on a fresh DB).
    2. GET /library/export returns a JSON array (asserted via a direct httpx GET
       carrying the browser's session cookie — the export route is a download
       attachment, so a direct request is simpler than navigating + reading a
       download body).
    3. admin opens /library/import and uploads a one-entry bundle with a NEW
       slug via ``set_input_files`` (in-memory JSON buffer on the real
       ``input[name='file']`` selector).
    4. the preview renders 1 "add" (the green ``add`` badge + the confirm button
       reading "Confirm import (1 entry)"). The upload POST re-renders the preview
       IN PLACE at the same ``/library/import`` URL (a 200, no redirect), so the
       test blocks on ``expect_navigation`` + the preview-only confirm button —
       NOT a no-op ``wait_for_url`` against the unchanged URL.
    5. admin submits the confirm form (303 -> /library; blocked on via
       ``expect_navigation`` before the verification ``goto``).
    6. the new entry appears in /library filtered to ``source=imported`` badged
       "Imported" (the P3 provenance badge from ``_entry_card.html``).

Browser-availability guard: ``chromium.launch()`` is wrapped so that, where the
Playwright Chromium binary is not installed, the test SKIPS cleanly rather than
hard-failing. The ``e2e`` marker deselects this module from the default
``uv run pytest`` hot loop.
"""

from __future__ import annotations

import contextlib
import json
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

# A NEW slug/name that does NOT collide with any seeded entry — so the import
# resolves to exactly one "add" (not a "skip"). The name is distinctive so the
# /library search filter (?q=) isolates it for the badge assertion.
_NEW_SLUG = "e2e-imported-scenario"
_NEW_NAME = "E2E Imported Bundle Scenario"


def _one_entry_bundle() -> bytes:
    """A single valid LibraryEntrySeed-shaped entry with a fresh slug/name.

    Mirrors ``generate_template_json``'s known-valid shape (round-trips through
    ``validate_upload`` -> ``apply_validated_preview``) but with a unique
    ``slug``/``name`` so it imports as exactly one "add".
    """
    entry = {
        "slug": _NEW_SLUG,
        "name": _NEW_NAME,
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
        "attack_vector": "phishing_then_lateral_movement",
        "tags": ["e2e"],
        "description": (
            "An end-to-end imported scenario used by the P3 bundle-import "
            "Playwright journey to prove the add path."
        ),
        "example_incidents": None,
        "source_citations": [],
        "canonical_fair_gap": (
            "Exercises the imported-provenance path end to end through the "
            "two-step bundle importer."
        ),
        "applicable_industries": None,
        "applicable_sub_sectors": None,
        "applicable_org_sizes": None,
        "threat_event_frequency": {
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2,
        },
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
        "primary_loss": {
            "distribution": "PERT",
            "low": 100000,
            "mode": 1000000,
            "high": 15000000,
        },
        "secondary_loss": {
            "distribution": "PERT",
            "low": 50000,
            "mode": 500000,
            "high": 5000000,
        },
        "suggested_control_ids": [],
        "standards_references": None,
        "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
    }
    return json.dumps([entry], indent=2).encode("utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server_url() -> Iterator[str]:
    """Ephemeral SQLite migrated to head + uvicorn bound to it.

    Runs the real migration chain (incl. the P3 ``source`` column + additive
    seed) so the library is populated and the provenance column exists. Yields
    the base URL; tears down the process + file.
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
    """Authenticate the Playwright browser, bootstrapping the first admin if needed.

    Branches on the actual presence of the bootstrap form (``migrated_server_url``
    is module-scoped — the first caller bootstraps, later callers log in).
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


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_admin_exports_then_imports_bundle_and_sees_imported_badge(
    migrated_server_url: str,
) -> None:
    """Real admin journey: export JSON array -> upload new-slug bundle ->
    preview 1 add -> confirm -> entry shows in /library badged "Imported".
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
        page.set_default_timeout(E2E_TIMEOUT_MS)

        # 1. Bootstrap first admin + login.
        await _bootstrap_admin_and_login(page, base)

        # 2. GET /library/export returns a JSON array. The export route is a
        #    download attachment, so assert via a direct httpx GET carrying the
        #    browser's session cookie (simpler than reading a download body).
        cookies = await context.cookies()
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        resp = httpx.get(
            f"{base}/library/export",
            headers={"Cookie": cookie_header},
            timeout=10,
        )
        assert resp.status_code == 200, f"/library/export returned {resp.status_code}"
        exported = resp.json()
        assert isinstance(exported, list), "/library/export must return a JSON array"

        # 3. Open the import form and upload a one-entry bundle with a NEW slug
        #    via the real input[name='file'] selector (in-memory JSON buffer).
        await page.goto(f"{base}/library/import")
        await page.locator("input[name='file']").set_input_files(
            files=[
                {
                    "name": "e2e_bundle.json",
                    "mimeType": "application/json",
                    "buffer": _one_entry_bundle(),
                }
            ]
        )
        # The upload POST re-renders import_preview.html IN PLACE at the SAME
        # URL (/library/import, a full-page 200 — no redirect). A wait_for_url
        # against the unchanged URL returns immediately and would capture the
        # pre-submit upload form, so wrap the click in expect_navigation to
        # block on the actual document load of the preview render.
        async with page.expect_navigation():
            await page.click("button[type='submit']")

        # 4. Preview renders exactly 1 "add". Wait on a preview-specific
        #    selector (the singular confirm button only exists on
        #    import_preview.html, never on the upload form) before snapshotting.
        await page.get_by_role("button", name="Confirm import (1 entry)").wait_for(state="visible")
        preview = await page.content()
        assert _NEW_NAME in preview, "preview should list the new entry's name"
        assert _NEW_SLUG in preview, "preview should list the new entry's slug"
        # The green add badge + the confirm-button singular copy both prove a
        # single add action.
        assert "badge-success" in preview, "preview should render the green 'add' badge"
        assert "Confirm import (1 entry)" in preview, (
            "confirm button should read 'Confirm import (1 entry)' for a single add"
        )

        # 5. Submit the confirm form. The confirm POST 303-redirects to
        #    /library on success, so block on that navigation (rather than
        #    letting the subsequent goto race the in-flight redirect, which
        #    masked the same defect as the upload step).
        async with page.expect_navigation(url=f"{base}/library"):
            await page.click("button[type='submit']")

        # 6. The new entry appears in /library badged "Imported". Filter to the
        #    imported provenance + search the distinctive name to isolate it.
        await page.goto(f"{base}/library?source=imported&q={_NEW_NAME.replace(' ', '+')}")
        listing = await page.content()
        assert _NEW_NAME in listing, "imported entry should appear in the filtered library"
        # The P3 provenance badge renders 'Imported' for source == 'imported'.
        card = page.locator(".card").filter(has_text=_NEW_NAME)
        await card.first.wait_for(state="visible")
        badge = card.locator("span.badge-info", has_text="Imported")
        assert await badge.count() > 0, (
            "the imported entry's card must carry the 'Imported' provenance badge"
        )

        await browser.close()
