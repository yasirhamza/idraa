import math

import numpy as np
import pytest

from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters

ID = {
    "threat_event_frequency": 1.0,
    "vulnerability": 1.0,
    "primary_loss": 1.0,
    "secondary_loss": 1.0,
}


def _params():
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 1, "mode": 2, "high": 4}
        ),
        vulnerability=FAIRDistribution(
            DistributionType.PERT, {"low": 0.1, "mode": 0.3, "high": 0.5}
        ),
        primary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 100, "mode": 500, "high": 2000}
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 50, "mode": 250, "high": 1000}
        ),
    )


def test_returns_params_and_vuln_knob():
    out, vuln_mult = _params().apply_node_multipliers({**ID, "vulnerability": 0.4})
    assert isinstance(out, FAIRParameters)
    assert vuln_mult == 0.4  # returned, NOT applied to the params


def test_tef_and_loss_scaled_vuln_params_untouched():
    out, vuln_mult = _params().apply_node_multipliers(
        {
            "threat_event_frequency": 0.5,
            "vulnerability": 0.4,
            "primary_loss": 0.5,
            "secondary_loss": 0.25,
        }
    )
    assert out.threat_event_frequency.parameters["mode"] == 1.0
    assert out.primary_loss.parameters["high"] == 1000.0
    assert out.secondary_loss.parameters["low"] == 12.5
    assert out.vulnerability.parameters == _params().vulnerability.parameters  # param untouched
    assert vuln_mult == 0.4


def test_lognormal_loss_log_shift():
    p = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 1, "mode": 2, "high": 4}
        ),
        vulnerability=FAIRDistribution(
            DistributionType.PERT, {"low": 0.1, "mode": 0.3, "high": 0.5}
        ),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 10.0, "sigma": 1.0}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )
    out, _ = p.apply_node_multipliers({**ID, "primary_loss": 0.5})
    assert out.primary_loss.parameters["mean"] == 10.0 + math.log(0.5)
    assert out.primary_loss.parameters["sigma"] == 1.0


def test_zero_multiplier_becomes_point_mass_at_zero():
    out, _ = _params().apply_node_multipliers({**ID, "primary_loss": 0.0})
    assert out.primary_loss.distribution_type == DistributionType.UNIFORM
    assert out.primary_loss.parameters == {"low": 0.0, "high": 0.0}
    s = out.primary_loss.sample(1000, rng=np.random.default_rng(0))
    assert np.all(s == 0.0)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -0.1])
def test_rejects_non_finite_or_negative_multiplier(bad):
    with pytest.raises(ValueError):
        _params().apply_node_multipliers({**ID, "threat_event_frequency": bad})
