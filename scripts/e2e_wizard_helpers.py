"""E2E validation for PR π wizard helpers: F7 IRIS pre-fill + F8 Apply-overlay.

User stories validated:
1. On first visit to wizard step 3, form is auto-pre-filled with IRIS industry
   baseline values (manufacturing / 1b_to_10b) — values are non-zero.
2. Analyst edits a value, clicks "Reset to industry baseline" button; form
   values revert to the IRIS baseline.
3. Analyst clicks "Apply: <overlay>" button; form values are multiplied by
   the overlay's frequency_multiplier (TEF) and magnitude_multiplier (PL, SL).
   Vulnerability is NOT multiplied.
4. Apply-overlay against an inactive overlay (is_active=False) returns 404.

Run via:
    rm -f idraa.db
    uv run alembic upgrade head
    uv run uvicorn idraa.app:app --port 8001 &
    uv run python scripts/e2e_wizard_helpers.py

Screenshots saved to /tmp/e2e-wizard-helpers-e2e/.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

BASE = "http://localhost:8001"
DB_PATH = Path(__file__).parent.parent / "idraa.db"
UV = "uv"
OUT = Path(tempfile.gettempdir()) / "e2e-wizard-helpers-e2e"
OUT.mkdir(parents=True, exist_ok=True)

SETUP_PAYLOAD = {
    "org_name": "Wizard Helpers Co",
    "industry_type": "manufacturing",
    "organization_size": "enterprise",
    "email": "analyst@wizard-helpers.test",
    "full_name": "Wizard Analyst",
    "password": "Aa12345678!",
}

# Overlay with known multipliers for deterministic assertion.
OVERLAY_FREQ_MULT = 1.5
OVERLAY_MAG_MULT = 2.0


def log(msg: str) -> None:
    print(f"[e2e-wizard-helpers] {msg}", flush=True)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def fresh_db() -> None:
    """Drop and recreate the DB via alembic upgrade head."""
    if DB_PATH.exists():
        os.remove(DB_PATH)
    result = subprocess.run(  # noqa: S603
        [UV, "run", "alembic", "upgrade", "head"],
        cwd=str(DB_PATH.parent),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stderr}")
    log("    fresh DB applied")


def seed_overlay(org_id: uuid.UUID, user_id: uuid.UUID, *, is_active: bool = True) -> uuid.UUID:
    """Seed an OverlayDefinition and return its id."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from idraa.models.overlay import OverlayDefinition

    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        overlay = OverlayDefinition(
            id=uuid.uuid4(),
            organization_id=org_id,
            tag="ransomware_surge",
            display_name="Ransomware Surge 2026",
            frequency_multiplier=OVERLAY_FREQ_MULT,
            magnitude_multiplier=OVERLAY_MAG_MULT,
            sources=["internal threat intel"],
            methodology=(
                "Based on Q1 2026 ransomware surge intelligence from ISAC. "
                "Frequency elevated 1.5x; magnitude elevated 2x for the quarter. "
                "Reviewed and approved by CISO on 2026-04-01."
            ),
            is_active=is_active,
        )
        session.add(overlay)
        session.commit()
        return overlay.id


def get_org_and_user() -> tuple[uuid.UUID, uuid.UUID]:
    """Read the org + admin user seeded by /setup."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from idraa.models.organization import Organization
    from idraa.models.user import User

    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        org = session.execute(select(Organization)).scalar_one()
        user = session.execute(select(User)).scalar_one()
        return org.id, user.id


def deactivate_overlay(overlay_id: uuid.UUID) -> None:
    """Set is_active=False on an overlay directly via ORM."""
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import Session

    from idraa.models.overlay import OverlayDefinition

    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        session.execute(
            update(OverlayDefinition)
            .where(OverlayDefinition.id == overlay_id)
            .values(is_active=False)
        )
        session.commit()


def _parse_form_values(page: Any, prefix: str) -> dict[str, str]:
    """Read low/mode/high input values for a given PERT prefix from the page."""
    result: dict[str, str] = {}
    for part in ("low", "mode", "high"):
        name = f"{prefix}_{part}"
        result[part] = page.locator(f'input[name="{name}"]').input_value()
    return result


def main() -> int:
    fresh_db()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # ---- Step 1: /setup ----
        log("Step 1: /setup → admin user (manufacturing / enterprise)")
        page.goto(f"{BASE}/")
        page.wait_for_load_state("networkidle")
        expect("/setup" in page.url, f"expected /setup, got {page.url}")
        page.locator("[name=org_name]").fill(SETUP_PAYLOAD["org_name"])
        page.locator("[name=industry_type]").select_option(SETUP_PAYLOAD["industry_type"])
        page.locator("[name=organization_size]").select_option(SETUP_PAYLOAD["organization_size"])
        page.locator("[name=email]").fill(SETUP_PAYLOAD["email"])
        page.locator("[name=full_name]").fill(SETUP_PAYLOAD["full_name"])
        page.locator("[name=password]").fill(SETUP_PAYLOAD["password"])
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/setup" in r.url
        ) as resp_info:
            page.get_by_role("button", name="Create and sign in").click()
        expect(resp_info.value.status < 400, f"setup POST failed: {resp_info.value.status}")
        page.wait_for_load_state("networkidle")
        log("    setup complete; logged in")

        # ---- Step 2: Seed overlay ----
        log("Step 2: Seed active overlay (freq_mult=1.5, mag_mult=2.0)")
        org_id, user_id = get_org_and_user()
        overlay_id = seed_overlay(org_id, user_id, is_active=True)
        log(f"    overlay_id={overlay_id}")

        # ---- Step 3: Wizard step 1 → skip library → step 2 ----
        log("Step 3: Navigate wizard step 1 → skip library")
        page.goto(f"{BASE}/scenarios/new/wizard")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(OUT / "01-wizard-step1.png"), full_page=True)
        # Click the "Skip — start blank" submit button (name=skip_library).
        # Base template has hx-boost=true; use wait_for_url to handle the
        # HTMX-driven URL change (page.url after networkidle is unreliable).
        page.locator("button[name=skip_library]").click()
        page.wait_for_url("**/wizard/step/2**", timeout=10000)

        # Extract tx from URL
        tx = page.url.split("tx=")[-1].split("&")[0] if "tx=" in page.url else ""
        log(f"    tx={tx!r}")

        # ---- Step 4: Fill step 2 (name, industry, revenue_tier) ----
        log("Step 4: Fill step 2 — name + industry=manufacturing / revenue_tier=1b_to_10b")
        page.locator("[name=name]").fill("IRIS Pre-fill Test Scenario")
        page.locator("[name=threat_category]").select_option("ransomware")
        page.locator("[name=industry]").select_option("manufacturing")
        page.locator("[name=revenue_tier]").select_option("1b_to_10b")
        page.screenshot(path=str(OUT / "02-wizard-step2-filled.png"), full_page=True)
        page.get_by_role("button", name="Next").click()
        page.wait_for_url("**/wizard/step/3**", timeout=10000)
        log("    advanced to step 3")

        # ---- Step 5: Verify auto-pre-fill on step 3 ----
        log("Step 5: Verify IRIS auto-pre-fill on step 3 first visit")
        page.screenshot(path=str(OUT / "03-wizard-step3-auto-prefill.png"), full_page=True)
        tef_initial = _parse_form_values(page, "tef")
        vuln_initial = _parse_form_values(page, "vuln")
        pl_initial = _parse_form_values(page, "pl")
        log(f"    tef initial={tef_initial}")
        log(f"    vuln initial={vuln_initial}")
        log(f"    pl initial={pl_initial}")
        expect(
            float(tef_initial.get("mode", "0")) > 0,
            f"IRIS auto-pre-fill failed: tef.mode is 0 or blank. tef={tef_initial}",
        )
        expect(
            float(pl_initial.get("mode", "0")) > 0,
            f"IRIS auto-pre-fill failed: pl.mode is 0 or blank. pl={pl_initial}",
        )
        log("    IRIS auto-pre-fill verified (tef.mode and pl.mode > 0)")

        # ---- Step 6: Edit a value then Reset to baseline ----
        log("Step 6: Edit tef_mode, then click Reset to industry baseline")
        tef_mode_input = page.locator('input[name="tef_mode"]')
        original_tef_mode = tef_initial["mode"]
        tef_mode_input.fill("999.999")
        page.screenshot(path=str(OUT / "04-wizard-step3-edited.png"), full_page=True)

        # Click the Reset button (HTMX POST)
        reset_btn = page.locator("button", has_text="Reset to industry baseline")
        expect(reset_btn.is_visible(), "Reset to industry baseline button not visible")
        reset_btn.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)  # HTMX swap settle
        page.screenshot(path=str(OUT / "05-wizard-step3-after-reset.png"), full_page=True)

        tef_after_reset = _parse_form_values(page, "tef")
        log(f"    tef after reset={tef_after_reset}")
        expect(
            tef_after_reset["mode"] != "999.999",
            "Reset button did not revert tef_mode — still shows 999.999",
        )
        expect(
            abs(float(tef_after_reset["mode"]) - float(original_tef_mode)) < 0.01,
            f"Reset did not restore original value. Expected ~{original_tef_mode}, "
            f"got {tef_after_reset['mode']}",
        )
        log(f"    tef_mode restored to {tef_after_reset['mode']} (matches IRIS baseline)")

        # ---- Step 7: Apply overlay — verify multipliers applied ----
        log(f"Step 7: Apply overlay (freq_mult={OVERLAY_FREQ_MULT}, mag_mult={OVERLAY_MAG_MULT})")
        tef_before_overlay = _parse_form_values(page, "tef")
        vuln_before_overlay = _parse_form_values(page, "vuln")
        pl_before_overlay = _parse_form_values(page, "pl")

        apply_btn = page.locator("button", has_text="Apply: Ransomware Surge 2026")
        expect(apply_btn.is_visible(), "Apply overlay button not visible")
        apply_btn.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "06-wizard-step3-after-overlay.png"), full_page=True)

        tef_after_overlay = _parse_form_values(page, "tef")
        vuln_after_overlay = _parse_form_values(page, "vuln")
        pl_after_overlay = _parse_form_values(page, "pl")
        log(f"    tef after overlay={tef_after_overlay}")
        log(f"    vuln after overlay={vuln_after_overlay}")
        log(f"    pl after overlay={pl_after_overlay}")

        # TEF should be multiplied by freq_mult
        expected_tef_mode = float(tef_before_overlay["mode"]) * OVERLAY_FREQ_MULT
        actual_tef_mode = float(tef_after_overlay["mode"])
        expect(
            abs(actual_tef_mode - expected_tef_mode) < 0.01,
            f"TEF not multiplied by freq_mult={OVERLAY_FREQ_MULT}: "
            f"expected {expected_tef_mode:.4f}, got {actual_tef_mode:.4f}",
        )
        log(f"    TEF.mode multiplied correctly: {expected_tef_mode:.4f}")

        # PL should be multiplied by mag_mult
        expected_pl_mode = float(pl_before_overlay["mode"]) * OVERLAY_MAG_MULT
        actual_pl_mode = float(pl_after_overlay["mode"])
        expect(
            abs(actual_pl_mode - expected_pl_mode) < 0.01,
            f"PL not multiplied by mag_mult={OVERLAY_MAG_MULT}: "
            f"expected {expected_pl_mode:.4f}, got {actual_pl_mode:.4f}",
        )
        log(f"    PL.mode multiplied correctly: {expected_pl_mode:.4f}")

        # Vulnerability should NOT change
        expect(
            vuln_after_overlay["mode"] == vuln_before_overlay["mode"],
            f"Vulnerability was changed by overlay (should be immutable): "
            f"before={vuln_before_overlay['mode']!r} after={vuln_after_overlay['mode']!r}",
        )
        log("    Vulnerability unchanged (correct — not multiplied by overlay)")

        # ---- Step 8: Deactivate overlay and confirm 404 ----
        log("Step 8: Deactivate overlay in DB, attempt apply → expect 404")
        deactivate_overlay(overlay_id)
        log(f"    overlay {overlay_id} deactivated")

        # POST directly via httpx to the apply-overlay endpoint with the inactive overlay
        import httpx

        # Grab CSRF token from current page cookies
        cookies = {c["name"]: c["value"] for c in context.cookies()}
        csrf_token = cookies.get("csrftoken", "")
        if not csrf_token:
            # Try extracting from cookie named differently
            for name, val in cookies.items():
                if "csrf" in name.lower():
                    csrf_token = val
                    break

        # Get the tx from current URL
        current_tx = page.url.split("tx=")[-1].split("&")[0] if "tx=" in page.url else tx
        apply_resp = httpx.post(
            f"{BASE}/scenarios/wizard/apply-overlay",
            data={
                "tx": current_tx,
                "overlay_id": str(overlay_id),
                "tef_low": "0.1",
                "tef_mode": "0.5",
                "tef_high": "1.0",
                "_csrf": csrf_token,
            },
            cookies=cookies,
            follow_redirects=False,
        )
        log(f"    apply-overlay with inactive overlay: status={apply_resp.status_code}")
        expect(
            apply_resp.status_code == 404,
            f"Expected 404 for inactive overlay, got {apply_resp.status_code}",
        )
        log("    inactive overlay returns 404 (correct)")

        browser.close()
        log("=" * 60)
        log("E2E WIZARD HELPERS PASSED")
        log(f"Screenshots: {OUT}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
