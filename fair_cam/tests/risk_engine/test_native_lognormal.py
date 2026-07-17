# fair_cam/tests/risk_engine/test_native_lognormal.py
import math

import pytest

from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIREngine,
    FAIRParameters,
)

ID = {
    "threat_event_frequency": 1.0,
    "vulnerability": 1.0,
    "primary_loss": 1.0,
    "secondary_loss": 1.0,
}


@pytest.mark.slow
def test_lognormal_mean_matches_analytic_moment():
    mu, sigma = 10.0, 0.8
    p = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": mu, "sigma": sigma}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )
    res = FAIREngine(iterations=200_000, random_seed=11).calculate_risk(p)
    analytic = math.exp(mu + sigma**2 / 2)
    se = analytic * math.sqrt((math.exp(sigma**2) - 1) / 200_000)
    assert abs(res["ale_mean"] - analytic) < 3 * se


@pytest.mark.slow
def test_lognormal_scaling_property():
    mu, sigma, k = 9.0, 0.6, 0.4
    base = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": mu, "sigma": sigma}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )
    scaled, _ = base.apply_node_multipliers({**ID, "primary_loss": k})
    m_base = FAIREngine(iterations=200_000, random_seed=12).calculate_risk(base)["ale_mean"]
    m_scaled = FAIREngine(iterations=200_000, random_seed=12).calculate_risk(scaled)["ale_mean"]
    assert math.isclose(m_scaled, k * m_base, rel_tol=1e-9)


@pytest.mark.slow
def test_distributional_vuln_increases_variance_vs_constant():
    mean_v = 0.3
    spread = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.1, "high": 0.5}),
        primary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 1000.0, "high": 1000.0}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )
    const = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": mean_v, "high": mean_v}),
        primary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 1000.0, "high": 1000.0}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )
    vs = FAIREngine(iterations=200_000, random_seed=13).calculate_risk(spread)
    vc = FAIREngine(iterations=200_000, random_seed=14).calculate_risk(const)
    assert math.isclose(vs["ale_mean"], vc["ale_mean"], rel_tol=0.02)
    assert vs["ale_std"] > vc["ale_std"]
    # Closed-form: risk = 1000·Vuln, Vuln~U(0.1,0.5) ⟹ std(risk) = 1000·(0.5-0.1)/sqrt(12) ≈ 115.470
    assert math.isclose(vs["ale_std"], 115.470, rel_tol=0.01)
