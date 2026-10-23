"""E2E: the VMC / DSC tags (issue #268, spec §3.6) must not overflow the 390px controls
list or the control detail header.

The tags sit beside the control name on the controls list (desktop link cell + mobile
card title), on the control detail header, and on library entry cards. A long control
name carrying BOTH tags is the worst case for a phone: the pair must wrap with the name
(``inline-flex flex-wrap``), never widen the card or the page.

Run by hand (like the other ``@pytest.mark.e2e`` tests, this is NOT in the merge path):
``uv run pytest tests/e2e/test_controls_list_mobile_e2e.py -m e2e -q``.

Harness mirrors ``tests/e2e/test_controls_combobox_focus_mobile_e2e.py``: ephemeral
per-run SQLite migrated to ``head``, a uvicorn subprocess bound via ``DATABASE_URL``, and
a ``try/except PlaywrightError -> pytest.skip`` guard so this SKIPS cleanly where
Chromium is not installed. Controls are seeded through the ORM (a synchronous engine on
the same SQLite file), never raw INSERTs, so ids use the ORM's own UUID storage.
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
from decimal import Decimal

import httpx
import pytest

from tests.e2e.conftest import E2E_TIMEOUT_MS

_ADMIN_EMAIL = "admin@e2e.local"
_ADMIN_PASSWORD = "E2e-passw0rd!"  # test-local credential

# Phone viewport — iPhone 12/13/14 logical width.
_MOBILE_VIEWPORT = {"width": 390, "height": 844}

_LONG_NAME = (
    "Enterprise-wide continuous monitoring and decision-quality assurance programme "
    "for third-party operational technology suppliers"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def migrated_server() -> Iterator[tuple[str, str]]:
    """``(base_url, sqlite_path)``: ephemeral SQLite migrated to head + a uvicorn on it."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="rf_e2e_")
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
        yield url, db_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        with contextlib.suppress(OSError):
            os.unlink(db_path)


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


def _seed_tagged_controls(db_path: str) -> str:
    """Insert the worst-case control (long name, VMC + DSC) and a plain one through the
    ORM on a synchronous engine over the server's SQLite file. Returns the tagged id."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction
    from idraa.models.organization import Organization

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with Session(engine) as session:
            org_id = session.execute(select(Organization.id)).scalars().one()

            def _control(name: str, sfs: list[FairCamSubFunction]) -> Control:
                ctrl = Control(
                    organization_id=org_id,
                    name=name,
                    type=ControlType.TECHNICAL,
                    annual_cost=Decimal("0"),
                    status=EntityStatus.ACTIVE,
                    version="1.0",
                )
                session.add(ctrl)
                session.flush()
                for sf in sfs:
                    session.add(
                        ControlFunctionAssignment(
                            organization_id=org_id,
                            control_id=ctrl.id,
                            sub_function=sf,
                            capability_value=0.8,
                            coverage=0.85,
                            reliability=0.9,
                        )
                    )
                session.flush()
                return ctrl

            tagged = _control(
                _LONG_NAME,
                [
                    FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
                    FairCamSubFunction.DSC_ID_MISALIGNED,
                ],
            )
            _control("Plain endpoint control", [FairCamSubFunction.LEC_PREV_RESISTANCE])
            session.commit()
            return str(tagged.id)
    finally:
        engine.dispose()


_OVERFLOW_JS = (
    "() => ({ scroll: document.documentElement.scrollWidth,"
    " client: document.documentElement.clientWidth })"
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_meta_tags_fit_390px_controls_list_and_detail(
    migrated_server: tuple[str, str],
) -> None:
    """A long-named control carrying both tags: on the controls list and on its detail
    header at 390px, both tags are visible, inside the viewport, and the page does not
    scroll horizontally."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    base, db_path = migrated_server
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

        # 1. Bootstrap the first admin (creates the org), then seed the controls.
        await _bootstrap_admin_and_login(page, base)
        tagged_id = _seed_tagged_controls(db_path)

        async def _assert_tags_fit(where: str) -> None:
            for code in ("VMC", "DSC"):
                # `:visible` skips the `hidden md:block` desktop table copy.
                tag = page.locator(f"[data-meta-tag='{code}']:visible").first
                await tag.wait_for(state="visible")
                box = await tag.bounding_box()
                assert box is not None, f"{where}: {code} tag has no box"
                assert box["x"] >= 0, f"{where}: {code} tag starts left of the viewport"
                assert box["x"] + box["width"] <= _MOBILE_VIEWPORT["width"], (
                    f"{where}: {code} tag spills past the 390px viewport: {box!r}"
                )
            dims = await page.evaluate(_OVERFLOW_JS)
            assert dims["scroll"] <= dims["client"], (
                f"{where}: page scrolls horizontally at 390px: {dims!r}"
            )

        # 2. Controls list (mobile card stack): the tagged card's title carries both tags.
        await page.goto(f"{base}/controls")
        await page.wait_for_selector("[data-meta-tag='VMC']:visible")
        await _assert_tags_fit("controls list")
        # The plain control stays untagged: exactly one VMC and one DSC tag are visible.
        assert await page.locator("[data-meta-tag='VMC']:visible").count() == 1
        assert await page.locator("[data-meta-tag='DSC']:visible").count() == 1

        # 3. Control detail header: tags under the (wrapping) long name.
        await page.goto(f"{base}/controls/{tagged_id}")
        await page.wait_for_selector("[data-meta-tag='VMC']:visible")
        await _assert_tags_fit("control detail header")
        # dsc_id_misaligned is this control's only DSC slug → the DSC tag is muted.
        dsc_class = await page.locator("[data-meta-tag='DSC']:visible").first.get_attribute("class")
        assert dsc_class is not None and "badge-warning" in dsc_class

        await context.close()
        await browser.close()
