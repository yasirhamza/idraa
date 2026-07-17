"""Reproducibility contract tests for the MC seed machinery.

Asserts already-built behaviour (T6 spawn_key + T7 executor wiring):
- Same base seed → byte-identical arrays
- Different base seeds → different arrays
- Per-array reconstruction via spawn_key + SeedSequence → byte-identical
- Collapsing spawn_key to bare int must NOT reconstruct (negative guard)
- Two scenarios from the same calculator get independent, distinct arrays
"""

from __future__ import annotations

import numpy as np
import pytest
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIREngine,
    FAIRParameters,
)
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator


@pytest.fixture()
def simple_fair_params() -> FAIRParameters:
    """Minimal, fast FAIRParameters for reproducibility tests."""
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.5, "high": 0.5}),
        primary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 1000, "mode": 5000, "high": 10000}
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 500, "mode": 2000, "high": 5000}
        ),
    )


def test_same_seed_byte_identical(simple_fair_params: FAIRParameters) -> None:
    """Two calculators with the same base seed must produce byte-identical arrays."""
    a = NativeControlAwareRiskCalculator(
        n_simulations=500, random_seed=42
    ).calculate_control_enhanced_risk(simple_fair_params, [], "s")
    b = NativeControlAwareRiskCalculator(
        n_simulations=500, random_seed=42
    ).calculate_control_enhanced_risk(simple_fair_params, [], "s")
    assert np.array_equal(a.base_risk.simulation_results, b.base_risk.simulation_results)


def test_different_seed_differs(simple_fair_params: FAIRParameters) -> None:
    """Different base seeds must produce different arrays."""
    a = NativeControlAwareRiskCalculator(
        n_simulations=500, random_seed=42
    ).calculate_control_enhanced_risk(simple_fair_params, [], "s")
    b = NativeControlAwareRiskCalculator(
        n_simulations=500, random_seed=43
    ).calculate_control_enhanced_risk(simple_fair_params, [], "s")
    assert not np.array_equal(a.base_risk.simulation_results, b.base_risk.simulation_results)


def test_per_array_reconstruction_via_spawn_key(simple_fair_params: FAIRParameters) -> None:
    """Reconstruct a scenario's array via SeedSequence(entropy=base, spawn_key=(idx,)).

    If this assertion fails it is a REAL reproducibility break — do NOT weaken it.
    """
    base = 42
    ce = NativeControlAwareRiskCalculator(
        n_simulations=500, random_seed=base
    ).calculate_control_enhanced_risk(simple_fair_params, [], "s")
    idx = ce.spawn_key[0]
    ss = np.random.SeedSequence(entropy=base, spawn_key=(idx,))
    recon = FAIREngine(iterations=500, random_seed=ss).calculate_risk(simple_fair_params)
    assert np.array_equal(recon["risk_distribution"], ce.base_risk.simulation_results)


def test_int_collapse_does_not_match(simple_fair_params: FAIRParameters) -> None:
    """Negative guard: collapsing the SeedSequence to the bare int spawn index must NOT match.

    Ensures callers cannot accidentally reconstruct by passing spawn_key[0] directly
    as an integer seed — that path is a different RNG stream.
    """
    ce = NativeControlAwareRiskCalculator(
        n_simulations=500, random_seed=42
    ).calculate_control_enhanced_risk(simple_fair_params, [], "s")
    wrong = FAIREngine(iterations=500, random_seed=ce.spawn_key[0]).calculate_risk(
        simple_fair_params
    )
    assert not np.array_equal(wrong["risk_distribution"], ce.base_risk.simulation_results)


def test_aggregate_independence_preserved(simple_fair_params: FAIRParameters) -> None:
    """Two scenarios from the same calculator get independent spawned children → different arrays.

    Also asserts spawn_key ordering: first call → (0,), second call → (1,).
    """
    calc = NativeControlAwareRiskCalculator(n_simulations=500, random_seed=42)
    ce0 = calc.calculate_control_enhanced_risk(simple_fair_params, [], "s0")
    ce1 = calc.calculate_control_enhanced_risk(simple_fair_params, [], "s1")
    assert ce0.spawn_key == (0,)
    assert ce1.spawn_key == (1,)
    assert not np.array_equal(ce0.base_risk.simulation_results, ce1.base_risk.simulation_results)
