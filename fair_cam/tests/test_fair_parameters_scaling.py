"""Unit tests for FAIRParameters.scaled — frequency + magnitude multipliers."""

from __future__ import annotations

import math

import numpy as np
import pytest


def _make_base_fair_parameters():
    """Construct a FAIRParameters with simple known values for scaling tests."""
    from fair_cam.risk_engine.fair_core import (
        DistributionType,
        FAIRDistribution,
        FAIRParameters,
    )

    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.TRIANGULAR,
            {"low": 0.3, "mode": 1.0, "high": 2.5},
        ),
        vulnerability=FAIRDistribution(
            DistributionType.TRIANGULAR,
            {"low": 0.2, "mode": 0.4, "high": 0.7},
        ),
        primary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL,
            {"mean": np.log(1_000_000.0), "sigma": 1.2},  # log-space
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL,
            {"mean": np.log(300_000.0), "sigma": 1.5},
        ),
    )


def test_scaled_identity_returns_equivalent_parameters():
    """scaling by (1.0, 1.0) returns parameters numerically identical to base."""
    base = _make_base_fair_parameters()
    scaled = base.scaled(frequency_multiplier=1.0, magnitude_multiplier=1.0)

    assert scaled.threat_event_frequency.parameters == base.threat_event_frequency.parameters
    assert scaled.vulnerability.parameters == base.vulnerability.parameters
    assert scaled.primary_loss.parameters == base.primary_loss.parameters
    assert scaled.secondary_loss.parameters == base.secondary_loss.parameters


def test_scaled_frequency_multiplies_tef_triangular():
    """frequency_multiplier=2.0 doubles low/mode/high of TEF triangular."""
    base = _make_base_fair_parameters()
    scaled = base.scaled(frequency_multiplier=2.0, magnitude_multiplier=1.0)

    assert math.isclose(scaled.threat_event_frequency.parameters["low"], 0.6)
    assert math.isclose(scaled.threat_event_frequency.parameters["mode"], 2.0)
    assert math.isclose(scaled.threat_event_frequency.parameters["high"], 5.0)
    # vulnerability unchanged
    assert scaled.vulnerability.parameters == base.vulnerability.parameters


def test_scaled_magnitude_shifts_lognormal_mean_in_log_space():
    """magnitude_multiplier=2.0 means real-space loss doubles → add ln(2) to log-space mean."""
    base = _make_base_fair_parameters()
    scaled = base.scaled(frequency_multiplier=1.0, magnitude_multiplier=2.0)

    expected_pl_mean = np.log(1_000_000.0) + math.log(2.0)
    expected_sl_mean = np.log(300_000.0) + math.log(2.0)
    assert math.isclose(scaled.primary_loss.parameters["mean"], expected_pl_mean)
    assert math.isclose(scaled.secondary_loss.parameters["mean"], expected_sl_mean)
    # sigma unchanged (shape preserved)
    assert math.isclose(scaled.primary_loss.parameters["sigma"], 1.2)
    assert math.isclose(scaled.secondary_loss.parameters["sigma"], 1.5)


def test_scaled_handles_pert_distribution():
    """PERT loss distribution scales low/mode/high by magnitude_multiplier."""
    from fair_cam.risk_engine.fair_core import (
        DistributionType,
        FAIRDistribution,
        FAIRParameters,
    )

    params = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.TRIANGULAR, {"low": 0.5, "mode": 1.0, "high": 2.0}
        ),
        vulnerability=FAIRDistribution(
            DistributionType.TRIANGULAR, {"low": 0.2, "mode": 0.4, "high": 0.7}
        ),
        primary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 100_000.0, "mode": 500_000.0, "high": 2_000_000.0}
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 50_000.0, "mode": 200_000.0, "high": 800_000.0}
        ),
    )
    scaled = params.scaled(frequency_multiplier=1.0, magnitude_multiplier=3.0)

    assert math.isclose(scaled.primary_loss.parameters["low"], 300_000.0)
    assert math.isclose(scaled.primary_loss.parameters["mode"], 1_500_000.0)
    assert math.isclose(scaled.primary_loss.parameters["high"], 6_000_000.0)


def test_scaled_rejects_non_positive_multipliers():
    """Negative or zero multipliers are physical nonsense — must raise."""
    base = _make_base_fair_parameters()
    with pytest.raises(ValueError):
        base.scaled(frequency_multiplier=0.0, magnitude_multiplier=1.0)
    with pytest.raises(ValueError):
        base.scaled(frequency_multiplier=-1.0, magnitude_multiplier=1.0)
    with pytest.raises(ValueError):
        base.scaled(frequency_multiplier=1.0, magnitude_multiplier=0.0)


def test_scaled_rejects_non_finite_multipliers():
    """inf/nan multipliers must raise."""
    base = _make_base_fair_parameters()
    with pytest.raises(ValueError):
        base.scaled(frequency_multiplier=math.inf, magnitude_multiplier=1.0)
    with pytest.raises(ValueError):
        base.scaled(frequency_multiplier=1.0, magnitude_multiplier=math.nan)


def test_scaled_preserves_optional_fields_unchanged():
    """contact_frequency, action_frequency, threat_capability, resistance_strength
    are NOT touched by frequency/magnitude scaling — they're modeling overlays
    for richer threat-community analysis, not loss-event drivers."""
    from fair_cam.risk_engine.fair_core import (
        DistributionType,
        FAIRDistribution,
        FAIRParameters,
    )

    cf = FAIRDistribution(DistributionType.TRIANGULAR, {"low": 1, "mode": 5, "high": 10})
    base = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.TRIANGULAR, {"low": 0.3, "mode": 1.0, "high": 2.5}
        ),
        vulnerability=FAIRDistribution(
            DistributionType.TRIANGULAR, {"low": 0.2, "mode": 0.4, "high": 0.7}
        ),
        primary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL, {"mean": np.log(1_000_000.0), "sigma": 1.2}
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL, {"mean": np.log(300_000.0), "sigma": 1.5}
        ),
        contact_frequency=cf,
    )
    scaled = base.scaled(frequency_multiplier=2.0, magnitude_multiplier=2.0)
    # contact_frequency carried through identically
    assert scaled.contact_frequency is not None
    assert scaled.contact_frequency.parameters == cf.parameters
