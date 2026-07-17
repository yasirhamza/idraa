"""Sanity checks for fair_cam's native FAIREngine / FAIRDistribution samplers.

These distributions were added outside pyfair's original scope; the native
engine (epic #324) hardens them against the bugs identified in the plan review.

NOTE: the legacy ``DistributionSampler`` (fair_cam.distributions.sampler) and
the ``FAIRRiskAggregationEngine`` it was extracted from were deleted in epic
#324 Task 10 (zero production callers; the native ``FAIREngine`` /
``FAIRDistribution`` path is the only live sampler). The
``TestAggregationEngineSamplers`` class that exercised the deleted
``_generate_distribution_samples`` / ``_generate_pert_samples`` private methods
went with them — its native-path analogues are the integrity-critical leaves
driving Monte Carlo inputs, and live coverage for them is below
(``FAIRDistribution.sample`` PERT validation, the BETA loss barrier, and the
per-instance-RNG guarantee)."""

from __future__ import annotations

import numpy as np
import pytest
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIREngine,
    FAIRParameters,
)


class TestFAIREngineBetaBarrier:
    """Task #22: FAIREngine.calculate_risk must reject BETA for loss fields."""

    def _make_params(
        self,
        primary_dist: FAIRDistribution | None = None,
        secondary_dist: FAIRDistribution | None = None,
    ) -> FAIRParameters:
        """Build a minimal valid FAIRParameters with overridable loss fields."""
        default_loss = FAIRDistribution(
            DistributionType.PERT, {"low": 1000, "mode": 5000, "high": 10000}
        )
        tef = FAIRDistribution(DistributionType.PERT, {"low": 0.1, "mode": 1.0, "high": 5.0})
        vuln = FAIRDistribution(DistributionType.UNIFORM, {"low": 0.1, "high": 0.5})
        return FAIRParameters(
            threat_event_frequency=tef,
            vulnerability=vuln,
            primary_loss=primary_dist or default_loss,
            secondary_loss=secondary_dist or default_loss,
        )

    def test_beta_primary_loss_rejected(self) -> None:
        engine = FAIREngine(iterations=100, random_seed=1)
        params = self._make_params(
            primary_dist=FAIRDistribution(DistributionType.BETA, {"alpha": 2, "beta": 5})
        )
        with pytest.raises(ValueError, match="BETA distribution is unsuitable"):
            engine.calculate_risk(params)

    def test_beta_secondary_loss_rejected(self) -> None:
        engine = FAIREngine(iterations=100, random_seed=1)
        params = self._make_params(
            secondary_dist=FAIRDistribution(DistributionType.BETA, {"alpha": 2, "beta": 5})
        )
        with pytest.raises(ValueError, match="BETA distribution is unsuitable"):
            engine.calculate_risk(params)

    def test_pert_loss_still_works(self) -> None:
        """Regression: the barrier must not break PERT loss fields."""
        engine = FAIREngine(iterations=100, random_seed=1)
        params = self._make_params()  # default PERT
        result = engine.calculate_risk(params)
        assert "risk_distribution" in result
        assert result["ale_mean"] >= 0


class TestFAIRDistributionSampler:
    def test_pert_equal_low_high_returns_constant(self) -> None:
        """A3 (fair_core.py): equal low/high returns constant."""
        d = FAIRDistribution(DistributionType.PERT, {"low": 5.0, "mode": 5.0, "high": 5.0})
        samples = d.sample(100)
        assert np.all(samples == 5.0)

    def test_pert_rejects_mode_out_of_range(self) -> None:
        d = FAIRDistribution(DistributionType.PERT, {"low": 0, "mode": 100, "high": 10})
        with pytest.raises(ValueError, match="mode must be in"):
            d.sample(100)


class TestFAIREngineRng:
    """Task #23 (completeness): FAIREngine must use a per-instance Generator.

    Commit A migrated only FAIRRiskAggregationEngine; Commit A.1 completes
    the migration for FAIREngine + FAIRDistribution.sample(). Mirrors
    TestAggregationEngineSamplers.test_per_instance_rng_is_used.
    """

    @staticmethod
    def _build_params() -> FAIRParameters:
        """Cheap, deterministic-friendly FAIRParameters for regression runs."""
        return FAIRParameters(
            threat_event_frequency=FAIRDistribution(
                DistributionType.PERT, {"low": 0.5, "mode": 2.0, "high": 5.0}
            ),
            vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.1, "high": 0.5}),
            # LOGNORMAL (not BETA — that's barred for loss fields).
            primary_loss=FAIRDistribution(
                DistributionType.LOGNORMAL, {"mean": np.log(1000.0), "sigma": 0.3}
            ),
            secondary_loss=FAIRDistribution(
                DistributionType.LOGNORMAL, {"mean": np.log(500.0), "sigma": 0.3}
            ),
        )

    def test_engine_has_rng_attribute(self) -> None:
        """FAIREngine exposes a _rng Generator rather than mutating global state."""
        eng = FAIREngine(iterations=100, random_seed=1)
        assert isinstance(eng._rng, np.random.Generator)

    def test_fair_engine_uses_per_instance_rng(self) -> None:
        """FAIREngine must not rely on numpy global state.

        Regression for Task #23 completeness: if FAIREngine still called
        np.random.seed(), perturbing the global state between two
        same-seeded engines would desync their outputs.
        """
        eng1 = FAIREngine(iterations=100, random_seed=12345)
        # Deliberately perturb numpy's GLOBAL state between constructions —
        # if the engine or sampler still uses np.random.*, eng2 diverges.
        np.random.seed(999)
        _ = np.random.normal(0, 1, 1000)
        eng2 = FAIREngine(iterations=100, random_seed=12345)

        r1 = eng1.calculate_risk(self._build_params())
        r2 = eng2.calculate_risk(self._build_params())
        np.testing.assert_array_equal(r1["risk_distribution"], r2["risk_distribution"])

    def test_seed_zero_is_honored(self) -> None:
        """random_seed=0 must be honored (not silently skipped by falsy check).

        Regression for the `if random_seed:` → `if random_seed is not None:`
        fix. Two engines seeded with 0 must produce identical outputs.
        """
        eng1 = FAIREngine(iterations=100, random_seed=0)
        eng2 = FAIREngine(iterations=100, random_seed=0)
        r1 = eng1.calculate_risk(self._build_params())
        r2 = eng2.calculate_risk(self._build_params())
        np.testing.assert_array_equal(r1["risk_distribution"], r2["risk_distribution"])
