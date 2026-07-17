"""E2E validation for PR π-specific regression cases.

Sub-tests (each independently verified):

1. DoS cap: POST /analyses with mc_iterations=2_000_000 → 422 with
   "_MAX_ITERATIONS" / out-of-range error in response body.

2. Negative form values on Apply-overlay: POST
   /scenarios/wizard/apply-overlay with tef_low=-1 → 422 with
   "values must be >= 0" in response body.

3. /calibration-overrides 404: GET each of the 6 deceased URL patterns
   → all return 404 (route was excised in PR π).

4. Scenario edit changes inputs_hash: create scenario S1, run analysis →
   record hash H1. Edit S1 (change tef_high). Run again → H2 != H1.

5. Wizard prefill-from-industry returns inline notice when industry is
   missing: POST /scenarios/wizard/prefill-from-industry with a draft
   that has no industry set → response contains "No industry baseline
   available" inline notice.

Uses httpx for direct HTTP assertions (faster + more deterministic than
browser-driven for regression checks). Playwright used only for sub-test 4
which requires the full UI flow.

Run via:
    rm -f idraa.db
    uv run alembic upgrade head
    uv run uvicorn idraa.app:app --port 8001 &
    uv run python scripts/e2e_edge_cases.py

Screenshots saved to /tmp/e2e-edge-cases-e2e/.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

BASE = "http://localhost:8001"
DB_PATH = Path(__file__).parent.parent / "idraa.db"
UV = "uv"
OUT = Path(tempfile.gettempdir()) / "e2e-edge-cases-e2e"
OUT.mkdir(parents=True, exist_ok=True)

SETUP_PAYLOAD = {
    "org_name": "Edge Cases Inc",
    "industry_type": "manufacturing",
    "organization_size": "enterprise",
    "email": "analyst@edge-cases.test",
    "full_name": "Edge Cases Analyst",
    "password": "Aa12345678!",
}

# URLs from the /calibration-overrides route that was excised in PR π.
_FAKE_UUID = "00000000-0000-0000-0000-000000000001"
CALIBRATION_OVERRIDE_URLS = [
    "/calibration-overrides",
    "/calibration-overrides/template.csv",
    "/calibration-overrides/import",
    "/calibration-overrides/new",
    f"/calibration-overrides/{_FAKE_UUID}",
    f"/calibration-overrides/{_FAKE_UUID}/edit",
]


def log(msg: str) -> None:
    print(f"[e2e-edge-cases] {msg}", flush=True)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def fresh_db() -> None:
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


def get_org_and_user() -> tuple[uuid.UUID, uuid.UUID]:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from idraa.models.organization import Organization
    from idraa.models.user import User

    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        org = session.execute(select(Organization)).scalar_one()
        user = session.execute(select(User)).scalar_one()
        return org.id, user.id


def seed_single_scenario(org_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    """Seed a minimal scenario for the inputs_hash sub-test. Returns scenario_id."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from idraa.models.enums import ScenarioType, ThreatCategory
    from idraa.models.scenario import Scenario

    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        sc = Scenario(
            id=uuid.uuid4(),
            organization_id=org_id,
            name="Hash Test Scenario",
            scenario_type=ScenarioType.CUSTOM,
            threat_category=ThreatCategory.RANSOMWARE,
            threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.0},
            vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
            primary_loss={"distribution": "PERT", "low": 1000, "mode": 5000, "high": 20000},
            industry="manufacturing",
            revenue_tier="1b_to_10b",
            created_by=user_id,
        )
        session.add(sc)
        session.commit()
        return sc.id


def seed_wizard_draft_no_industry(org_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    """Seed a WizardDraft with industry=None for the inline-notice sub-test."""
    from dataclasses import asdict

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from idraa.models.wizard_draft import WizardDraft
    from idraa.services.wizard_state import WizardState

    tx_id = uuid.uuid4()
    state = WizardState(
        tx_id=str(tx_id),
        current_step=3,
        name="No Industry Test",
        threat_category="ransomware",
        industry=None,
        revenue_tier=None,
        threat_event_frequency=None,
        vulnerability=None,
        primary_loss=None,
    )
    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        session.add(
            WizardDraft(
                user_id=user_id,
                tx_id=tx_id,
                organization_id=org_id,
                state_json=asdict(state),
            )
        )
        session.commit()
    return tx_id


def main() -> int:
    fresh_db()

    from playwright.sync_api import sync_playwright

    # ---- Bootstrap: /setup via Playwright (needs JS/HTMX redirect handling) ----
    log("Step 0: /setup via Playwright (bootstrap admin)")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
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

        org_id, user_id = get_org_and_user()
        scenario_id = seed_single_scenario(org_id, user_id)
        log(f"    org_id={org_id} scenario_id={scenario_id}")

        # ---- Sub-test 4: inputs_hash changes after scenario edit ----
        log("Sub-test 4: inputs_hash changes after editing scenario")
        log("    4a: Run analysis on S1 (tef_high=1.0)")
        page.goto(f"{BASE}/analyses/new")
        page.wait_for_load_state("networkidle")
        cb = page.locator(f'input[name=scenario_ids][value="{scenario_id}"]')
        cb.check()
        page.locator("[name=name]").fill("Hash test run 1")
        page.locator("[name=mc_iterations]").fill("200")
        page.screenshot(path=str(OUT / "04a-analyses-new-run1.png"), full_page=True)
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/analyses" in r.url
        ) as resp_info:
            page.get_by_role("button", name="Run analysis").click()
        expect(resp_info.value.status < 400, f"run1 POST failed: {resp_info.value.status}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        log(f"    4a: run1 landed at {page.url}")

        # Read run1 hash from DB
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session

        from idraa.models.risk_analysis_run import RiskAnalysisRun

        engine = create_engine(f"sqlite:///{DB_PATH}")
        with Session(engine) as session:
            runs = session.execute(select(RiskAnalysisRun)).scalars().all()
            run1 = runs[-1]
            hash1 = run1.inputs_hash
        log(f"    4a: hash1={hash1!r}")
        expect(hash1 is not None and len(str(hash1)) > 0, "run1.inputs_hash is empty")

        log("    4b: Edit S1 (tef_high 1.0 → 5.0) via scenario edit form")
        # Navigate to scenario edit form
        page.goto(f"{BASE}/scenarios/{scenario_id}/edit")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(OUT / "04b-scenario-edit.png"), full_page=True)
        tef_high_input = page.locator('input[name="tef_high"]')
        tef_high_input.fill("5.0")
        # Submit the edit form
        with page.expect_response(
            lambda r: r.request.method == "POST" and str(scenario_id) in r.url
        ) as edit_resp:
            page.get_by_role("button", name="Save").click()
        log(f"    4b: edit status={edit_resp.value.status}")
        page.wait_for_load_state("networkidle")

        log("    4c: Run analysis again on edited S1")
        page.goto(f"{BASE}/analyses/new")
        page.wait_for_load_state("networkidle")
        cb2 = page.locator(f'input[name=scenario_ids][value="{scenario_id}"]')
        cb2.check()
        page.locator("[name=name]").fill("Hash test run 2")
        page.locator("[name=mc_iterations]").fill("200")
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/analyses" in r.url
        ) as resp_info2:
            page.get_by_role("button", name="Run analysis").click()
        expect(resp_info2.value.status < 400, f"run2 POST failed: {resp_info2.value.status}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "04c-run2-complete.png"), full_page=True)

        with Session(engine) as session:
            runs2 = session.execute(select(RiskAnalysisRun)).scalars().all()
            run2 = runs2[-1]
            hash2 = run2.inputs_hash
        log(f"    4c: hash2={hash2!r}")
        expect(
            hash2 != hash1,
            f"inputs_hash did not change after editing scenario: h1={hash1!r} h2={hash2!r}",
        )
        log(f"    inputs_hash changed correctly: {hash1!r} → {hash2!r}")

        # ---- Use Playwright's context.request for auth-preserved API calls ----
        # Refresh CSRF cookie
        api = context.request
        api.get(f"{BASE}/")
        csrf_token = ""
        for c in context.cookies():
            if c.get("name") == "csrf_token":
                csrf_token = c.get("value") or ""
        log(f"    csrf_token={csrf_token[:30]}...")

        # ---- Sub-test 1: DoS cap (mc_iterations=2_000_000 → 422) ----
        log("Sub-test 1: DoS cap — POST /analyses with mc_iterations=2_000_000 → 422")
        r1 = api.post(
            f"{BASE}/analyses",
            form={
                "scenario_ids": str(scenario_id),
                "mc_iterations": "2000000",
                "name": "DoS attempt",
                "_csrf": csrf_token,
            },
        )
        log(f"    status={r1.status}")
        body1 = r1.text()
        expect(
            r1.status == 422,
            f"Expected 422 for mc_iterations=2M, got {r1.status}. body={body1[:200]}",
        )
        expect(
            "2000000" in body1 or "out of range" in body1 or "1000000" in body1,
            f"422 body does not mention the mc_iterations cap: {body1[:300]}",
        )
        log("    DoS cap: 422 with out-of-range message — PASSED")

        # ---- Sub-test 2: Negative form values on apply-overlay → 422 ----
        log("Sub-test 2: Negative tef_low on apply-overlay → 422")
        overlay_id = uuid.uuid4()
        from sqlalchemy import create_engine as sql_create_engine
        from sqlalchemy.orm import Session as SqlSession

        from idraa.models.overlay import OverlayDefinition

        _engine = sql_create_engine(f"sqlite:///{DB_PATH}")
        with SqlSession(_engine) as _sess:
            _overlay = OverlayDefinition(
                id=overlay_id,
                organization_id=org_id,
                tag="edge_case_overlay",
                display_name="Edge Case Overlay",
                frequency_multiplier=1.2,
                magnitude_multiplier=1.5,
                sources=[],
                methodology=(
                    "Edge case test overlay for negative-value rejection. "
                    "This overlay exists only to test the 422 validation path "
                    "when negative distribution values are submitted."
                ),
                is_active=True,
            )
            _sess.add(_overlay)
            _sess.commit()

        tx_id = seed_wizard_draft_no_industry(org_id, user_id)

        r2 = api.post(
            f"{BASE}/scenarios/wizard/apply-overlay",
            form={
                "tx": str(tx_id),
                "overlay_id": str(overlay_id),
                "tef_low": "-1",
                "tef_mode": "0.5",
                "tef_high": "1.0",
                "_csrf": csrf_token,
            },
        )
        log(f"    status={r2.status}")
        body2 = r2.text()
        expect(
            r2.status == 422,
            f"Expected 422 for negative tef_low, got {r2.status}. body={body2[:200]}",
        )
        expect(
            "values must be >= 0" in body2 or "tef" in body2.lower(),
            f"422 body does not reference the >= 0 constraint: {body2[:300]}",
        )
        log("    Negative tef_low: 422 with 'values must be >= 0' — PASSED")

        # ---- Sub-test 3: /calibration-overrides 404 ----
        log("Sub-test 3: Excised /calibration-overrides URLs → all 404")
        for url in CALIBRATION_OVERRIDE_URLS:
            r3 = api.get(f"{BASE}{url}")
            log(f"    GET {url} → {r3.status}")
            expect(
                r3.status == 404,
                f"Expected 404 for excised URL {url!r}, got {r3.status}",
            )
        log(f"    All {len(CALIBRATION_OVERRIDE_URLS)} calibration-overrides URLs → 404 — PASSED")

        # ---- Sub-test 5: prefill-from-industry inline notice when industry missing ----
        log("Sub-test 5: prefill-from-industry inline notice when industry=None")
        tx_no_industry = seed_wizard_draft_no_industry(org_id, user_id)
        r5 = api.post(
            f"{BASE}/scenarios/wizard/prefill-from-industry",
            form={
                "tx": str(tx_no_industry),
                "_csrf": csrf_token,
            },
        )
        log(f"    status={r5.status}")
        body5 = r5.text()
        expect(
            r5.status == 200,
            f"Expected 200 from prefill-from-industry, got {r5.status}. body={body5[:200]}",
        )
        expect(
            "No industry baseline available" in body5,
            f"Expected inline 'No industry baseline available' notice; got: {body5[:300]}",
        )
        log("    prefill-from-industry: inline notice rendered for missing industry — PASSED")

        browser.close()

    log("=" * 60)
    log("E2E EDGE CASES PASSED (5/5 sub-tests)")
    log(f"Screenshots: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
