"""Milestone B gate (spec §4): a mode==low PERT — the shape EVERY capped
library loss node has after conversion (analytic lognormal mode < p5 for all
sigma > 1.645) — must pass v3 validation and sample correctly through the
native engine (pyfair-matched Vose moment form: Beta(2/3, 10/3), density
rising toward the low bound, mean = (5*low+high)/6)."""

from __future__ import annotations

import numpy as np
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution

# Real converted values: ransomware-on-ehr primary loss (pinned table).
_LOW, _HIGH = 15955.6628554057, 10080000.000343738


def test_mode_at_low_pert_samples_with_correct_moments() -> None:
    dist = FAIRDistribution(
        distribution_type=DistributionType.PERT,
        parameters={"low": _LOW, "mode": _LOW, "high": _HIGH},
    )
    rng = np.random.default_rng(42)
    samples = dist.sample(200_000, rng)
    assert samples.min() >= _LOW and samples.max() <= _HIGH
    # fair_core's pyfair-matched Vose moment form (gamma=4) at mode==low gives
    # Beta(2/3, 10/3) (alpha<1: density rises toward low). The mean is
    # (5*low + high)/6 in both the classic and moment-matched forms.
    expected_mean = (5 * _LOW + _HIGH) / 6
    assert abs(samples.mean() - expected_mean) / expected_mean < 0.02
    # Right-skew sanity: median well below mean.
    assert np.median(samples) < samples.mean()


def test_mode_at_low_pert_passes_storage_validation() -> None:
    from idraa.services.fair_cam_validation import validate_fair_distributions

    node = {"distribution": "PERT", "low": _LOW, "mode": _LOW, "high": _HIGH}
    # Must NOT raise (errors raise FAIRCAMValidationError; warnings return).
    result = validate_fair_distributions(
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 1.5, "high": 4.0},
        vulnerability={"low": 0.1, "mode": 0.2, "high": 0.5},
        primary_loss=node,
        secondary_loss=None,
    )
    assert result.errors == []
