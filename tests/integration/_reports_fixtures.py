"""Test helpers for omicron-2 F10 + F12.

Provides ``_make_completed_aggregate_run`` and (T2 #351)
``_make_completed_single_run`` that build real RiskAnalysisRun rows
with populated ``simulation_results`` dicts, and seed the referenced
Scenario rows.

NOT a conftest (leading underscore); call from test bodies.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import ScenarioType, ThreatCategory
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario


async def _make_scenarios(
    session: AsyncSession, org_id: uuid.UUID, names: list[str]
) -> list[Scenario]:
    """Seed Scenario rows with placeholder FAIR distribution defaults that
    satisfy the model's NOT NULL constraints. Returns the seeded scenarios.

    Note: the plan's helper sketch used flat field names
    (``tef_min``, ``primary_loss_likely``, ``event_type``, ``sector``).
    The actual model carries FAIR distributions as a JSON dict per field
    (``threat_event_frequency``, ``vulnerability``, ``primary_loss``)
    and uses ``threat_category`` (enum) for the descriptive anchors.
    This helper mirrors
    ``tests/integration/_dashboard_fixtures.py::_make_scenario`` for
    cross-test consistency.
    """
    scenarios: list[Scenario] = []
    for name in names:
        sc = Scenario(
            id=uuid.uuid4(),
            organization_id=org_id,
            name=name,
            description=f"{name} description for testing.",
            scenario_type=ScenarioType.CUSTOM,
            threat_category=ThreatCategory.RANSOMWARE,
            threat_event_frequency={
                "distribution": "PERT",
                "low": 0.1,
                "mode": 0.5,
                "high": 1.0,
            },
            vulnerability={
                "distribution": "PERT",
                "low": 0.2,
                "mode": 0.4,
                "high": 0.6,
            },
            primary_loss={
                "distribution": "PERT",
                "low": 10_000.0,
                "mode": 100_000.0,
                "high": 1_000_000.0,
            },
        )
        session.add(sc)
        scenarios.append(sc)
    await session.flush()
    return scenarios


def _aggregate_simulation_results(
    *,
    scenario_ids: list[uuid.UUID],
    scenario_names: list[str],
    legacy_band: bool = False,
) -> dict[str, Any]:
    """Build a simulation_results dict for an AGGREGATE run that satisfies
    the F9 ExecutivePdfData consumer shape (per_scenario, aggregate_*,
    control_value, confidence_intervals, n_scenarios, n_simulations).

    ``legacy_band=True`` emits a PRE-#202 ``confidence_intervals`` block:
    the retired Gaussian SE-of-the-mean geometry (``confidence_level`` /
    ``standard_error`` / narrow ``lower_bound`` / ``upper_bound``) with NO
    ``interval_pct`` marker. Used to assert the suppress-not-relabel gate
    (#202) on the executive-PDF path.
    """
    per_scenario = []
    for sid, name in zip(scenario_ids, scenario_names, strict=True):
        # control_adjustments carry shapley_value so the attribution matrix builder
        # (#352 semantics) returns rows rather than the 'unavailable' state.
        # Shared control IDs across all scenarios so the matrix stays compact
        # (3 columns × N rows) and avoids page-count explosion in multi-scenario tests.
        per_scenario.append(
            {
                "scenario_id": str(sid),
                "scenario_name": name,
                "base_risk": {
                    "annualized_loss_expectancy": 800_000.0,
                    "loss_event_frequency": 2.0,
                },
                "residual_risk": {"annualized_loss_expectancy": 400_000.0},
                "control_adjustments": [
                    {
                        "control_id": "ctrl-shared-a",
                        "control_name": "Firewall",
                        "risk_reduction_value": 200_000.0,
                        "loss_reduction_per_event": 5_000.0,
                        "shapley_value": 150_000.0,
                    },
                    {
                        "control_id": "ctrl-shared-b",
                        "control_name": "EDR",
                        "risk_reduction_value": 100_000.0,
                        "loss_reduction_per_event": 3_000.0,
                        "shapley_value": 80_000.0,
                    },
                    {
                        "control_id": "ctrl-shared-c",
                        "control_name": "SIEM",
                        "risk_reduction_value": 50_000.0,
                        "loss_reduction_per_event": 1_000.0,
                        "shapley_value": 30_000.0,
                    },
                ],
            }
        )
    return {
        "per_scenario": per_scenario,
        # AGGREGATE dicts carry the FULL tail ladder (var_90/95/99/999 +
        # expected_shortfall) — the shape run_executor._build_aggregate_lec_pair
        # persists (via _fair_risk_to_dict's tail-metric merge). Mirrors the prod
        # post-change shape so has_tail_metrics() is True for the aggregate report.
        "aggregate_with_controls": {
            "annualized_loss_expectancy": 2_610_000.0,
            "mean": 2_610_000.0,
            "median": 2_400_000.0,
            "std_deviation": 800_000.0,
            "var_90": 3_400_000.0,
            "var_95": 3_900_000.0,
            "var_99": 5_200_000.0,
            "var_999": 6_800_000.0,
            "expected_shortfall": {
                "es_95": 4_500_000.0,
                "es_99": 5_900_000.0,
                "es_999": 7_400_000.0,
            },
            "loss_exceedance_curve": [
                {"loss": 1.0, "probability": 0.99},
                {"loss": 10_000.0, "probability": 0.5},
                {"loss": 1_000_000.0, "probability": 0.05},
            ],
        },
        "aggregate_without_controls": {
            "annualized_loss_expectancy": 4_950_000.0,
            "mean": 4_950_000.0,
            "median": 4_600_000.0,
            "std_deviation": 1_500_000.0,
            "var_90": 6_300_000.0,
            "var_95": 7_200_000.0,
            "var_99": 9_500_000.0,
            "var_999": 12_000_000.0,
            "expected_shortfall": {
                "es_95": 8_300_000.0,
                "es_99": 10_500_000.0,
                "es_999": 13_000_000.0,
            },
            "loss_exceedance_curve": [
                {"loss": 1.0, "probability": 0.99},
                {"loss": 50_000.0, "probability": 0.5},
                {"loss": 5_000_000.0, "probability": 0.05},
            ],
        },
        "control_value": {"dollars": 2_340_000.0, "percent": 47.3},
        # Issue #202: empirical central-95% percentile band (p2.5/p97.5). The
        # retired heuristic keys (confidence_level / standard_error) are gone;
        # interval_pct is the fixed analyst-chosen central interval. sample_size
        # is retained (the executive-PDF n_simulations source). lower/upper are
        # hard-set p2.5/p97.5 values (this fixture carries no sample array, so
        # the persist-time band helper never runs on it).
        "confidence_intervals": (
            {
                # PRE-#202 legacy geometry: retired SE-of-the-mean band, narrow
                # bounds, NO interval_pct marker. has_ci_band() returns False
                # for this -> band suppressed, not relabeled.
                "confidence_level": 0.95,
                "standard_error": 12_000.0,
                "lower_bound": 2_586_480.0,
                "upper_bound": 2_633_520.0,
                "sample_size": 50_000,
            }
            if legacy_band
            else {
                "lower_bound": 2_400_000.0,
                "upper_bound": 2_820_000.0,
                "interval_pct": 95,
                "sample_size": 50_000,
            }
        ),
        "n_scenarios": len(scenario_ids),
        "n_simulations": 50_000,
    }


def _controls_snapshot_v2(items: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """items = [(name, domain, type), ...] -> ControlSnapshotV2-shaped list.

    Matches the production writer ``services/run_executor._snapshot_control_v2``
    which emits ``domains: list[str]`` containing the sorted lowercase
    ControlDomain enum values spanned by the control's assignments (issue #90).
    Caller-side ``domain`` strings may be passed either uppercase
    ("LOSS_EVENT") or lowercase ("loss_event"); both are normalised to the
    lowercase enum value the real writer produces.
    """
    return [
        {
            "snapshot_version": 2,
            "control_id": str(uuid.uuid4()),
            "name": name,
            "domains": [domain.lower()],
            "type": ctype,
            "assignments": [],
        }
        for (name, domain, ctype) in items
    ]


async def _make_completed_aggregate_run(
    session: AsyncSession,
    org: Organization,
    *,
    name: str = "Q2 board review",
    scenario_names: list[str] | None = None,
    controls: list[tuple[str, str, str]] | None = None,
    completed_at: dt.datetime | None = None,
    legacy_band: bool = False,
) -> RiskAnalysisRun:
    """Seed an AGGREGATE COMPLETED run + its referenced scenarios.

    ``legacy_band=True`` seeds a pre-#202 ``confidence_intervals`` block (no
    ``interval_pct`` marker) so the suppress-not-relabel gate can be exercised.

    Returns the persisted run.
    """
    scenario_names = scenario_names or ["Ransomware", "Insider", "APT"]
    controls = (
        controls
        if controls is not None
        else [
            ("Firewall", "LOSS_EVENT", "preventive"),
            ("EDR", "LOSS_EVENT", "preventive"),
            ("SIEM", "VARIANCE_MANAGEMENT", "detective"),
            ("Risk Committee", "DECISION_SUPPORT", "responsive"),
        ]
    )
    scenarios = await _make_scenarios(session, org.id, scenario_names)
    sids = [sc.id for sc in scenarios]

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org.id,
        name=name,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        scenario_id=None,
        aggregate_scenario_ids=[str(s) for s in sids],
        mc_iterations=50_000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=_controls_snapshot_v2(controls),
        control_ids_used=[c[0] for c in controls],
        created_at=completed_at or dt.datetime(2026, 5, 7, 14, 30, tzinfo=dt.UTC),
        completed_at=completed_at or dt.datetime(2026, 5, 7, 14, 30, tzinfo=dt.UTC),
        simulation_results=_aggregate_simulation_results(
            scenario_ids=sids, scenario_names=scenario_names, legacy_band=legacy_band
        ),
    )
    session.add(run)
    await session.flush()
    return run


def _single_simulation_results(
    *,
    scenario_id: uuid.UUID,
    scenario_name: str,
    legacy_band: bool = False,
) -> dict[str, Any]:
    """Build a simulation_results dict for a SINGLE run.

    T2 (#351): SINGLE shape differs from AGGREGATE — base_risk / residual_risk
    at the top level instead of aggregate_with/without_controls.
    Includes tail-risk metrics (var_90/var_95/var_99/var_999 + expected_shortfall)
    so has_tail_metrics() returns True for the main fixture.
    """
    return {
        "base_risk": {
            "annualized_loss_expectancy": 800_000.0,
            "mean": 820_000.0,
            "median": 750_000.0,
            "std_deviation": 200_000.0,
            "var_90": 1_100_000.0,
            "var_95": 1_300_000.0,
            "var_99": 1_800_000.0,
            "var_999": 2_500_000.0,
            "expected_shortfall": {
                "es_95": 1_450_000.0,
                "es_99": 1_950_000.0,
                "es_999": 2_700_000.0,
            },
            "loss_exceedance_curve": [
                {"loss": 1.0, "probability": 0.99},
                {"loss": 100_000.0, "probability": 0.5},
                {"loss": 2_000_000.0, "probability": 0.05},
            ],
            "loss_event_frequency": 2.0,
            "n_simulations": 10_000,
        },
        "residual_risk": {
            "annualized_loss_expectancy": 400_000.0,
            "mean": 410_000.0,
            "median": 375_000.0,
            "std_deviation": 100_000.0,
            "var_90": 580_000.0,
            "var_95": 680_000.0,
            "var_99": 900_000.0,
            "var_999": 1_200_000.0,
            "expected_shortfall": {
                "es_95": 750_000.0,
                "es_99": 1_000_000.0,
                "es_999": 1_300_000.0,
            },
            "loss_exceedance_curve": [
                {"loss": 1.0, "probability": 0.99},
                {"loss": 50_000.0, "probability": 0.5},
                {"loss": 1_000_000.0, "probability": 0.05},
            ],
            "loss_event_frequency": 1.5,
            "n_simulations": 10_000,
        },
        "control_adjustments": [
            {
                "control_id": "ctrl-aaa",
                "control_name": "Firewall",
                "effectiveness": 0.7,
                "risk_reduction_value": 200_000.0,
                "loss_reduction_per_event": 5_000.0,
                "control_cost": 50_000.0,
            },
            {
                "control_id": "ctrl-bbb",
                "control_name": "EDR",
                "effectiveness": 0.5,
                "risk_reduction_value": 150_000.0,
                "loss_reduction_per_event": 3_000.0,
                "control_cost": 30_000.0,
            },
            {
                "control_id": "ctrl-ccc",
                "control_name": "SIEM",
                "effectiveness": 0.3,
                "risk_reduction_value": 50_000.0,
                "loss_reduction_per_event": 1_000.0,
                "control_cost": 20_000.0,
            },
        ],
        "cost_summary": {
            "total_annual_cost": 100_000.0,
            "total_risk_reduction": 400_000.0,
            "net_benefit": 300_000.0,
            "aggregate_roi": 4.0,
        },
        "confidence_intervals": (
            {
                "confidence_level": 0.95,
                "standard_error": 5_000.0,
                "lower_bound": 390_200.0,
                "upper_bound": 409_800.0,
                "sample_size": 10_000,
            }
            if legacy_band
            else {
                "lower_bound": 300_000.0,
                "upper_bound": 520_000.0,
                "interval_pct": 95,
                "sample_size": 10_000,
            }
        ),
        "exceedance_probability_curve": [
            {"percentile": 0.5, "loss": 375_000.0},
            {"percentile": 0.9, "loss": 680_000.0},
            {"percentile": 0.99, "loss": 900_000.0},
        ],
        "n_scenarios": 1,
        "n_simulations": 10_000,
    }


def _scenario_inputs_snapshot_for(scenarios: list[Scenario]) -> dict[str, Any]:
    """Build a scenario_inputs_snapshot dict from live scenario ORM rows.

    T2 (#351): mirrors _build_scenario_inputs_snapshot in run_executor.py.
    Used by test fixtures to populate the column at seed time.
    """
    return {
        "scenarios": [
            {
                "scenario_id": str(sc.id),
                "scenario_name": sc.name,
                "threat_event_frequency": sc.threat_event_frequency,
                "vulnerability": sc.vulnerability,
                "primary_loss": sc.primary_loss,
                "secondary_loss": sc.secondary_loss,
            }
            for sc in scenarios
        ]
    }


async def _make_completed_single_run(
    session: AsyncSession,
    org: Organization,
    *,
    name: str = "Q2 single-scenario analysis",
    scenario_name: str = "Ransomware OT",
    controls: list[tuple[str, str, str]] | None = None,
    completed_at: dt.datetime | None = None,
    legacy_band: bool = False,
    with_snapshot: bool = True,
) -> RiskAnalysisRun:
    """Seed a SINGLE COMPLETED run + its referenced scenario.

    T2 (#351): adds a SINGLE-run fixture; the existing fixture is AGGREGATE-only.

    ``legacy_band=True`` seeds a pre-#202 CI block (no interval_pct marker).
    ``with_snapshot=False`` seeds a legacy-null scenario_inputs_snapshot for
    testing the honest-label fallback path.

    Returns the persisted run.
    """
    controls = (
        controls
        if controls is not None
        else [
            ("Firewall", "LOSS_EVENT", "preventive"),
            ("EDR", "LOSS_EVENT", "preventive"),
            ("SIEM", "VARIANCE_MANAGEMENT", "detective"),
        ]
    )
    scenarios = await _make_scenarios(session, org.id, [scenario_name])
    sc = scenarios[0]

    snapshot = _scenario_inputs_snapshot_for(scenarios) if with_snapshot else None

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org.id,
        name=name,
        run_type=RunType.SINGLE,
        status=RunStatus.COMPLETED,
        scenario_id=sc.id,
        aggregate_scenario_ids=None,
        mc_iterations=10_000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=_controls_snapshot_v2(controls),
        control_ids_used=[c[0] for c in controls],
        created_at=completed_at or dt.datetime(2026, 5, 7, 15, 0, tzinfo=dt.UTC),
        completed_at=completed_at or dt.datetime(2026, 5, 7, 15, 0, tzinfo=dt.UTC),
        simulation_results=_single_simulation_results(
            scenario_id=sc.id,
            scenario_name=scenario_name,
            legacy_band=legacy_band,
        ),
        scenario_inputs_snapshot=snapshot,
    )
    session.add(run)
    await session.flush()
    return run
