"""Native equivalence harness (the GATE, Epic A #324).

HARNESS LIFECYCLE (live-pyfair parity during build → golden-vs-native after cut):
  During the build (Tasks 1-7) the harness ran LIVE-PYFAIR parity layers —
  ``test_layer2_engine_parity`` / ``test_layer3_multiseed_bias`` /
  ``test_aggregate_parity`` compared the native engine residual/rollup metrics
  against the pyfair oracle (via the now-deleted
  ``run_executor._fair_params_to_risk_params`` bridge +
  ``ControlAwareRiskCalculator``), and ``test_freeze_golden_from_pyfair`` froze
  the pyfair oracle's per-metric reference values into ``golden/*.json``.
  At the Task-8 CUTOVER ``run_executor`` was rewired to the native calculator
  and the lossy bridge was DELETED — which removes the input the live-pyfair
  layers depended on. So those four live-pyfair layers were RETIRED here (folded
  into the Task-8 commit by dependency). The golden JSON stays committed (frozen
  in Task 7, NOT re-frozen) and the surviving layers below validate against it.

Surviving layers (pyfair-free; consume the committed golden):
  1. ``test_layer1_analytic_anchor``    — native engine grand-mean vs the
     closed-form E[TEF]·E[vuln]·(E[PL]+E[SL]) on no-control fixtures. No pyfair.
  2b ``test_layer2b_native_vs_golden``  — native residual metrics vs the FROZEN
     golden reference, within REL_TOL. The regression anchor that SURVIVES pyfair
     removal.
  +  ``test_aggregate_native_vs_golden`` — native AGGREGATE rollup vs the frozen
     aggregate golden, within REL_TOL.

All n=100k layers are ``@pytest.mark.slow``; run with ``-m slow``.

TOLERANCE DISCIPLINE: REL_TOL is pinned from measured MC standard error. A
golden FAILURE is a genuine finding — do NOT loosen the tolerance to force green.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from fair_cam.risk_engine.fair_core import DistributionType, FAIREngine

from tests.equivalence.fixtures import (
    N_ITER,
    REL_TOL,
    SEEDS,
    aggregate_fixture_pair,
    build_control_registry,
    shared_surface_fixtures,
)

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.fixture
def control_registry_fixture():
    return build_control_registry()


def _dist_mean(d):
    p = d.parameters
    if d.distribution_type == DistributionType.PERT:
        return (p["low"] + 4 * p["mode"] + p["high"]) / 6
    if d.distribution_type == DistributionType.UNIFORM:
        return (p["low"] + p["high"]) / 2
    if d.distribution_type == DistributionType.NORMAL:
        return p["mean"]
    raise AssertionError(f"non-analytic dist {d.distribution_type}")


def _parity_keys(fx):
    """Metric keys on which native-vs-pyfair parity is asserted for this fixture.

    Defaults to the full REL_TOL set. A fixture may restrict it (e.g. normal_loss
    asserts only mean/median because pyfair PERT-approximates NORMAL and its tail
    is a known lossy-bridge artefact, not an engine equivalence claim)."""
    keys = fx.parity_metrics or tuple(REL_TOL)
    return {k: REL_TOL[k] for k in keys}


def _metrics(arr):
    v95 = np.percentile(arr, 95)
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(v95),
        "p99": float(np.percentile(arr, 99)),
        "var95": float(v95),
        "es95": float(np.mean(arr[arr >= v95])),
    }


# --------------------------------------------------------------------------- #
# Layer 1 — analytic anchor (no pyfair).
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.parametrize("fx", shared_surface_fixtures(), ids=lambda f: f.name)
def test_layer1_analytic_anchor(fx):
    if fx.control_ids:
        pytest.skip("analytic anchor only for no-control fixtures")
    p = fx.params
    analytic = (
        _dist_mean(p.threat_event_frequency)
        * _dist_mean(p.vulnerability)
        * (_dist_mean(p.primary_loss) + _dist_mean(p.secondary_loss))
    )
    means = [
        FAIREngine(iterations=N_ITER, random_seed=s).calculate_risk(p)["ale_mean"] for s in SEEDS
    ]
    grand = float(np.mean(means))
    se = float(np.std(means) / math.sqrt(len(SEEDS)))
    assert abs(grand - analytic) < 3 * se + 0.01 * analytic, (
        f"{fx.name}: analytic={analytic:.2f} grand={grand:.2f} se={se:.2f}"
    )


# --------------------------------------------------------------------------- #
# Native residual sampler — shared by the surviving golden-anchored layers.
# (The live-pyfair parity/bias/aggregate-parity layers + the golden-freeze
# helper were RETIRED at the Task-8 cutover — see the module docstring's
# HARNESS LIFECYCLE note. The golden JSON they froze stays committed.)
# --------------------------------------------------------------------------- #
def _native_residual(fx, registry, seed):
    from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator

    nat = NativeControlAwareRiskCalculator(
        controls=registry, n_simulations=N_ITER, random_seed=seed
    ).calculate_control_enhanced_risk(fx.params, fx.control_ids, "fx")
    return nat.residual_risk.simulation_results


# --------------------------------------------------------------------------- #
# Layer 2b + aggregate — native vs the FROZEN golden (the anchors that survive
# pyfair removal). The golden JSON was frozen from the pyfair oracle in Task 7;
# it is consumed here, NOT re-frozen.
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.parametrize("fx", shared_surface_fixtures(), ids=lambda f: f.name)
def test_layer2b_native_vs_golden(fx, control_registry_fixture):
    """Native residual metrics vs the FROZEN golden reference, within REL_TOL.
    This is the regression anchor that survives pyfair removal (Task 9): when the
    live-pyfair layers are retired, this remains as the cross-engine guarantee."""
    golden_path = GOLDEN_DIR / f"{fx.name}.json"
    assert golden_path.exists(), (
        f"golden missing for {fx.name}; run test_freeze_golden_from_pyfair first"
    )
    golden = json.loads(golden_path.read_text())
    nat_metrics = [_metrics(_native_residual(fx, control_registry_fixture, s)) for s in SEEDS]
    for k, tol in _parity_keys(fx).items():
        mean_nat = float(np.mean([m[k] for m in nat_metrics]))
        delta = (mean_nat - golden[k]) / (abs(golden[k]) or 1.0)
        assert abs(delta) < tol, (
            f"{fx.name}/{k}: native-vs-golden rel-delta {delta:+.4f} exceeds tol {tol}"
        )


@pytest.mark.slow
def test_aggregate_native_vs_golden():
    """Native AGGREGATE rollup vs the frozen aggregate golden, within REL_TOL."""
    from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator

    golden_path = GOLDEN_DIR / "aggregate.json"
    assert golden_path.exists(), "aggregate golden missing; run test_freeze_golden_from_pyfair"
    golden = json.loads(golden_path.read_text())
    nat_agg = NativeControlAwareRiskCalculator(
        controls=[], n_simulations=N_ITER, random_seed=99
    ).calculate_aggregate_enhanced_risk(
        per_scenario_risk_params=aggregate_fixture_pair(), active_control_ids=[]
    )
    nm = _metrics(nat_agg.aggregate_without_controls.simulation_results)
    for k, tol in REL_TOL.items():
        delta = (nm[k] - golden[k]) / (abs(golden[k]) or 1.0)
        assert abs(delta) < tol, (
            f"aggregate/{k}: native-vs-golden rel-delta {delta:+.4f} exceeds tol {tol}"
        )
