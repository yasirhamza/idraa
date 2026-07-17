"""V2-snapshot read surfaces: banner + structured log (issue #131 T6.5).

Covers:
- Test D (Sec2-I1): runs/detail.html renders the "Pre-#131 snapshot" banner
  when ``run.controls_snapshot`` carries any V2 envelope.
- Test E (Sec3-I1 + CR4-B1): the run-detail route emits a structured
  ``snapshot_v2_read`` log entry with ``reclassified_sub_functions`` in
  ``extra={}`` whenever a V2 snapshot is surfaced to a user.
- Negative: V3-only snapshot triggers neither the banner nor the log.

Mirrors the fixture/topology of tests/integration/test_run_routes.py.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.scenario import Scenario
from idraa.models.user import User

_DETAIL_TEMPLATE = Path("src/idraa/templates/runs/detail.html")

_BANNER_TEXT = "Pre-#131 snapshot"


async def _seed_analyst_org_scenario(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str = "v2-banner test scenario",
) -> Scenario:
    """Build a minimal schema-valid Scenario in the given org and commit."""
    scenario = Scenario(
        organization_id=organization_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario


def _minimal_completed_simulation_results() -> dict[str, Any]:
    """A minimal simulation_results payload sufficient for the detail-page render."""
    rng = np.random.default_rng(seed=42)
    samples = rng.lognormal(mean=10.0, sigma=0.5, size=200).tolist()
    ale = float(np.mean(samples))
    return {
        "base_risk": {
            "annualized_loss_expectancy": ale,
            "mean": ale,
            "median": float(np.median(samples)),
            "std_deviation": float(np.std(samples)),
            "var_95": float(np.percentile(samples, 95)),
            "var_99": float(np.percentile(samples, 99)),
            "loss_event_frequency": 1.0,
            "loss_magnitude": ale,
            "simulation_results": samples,
            "n_simulations": 200,
        },
        "residual_risk": {
            "annualized_loss_expectancy": ale,
            "mean": ale,
            "median": float(np.median(samples)),
            "std_deviation": float(np.std(samples)),
            "var_95": float(np.percentile(samples, 95)),
            "var_99": float(np.percentile(samples, 99)),
            "loss_event_frequency": 1.0,
            "loss_magnitude": ale,
            "simulation_results": samples,
            "n_simulations": 200,
        },
        "control_adjustments": [],
        "confidence_intervals": {
            "lower_bound": ale * 0.9,
            "upper_bound": ale * 1.1,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "loss_exceedance_curve": [
            {"loss": float(np.percentile(samples, p)), "probability": 1 - p / 100}
            for p in (5, 25, 50, 75, 95, 99)
        ],
        "exceedance_probability_curve": [
            {"percentile": p / 100, "loss": float(np.percentile(samples, p))}
            for p in (5, 25, 50, 75, 95, 99)
        ],
    }


def _v2_snapshot_with_reclassified_subfn() -> list[dict[str, Any]]:
    """V2 snapshot list with at least one post-#131 reclassified sub-function.

    LEC_RESP_RESILIENCE is post-#131 reclassified from ELAPSED_TIME →
    PROBABILITY. The log entry should pick this sub-function up.
    """
    return [
        {
            "snapshot_version": 2,
            "control_id": str(uuid.uuid4()),
            "name": "Resilience playbook (pre-#131 capture)",
            "domains": ["loss_event"],
            "type": "technical",
            "assignments": [
                {
                    "sub_function": "lec_resp_resilience",
                    "capability_value": 0.6,
                    "coverage": 0.8,
                    "reliability": 0.9,
                    "confirmed_by_user_at": None,
                    "derived_from_assignment_id": None,
                    "measured_at": None,
                    "measured_by": None,
                }
            ],
        }
    ]


def _v2_snapshot_only_always_probability() -> list[dict[str, Any]]:
    """V2 snapshot whose assignments only touch always-PROBABILITY sub-functions.

    LEC_PREV_RESISTANCE was PROBABILITY before #131 and remains PROBABILITY
    after #131 — i.e. it is NOT in ``_RECLASSIFIED_SUB_FUNCTIONS_131``. Such
    V2 snapshots carry no post-#131 re-interpretation drift and must:
      - NOT trigger the operator-facing banner (M-N2)
      - emit a ``snapshot_v2_read`` log entry with
        ``reclassified_sub_functions == []`` (M-I1)
    """
    return [
        {
            "snapshot_version": 2,
            "control_id": str(uuid.uuid4()),
            "name": "Always-PROBABILITY control (pre-#131 capture)",
            "domains": ["loss_event"],
            "type": "technical",
            "assignments": [
                {
                    "sub_function": "lec_prev_resistance",
                    "capability_value": 0.85,
                    "coverage": 0.9,
                    "reliability": 0.95,
                    "confirmed_by_user_at": None,
                    "derived_from_assignment_id": None,
                    "measured_at": None,
                    "measured_by": None,
                }
            ],
        }
    ]


def _v3_snapshot_only() -> list[dict[str, Any]]:
    """V3-only snapshot list — no V2 envelope present. Banner + log must NOT fire."""
    return [
        {
            "snapshot_version": 3,
            "control_id": str(uuid.uuid4()),
            "name": "Post-#131 control",
            "domains": ["loss_event"],
            "type": "technical",
            "assignments": [
                {
                    "sub_function": "lec_prev_resistance",
                    "capability_value": 0.85,
                    "coverage": 0.9,
                    "reliability": 0.95,
                    "unit_type": "probability",
                }
            ],
        }
    ]


@pytest_asyncio.fixture
async def analyst_org_v2_snapshot_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
    seed_run_factory: Any,
) -> RiskAnalysisRun:
    """A COMPLETED run with a V2 snapshot (pre-#131 capture) in the analyst's org."""
    _, analyst_org_id = authed_analyst
    scenario = await _seed_analyst_org_scenario(db_session, analyst_org_id, seed_user.id)
    run = await seed_run_factory(
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=analyst_org_id,
        simulation_results=_minimal_completed_simulation_results(),
        controls_snapshot=_v2_snapshot_with_reclassified_subfn(),
        completed_at=datetime.now(UTC),
    )
    return run


@pytest_asyncio.fixture
async def analyst_org_v2_always_probability_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
    seed_run_factory: Any,
) -> RiskAnalysisRun:
    """COMPLETED run with a V2 snapshot referencing only always-PROBABILITY sub-functions.

    Used by the M-N2 (banner) + M-I1 (log filter) regression tests: V2 carries
    no post-#131 re-interpretation drift here, so banner + reclassified list
    must both stay empty.
    """
    _, analyst_org_id = authed_analyst
    scenario = await _seed_analyst_org_scenario(
        db_session,
        analyst_org_id,
        seed_user.id,
        name="v2 always-PROBABILITY test scenario",
    )
    run = await seed_run_factory(
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=analyst_org_id,
        simulation_results=_minimal_completed_simulation_results(),
        controls_snapshot=_v2_snapshot_only_always_probability(),
        completed_at=datetime.now(UTC),
    )
    return run


@pytest_asyncio.fixture
async def analyst_org_v3_snapshot_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
    seed_run_factory: Any,
) -> RiskAnalysisRun:
    """A COMPLETED run with a V3-only snapshot (post-#131 capture)."""
    _, analyst_org_id = authed_analyst
    scenario = await _seed_analyst_org_scenario(
        db_session, analyst_org_id, seed_user.id, name="v3-only test scenario"
    )
    run = await seed_run_factory(
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=analyst_org_id,
        simulation_results=_minimal_completed_simulation_results(),
        controls_snapshot=_v3_snapshot_only(),
        completed_at=datetime.now(UTC),
    )
    return run


# ---------------------------------------------------------------------------
# Test D — banner regression (Sec2-I1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_v2_snapshot_renders_pre_131_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v2_snapshot_run: RiskAnalysisRun,
) -> None:
    """V2 snapshot in run.controls_snapshot triggers the "Pre-#131 snapshot" banner."""
    client, _ = authed_analyst
    response = await client.get(f"/runs/{analyst_org_v2_snapshot_run.id}")
    assert response.status_code == 200
    body = response.text
    assert _BANNER_TEXT in body
    # Banner copy includes the SUB_FUNCTION_UNITS reference + re-interpretation language.
    assert "SUB_FUNCTION_UNITS" in body
    assert "re-interpreted" in body


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_v3_only_snapshot_does_not_render_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v3_snapshot_run: RiskAnalysisRun,
) -> None:
    """V3-only snapshot does NOT trigger the banner (post-#131 captures have no
    interpretation drift to disclose)."""
    client, _ = authed_analyst
    response = await client.get(f"/runs/{analyst_org_v3_snapshot_run.id}")
    assert response.status_code == 200
    assert _BANNER_TEXT not in response.text


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_control_snapshot_table_is_horizontally_scrollable(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v3_snapshot_run: RiskAnalysisRun,
) -> None:
    """The "Controls applied at run time" sub-function table must sit in an
    ``overflow-x-auto`` scroll container so its right-hand columns (Reliability,
    Confirmed) scroll instead of clipping off the card edge on narrow / mobile
    viewports. Regression for the live UAT report (table columns cut off on a
    phone-width screen)."""
    client, _ = authed_analyst
    body = (await client.get(f"/runs/{analyst_org_v3_snapshot_run.id}")).text
    # The control-snapshot table renders...
    assert '<table class="table table-xs">' in body
    # ...wrapped in an overflow-x-auto container (whitespace-tolerant).
    assert re.search(r'overflow-x-auto[^>]*>\s*<table class="table table-xs">', body), (
        "control-snapshot table is not wrapped in overflow-x-auto (clips on mobile)"
    )
    # Sibling fix: the completed-run "Risk distribution" results table (money
    # columns) renders on the same page and must ALSO be scroll-wrapped.
    assert "Risk distribution" in body
    assert re.search(r'overflow-x-auto[^>]*>\s*<table class="table table-sm">', body), (
        "risk-distribution results table is not wrapped in overflow-x-auto (clips on mobile)"
    )


# ---------------------------------------------------------------------------
# Test E — structured log emission (Sec3-I1 + CR4-B1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_snapshot_read_emits_structured_log(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v2_snapshot_run: RiskAnalysisRun,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reading a V2-snapshot run emits ``snapshot_v2_read`` log with extra dict."""
    client, _ = authed_analyst

    with caplog.at_level(logging.INFO, logger="idraa.routes.runs"):
        response = await client.get(f"/runs/{analyst_org_v2_snapshot_run.id}")
        assert response.status_code == 200

    matches = [r for r in caplog.records if r.getMessage().startswith("snapshot_v2_read")]
    assert matches, (
        "expected a 'snapshot_v2_read' log record from routes.runs._emit_v2_snapshot_read_log "
        f"but got: {[r.getMessage() for r in caplog.records]}"
    )
    record = matches[0]
    # CR4-B1: stdlib logging, structured fields via extra= attach to the LogRecord.
    assert hasattr(record, "reclassified_sub_functions")
    assert "lec_resp_resilience" in record.reclassified_sub_functions
    assert hasattr(record, "run_id")
    assert record.run_id == str(analyst_org_v2_snapshot_run.id)
    assert hasattr(record, "user_id")


@pytest.mark.asyncio
async def test_v3_only_snapshot_does_not_emit_v2_read_log(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v3_snapshot_run: RiskAnalysisRun,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A V3-only snapshot read does NOT emit ``snapshot_v2_read`` (no V2 envelope)."""
    client, _ = authed_analyst

    with caplog.at_level(logging.INFO, logger="idraa.routes.runs"):
        response = await client.get(f"/runs/{analyst_org_v3_snapshot_run.id}")
        assert response.status_code == 200

    v2_logs = [r for r in caplog.records if r.getMessage().startswith("snapshot_v2_read")]
    assert v2_logs == [], (
        f"V3-only snapshot must NOT emit snapshot_v2_read; got: {[r.getMessage() for r in v2_logs]}"
    )


# ---------------------------------------------------------------------------
# M-N2 — banner condition tightened to V2-with-reclassified-sub-function only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_v2_snapshot_with_only_always_probability_does_not_render_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v2_always_probability_run: RiskAnalysisRun,
) -> None:
    """M-N2: V2 snapshot referencing only always-PROBABILITY sub-functions must
    NOT render the Pre-#131 banner.

    These V2 snapshots carry no #131 re-interpretation drift; rendering the
    banner over-reports and erodes operator trust in the signal.
    """
    client, _ = authed_analyst
    response = await client.get(f"/runs/{analyst_org_v2_always_probability_run.id}")
    assert response.status_code == 200
    assert _BANNER_TEXT not in response.text


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_v2_snapshot_with_reclassified_sub_function_renders_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v2_snapshot_run: RiskAnalysisRun,
) -> None:
    """M-N2: V2 snapshot carrying a #131-reclassified sub-function renders the
    Pre-#131 banner. Mirrors ``test_v2_snapshot_renders_pre_131_banner`` from
    a separation-of-concerns angle (banner driven by reclassified set, not
    by V2-presence)."""
    client, _ = authed_analyst
    response = await client.get(f"/runs/{analyst_org_v2_snapshot_run.id}")
    assert response.status_code == 200
    assert _BANNER_TEXT in response.text


# ---------------------------------------------------------------------------
# M-I1 — structured-log reclassified_sub_functions filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_snapshot_with_only_always_probability_sub_functions_emits_empty_reclassified_list(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v2_always_probability_run: RiskAnalysisRun,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M-I1: V2 snapshot whose assignments only touch always-PROBABILITY
    sub-functions still emits ``snapshot_v2_read`` (the V2 envelope is the
    trigger) but ``reclassified_sub_functions`` must be ``[]`` — never list
    sub-functions whose unit_type did not change at #131.
    """
    client, _ = authed_analyst

    with caplog.at_level(logging.INFO, logger="idraa.routes.runs"):
        response = await client.get(f"/runs/{analyst_org_v2_always_probability_run.id}")
        assert response.status_code == 200

    matches = [r for r in caplog.records if r.getMessage().startswith("snapshot_v2_read")]
    assert matches, (
        "always-PROBABILITY V2 snapshot must still emit snapshot_v2_read (V2 envelope) "
        f"but got no matching records: {[r.getMessage() for r in caplog.records]}"
    )
    record = matches[0]
    assert hasattr(record, "reclassified_sub_functions")
    assert record.reclassified_sub_functions == [], (
        "always-PROBABILITY sub-functions must NOT appear in reclassified_sub_functions; "
        f"got {record.reclassified_sub_functions}"
    )


@pytest.mark.asyncio
async def test_v2_snapshot_with_reclassified_sub_function_emits_the_slug(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v2_snapshot_run: RiskAnalysisRun,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M-I1: V2 snapshot carrying a #131-reclassified sub-function emits its
    slug in ``reclassified_sub_functions``. Symmetric to the always-PROBABILITY
    negative-case test above."""
    client, _ = authed_analyst

    with caplog.at_level(logging.INFO, logger="idraa.routes.runs"):
        response = await client.get(f"/runs/{analyst_org_v2_snapshot_run.id}")
        assert response.status_code == 200

    matches = [r for r in caplog.records if r.getMessage().startswith("snapshot_v2_read")]
    assert matches
    record = matches[0]
    assert hasattr(record, "reclassified_sub_functions")
    assert "lec_resp_resilience" in record.reclassified_sub_functions


# ---------------------------------------------------------------------------
# #454 web-UI polish catalog — inputs_hash demotion, confirmed badge,
# humanized sub-function labels.
# ---------------------------------------------------------------------------


def _v2_snapshot_mixed_confirmed() -> list[dict[str, Any]]:
    """V2 snapshot with two assignments — one confirmed, one not — so the
    per-control "N of M confirmed" header badge reads "1 of 2 confirmed"."""
    return [
        {
            "snapshot_version": 2,
            "control_id": str(uuid.uuid4()),
            "name": "Mixed-confirmation control",
            "domains": ["decision_support", "loss_event"],
            "type": "technical",
            "assignments": [
                {
                    "sub_function": "lec_prev_resistance",
                    "capability_value": 0.85,
                    "coverage": 0.9,
                    "reliability": 0.8,
                    "confirmed_by_user_at": datetime.now(UTC).isoformat(),
                    "derived_from_assignment_id": None,
                    "measured_at": None,
                    "measured_by": None,
                },
                {
                    "sub_function": "dsc_prev_sa_reporting",
                    "capability_value": 0.5,
                    "coverage": 0.7,
                    "reliability": 0.6,
                    "confirmed_by_user_at": None,
                    "derived_from_assignment_id": None,
                    "measured_at": None,
                    "measured_by": None,
                },
            ],
        }
    ]


@pytest_asyncio.fixture
async def analyst_org_mixed_confirmed_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
    seed_run_factory: Any,
) -> RiskAnalysisRun:
    """COMPLETED run whose snapshot has one confirmed + one unconfirmed assignment."""
    _, analyst_org_id = authed_analyst
    scenario = await _seed_analyst_org_scenario(
        db_session, analyst_org_id, seed_user.id, name="mixed-confirmed test scenario"
    )
    run = await seed_run_factory(
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=analyst_org_id,
        simulation_results=_minimal_completed_simulation_results(),
        controls_snapshot=_v2_snapshot_mixed_confirmed(),
        completed_at=datetime.now(UTC),
    )
    return run


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_inputs_hash_demoted_below_main_content(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_v3_snapshot_run: RiskAnalysisRun,
) -> None:
    """#454 item 1: the reproducibility hash must stay in the DOM (audit
    affordance) but be demoted out of the prominent header region into the
    collapsed <details> near the bottom of the page."""
    client, _ = authed_analyst
    body = (await client.get(f"/runs/{analyst_org_v3_snapshot_run.id}")).text

    # Still present in the DOM as an audit anchor, inside the demoted details.
    assert 'class="run-inputs-hash' in body, "demoted inputs-hash <details> block missing"
    assert "Inputs hash:" in body, "inputs hash line missing from DOM (audit affordance lost)"
    assert analyst_org_v3_snapshot_run.inputs_hash in body

    # Position check: the hash line now sits AFTER the main controls section,
    # not at the top header. The old prominent top line ("inputs_hash: <code>"
    # immediately under the page header) is gone.
    controls_idx = body.index("Controls applied at run time")
    hash_idx = body.index("Inputs hash:")
    assert hash_idx > controls_idx, (
        "inputs_hash line is not demoted below the main run content (still prominent)"
    )


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_controls_applied_show_confirmed_count_badge(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_mixed_confirmed_run: RiskAnalysisRun,
) -> None:
    """#454 item 4: per-control header carries a single "N of M confirmed" badge
    instead of a red "unconfirmed" on every assignment row."""
    client, _ = authed_analyst
    body = (await client.get(f"/runs/{analyst_org_mixed_confirmed_run.id}")).text

    # T7: the condensed controls_snapshot renders a single "N of M" confirmed
    # badge per control in the "Confirmed" column (full "confirmed by a user"
    # text is in the badge title=); the alarming per-row red "unconfirmed" is gone.
    assert "1 of 2" in body, "confirmed-count badge missing / wrong count"
    assert "confirmed by a user" in body, "confirmed-count badge title missing"
    # The alarming per-row red "unconfirmed" text is gone.
    assert 'text-status-warning">unconfirmed' not in body, (
        "per-row red 'unconfirmed' text should be replaced by the header badge"
    )


@pytest.mark.skipif(not _DETAIL_TEMPLATE.exists(), reason="detail.html template not present")
@pytest.mark.asyncio
async def test_sub_function_slug_humanized_with_raw_tooltip(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_mixed_confirmed_run: RiskAnalysisRun,
) -> None:
    """#454 item 3b: sub-function slug cells render humanized text with the raw
    slug preserved in a title= tooltip for auditability.

    Polish-1 (post-#454 SWE review): ``_humanize_slug`` uppercases known
    FAIR-CAM acronym tokens ("dsc", "sa", ...) instead of merely
    capitalizing them, so this pins "DSC Prev SA Reporting" rather than the
    earlier "Dsc Prev Sa Reporting".
    """
    client, _ = authed_analyst
    body = (await client.get(f"/runs/{analyst_org_mixed_confirmed_run.id}")).text

    # Humanized display text present.
    assert "DSC Prev SA Reporting" in body, "sub-function slug not humanized for display"
    # Raw slug preserved in the title= tooltip.
    assert 'title="dsc_prev_sa_reporting"' in body, "raw sub-function slug not kept in tooltip"
    # Humanized FAIR-CAM domain chip.
    assert "Decision Support" in body, "domain chip not humanized"
