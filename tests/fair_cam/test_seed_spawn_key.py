"""Tests for per-scenario spawn_key exposure and SeedSequence acceptance.

Step 1: RED (failing) — ControlEnhancedRisk has no spawn_key field yet,
and FAIREngine.__init__ annotation does not yet mention SeedSequence.
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
    """Minimal, fast FAIRParameters for engine tests."""
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


def test_control_enhanced_risk_carries_spawn_key(simple_fair_params: FAIRParameters) -> None:
    """ControlEnhancedRisk must carry the spawn_key from the child SeedSequence."""
    calc = NativeControlAwareRiskCalculator(n_simulations=200, random_seed=42)
    ce = calc.calculate_control_enhanced_risk(simple_fair_params, [], "s")
    # First spawn from SeedSequence(42) always produces spawn_key=(0,)
    assert ce.spawn_key == (0,)


def test_control_enhanced_risk_spawn_key_increments(simple_fair_params: FAIRParameters) -> None:
    """Second scenario spawned by the same calculator must get spawn_key=(1,)."""
    calc = NativeControlAwareRiskCalculator(n_simulations=200, random_seed=42)
    calc.calculate_control_enhanced_risk(simple_fair_params, [], "first")
    ce2 = calc.calculate_control_enhanced_risk(simple_fair_params, [], "second")
    assert ce2.spawn_key == (1,)


def test_spawn_key_default_none_when_not_set() -> None:
    """Default ControlEnhancedRisk (no spawn_key supplied) must have spawn_key=None."""
    from fair_cam.models.risk_enhanced import ControlEnhancedRisk

    ce = ControlEnhancedRisk()
    assert ce.spawn_key is None


def test_engine_accepts_seedsequence(simple_fair_params: FAIRParameters) -> None:
    """FAIREngine must accept a numpy SeedSequence as random_seed and produce
    identical results when given the same SeedSequence state."""
    ss = np.random.SeedSequence(entropy=42, spawn_key=(0,))
    a = FAIREngine(iterations=200, random_seed=ss).calculate_risk(simple_fair_params)
    ss2 = np.random.SeedSequence(entropy=42, spawn_key=(0,))
    b = FAIREngine(iterations=200, random_seed=ss2).calculate_risk(simple_fair_params)
    # calculate_risk returns a dict with key "risk_distribution"
    assert np.array_equal(a["risk_distribution"], b["risk_distribution"])
