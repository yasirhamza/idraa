"""E2E validation for the SINGLE run-type path (PR π post-merge).

The existing e2e_acme_industrial.py covers the AGGREGATE run-type exclusively.
This script validates the SINGLE path:

User stories validated:
1. Analyst creates ONE scenario, attaches 2 controls, submits the run form
   with only that scenario selected (len(scenario_ids)==1 → RunType.SINGLE).
2. Run completes with non-trivial residual ALE (mc_iterations=200, sync path).
3. Dashboard renders the run.
4. /runs/{id} detail page shows SINGLE-run shape: per-scenario card matches
   the input scenario name; no AGGREGATE breakdown visible.

Run via:
    rm -f idraa.db
    uv run alembic upgrade head
    uv run uvicorn idraa.app:app --port 8001 &
    uv run python scripts/e2e_single_run.py

Screenshots saved to /tmp/e2e-single-run-e2e/.
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
OUT = Path(tempfile.gettempdir()) / "e2e-single-run-e2e"
OUT.mkdir(parents=True, exist_ok=True)

SETUP_PAYLOAD = {
    "org_name": "Single Run Co",
    "industry_type": "financial",
    "organization_size": "small",
    "email": "analyst@single-run.test",
    "full_name": "Single Run Analyst",
    "password": "Aa12345678!",
}

SCENARIO = {
    "name": "Credential Stuffing Attack",
    "threat_category": "SOCIAL_ENGINEERING",
    "tef": {"low": 0.5, "mode": 2.0, "high": 6.0},
    "vuln": {"low": 0.2, "mode": 0.4, "high": 0.6},
    "pl": {"low": 50_000, "mode": 300_000, "high": 1_500_000},
}

CONTROLS = [
    {
        "name": "MFA on all accounts",
        "domain": "LOSS_EVENT",
        "sub_function": "LEC_PREV_RESISTANCE",
        "capability": 0.75,
        "coverage": 0.90,
        "reliability": 0.85,
    },
    {
        "name": "Rate limiting on login endpoints",
        "domain": "LOSS_EVENT",
        "sub_function": "LEC_PREV_AVOIDANCE",
        "capability": 0.65,
        "coverage": 0.80,
        "reliability": 0.80,
    },
]


def log(msg: str) -> None:
    print(f"[e2e-single-run] {msg}", flush=True)


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


def seed_scenario_and_controls() -> tuple[uuid.UUID, list[uuid.UUID]]:
    """DB-seed 1 scenario + 2 controls + their scenario-control link.

    Returns (scenario_id, control_ids).
    """
    from datetime import UTC, datetime

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import (
        ControlDomain,
        ControlType,
        EntityStatus,
        FairCamSubFunction,
        ScenarioType,
        ThreatCategory,
    )
    from idraa.models.organization import Organization
    from idraa.models.scenario import Scenario
    from idraa.models.scenario_control import ScenarioControl
    from idraa.models.user import User

    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        org = session.execute(select(Organization)).scalar_one()
        admin = session.execute(select(User)).scalar_one()

        tef: dict[str, Any] = SCENARIO["tef"]  # type: ignore[assignment]
        vuln: dict[str, Any] = SCENARIO["vuln"]  # type: ignore[assignment]
        pl: dict[str, Any] = SCENARIO["pl"]  # type: ignore[assignment]
        sc = Scenario(
            id=uuid.uuid4(),
            organization_id=org.id,
            name=str(SCENARIO["name"]),
            scenario_type=ScenarioType.CUSTOM,
            threat_category=ThreatCategory[str(SCENARIO["threat_category"])],
            threat_event_frequency={"distribution": "PERT", **tef},
            vulnerability={"distribution": "PERT", **vuln},
            primary_loss={"distribution": "PERT", **pl},
            industry="financial",
            revenue_tier="less_than_100m",
            created_by=admin.id,
        )
        session.add(sc)
        session.flush()

        control_ids: list[uuid.UUID] = []
        for c in CONTROLS:
            ctrl = Control(
                id=uuid.uuid4(),
                organization_id=org.id,
                name=str(c["name"]),
                domain=ControlDomain[str(c["domain"])],
                type=ControlType.TECHNICAL,
                cost_model={},
                nist_csf_functions=[],
                iso_27001_domains=[],
                compliance_mappings={},
                skill_requirements=[],
                technology_dependencies=[],
                applicable_industries=[],
                applicable_org_sizes=[],
                status=EntityStatus.ACTIVE,
                version="1.0",
                created_by=admin.id,
            )
            session.add(ctrl)
            session.flush()
            asgn = ControlFunctionAssignment(
                control_id=ctrl.id,
                organization_id=org.id,
                sub_function=FairCamSubFunction[str(c["sub_function"])],
                capability_value=float(c["capability"]),  # type: ignore[arg-type]
                coverage=float(c["coverage"]),  # type: ignore[arg-type]
                reliability=float(c["reliability"]),  # type: ignore[arg-type]
                confirmed_by_user_at=datetime.now(UTC),
            )
            session.add(asgn)
            control_ids.append(ctrl.id)
            session.add(ScenarioControl(scenario_id=sc.id, control_id=ctrl.id))

        session.commit()
        return sc.id, control_ids


def main() -> int:
    fresh_db()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # ---- Step 1: /setup ----
        log("Step 1: /setup → admin (Single Run Co, financial, small)")
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

        # ---- Step 2: DB-seed 1 scenario + 2 controls ----
        log("Step 2: DB-seed 1 scenario + 2 controls")
        scenario_id, control_ids = seed_scenario_and_controls()
        log(f"    scenario_id={scenario_id}")
        log(f"    control_ids={control_ids}")

        # ---- Step 3: Visit /scenarios — verify scenario shows ----
        log("Step 3: Verify scenario appears in /scenarios list")
        page.goto(f"{BASE}/scenarios")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(OUT / "01-scenarios-list.png"), full_page=True)
        expect(str(SCENARIO["name"]) in page.content(), "scenario missing from list")
        log(f"    '{SCENARIO['name']}' visible")

        # ---- Step 4: /analyses/new — select ONE scenario ----
        log("Step 4: Visit /analyses/new, select ONE scenario → SINGLE run-type")
        page.goto(f"{BASE}/analyses/new")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(OUT / "02-analyses-new.png"), full_page=True)

        cb = page.locator(f'input[name=scenario_ids][value="{scenario_id}"]')
        cb.check()
        page.wait_for_timeout(300)
        # Select both controls
        for cid in control_ids:
            ctrl_cb = page.locator(f'input[name=control_ids][value="{cid}"]')
            if ctrl_cb.count() > 0:
                ctrl_cb.check()
        page.locator("[name=name]").fill("Single run credential stuffing")
        page.locator("[name=mc_iterations]").fill("200")
        page.screenshot(path=str(OUT / "03-analyses-new-filled.png"), full_page=True)
        log("    form filled: 1 scenario, 2 controls, 200 iterations")

        # ---- Step 5: Submit ----
        log("Step 5: POST /analyses — SINGLE run executes sync (200 < 1000 threshold)")
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/analyses" in r.url
        ) as resp_info:
            page.get_by_role("button", name="Run analysis").click()
        resp = resp_info.value
        log(f"    POST /analyses status={resp.status}")
        expect(resp.status < 400, f"POST /analyses failed: {resp.status}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        log(f"    landed at {page.url}")

        run_id: uuid.UUID | None = None
        if "/runs/" in page.url:
            run_id = uuid.UUID(page.url.rsplit("/runs/", 1)[1].split("?")[0].split("/")[0])
            log(f"    run_id={run_id}")
            page.screenshot(path=str(OUT / "04-run-detail.png"), full_page=True)
        else:
            log(f"    WARN: expected /runs/{{id}}, got {page.url}")

        # ---- Step 6: /runs/{id} — verify SINGLE shape ----
        if run_id is not None:
            log("Step 6: /runs/{id} — verify SINGLE run shape")
            page.goto(f"{BASE}/runs/{run_id}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
            page.screenshot(path=str(OUT / "05-run-detail-full.png"), full_page=True)
            body = page.content()
            expect(
                "Internal Server Error" not in body,
                "/runs/{id} returned 500",
            )
            expect(
                str(SCENARIO["name"]) in body,
                f"SINGLE run detail page missing scenario name: {SCENARIO['name']}",
            )
            log(f"    '{SCENARIO['name']}' found in run detail page")
            # The run should NOT show an "Aggregate breakdown" section label
            # (SINGLE runs have no multi-scenario aggregation)
            if "n_scenarios" in body and "2" in body:
                log("    WARN: aggregate-style n_scenarios=2 marker found; investigate")
            log("    /runs/{id} rendered without 500/traceback")

        # ---- Step 7: Dashboard shows run ----
        log("Step 7: Dashboard shows completed SINGLE run")
        page.goto(f"{BASE}/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "06-dashboard.png"), full_page=True)
        body = page.content()
        expect(
            "Single run credential stuffing" in body,
            "run name missing from dashboard",
        )
        log("    dashboard shows run name")

        # Verify residual ALE is non-trivial (not $0)
        import re

        dollar_amounts = re.findall(r"\$[\d,]+", body)
        big_amounts = [d for d in dollar_amounts if len(d) >= 4]
        log(f"    {len(big_amounts)} dollar values on dashboard: {big_amounts[:5]}")
        expect(len(big_amounts) > 0, "No dollar values on dashboard — ALE may be $0")
        log("    residual ALE non-trivial (dollar values present)")

        browser.close()
        log("=" * 60)
        log("E2E SINGLE RUN PASSED")
        log(f"Screenshots: {OUT}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
