import numpy as np
import pytest

from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIREngine,
    FAIRParameters,
)


def _const_params(*, tef, vuln, pl, sl):
    def c(v):
        return FAIRDistribution(DistributionType.UNIFORM, {"low": v, "high": v})

    return FAIRParameters(
        threat_event_frequency=c(tef),
        vulnerability=c(vuln),
        primary_loss=c(pl),
        secondary_loss=c(sl),
    )


def test_subtractor_zero_is_noop():
    eng = FAIREngine(iterations=10_000, random_seed=1)
    p = _const_params(tef=1.0, vuln=1.0, pl=0.0, sl=1000.0)
    base = eng.calculate_risk(p)
    sub = FAIREngine(iterations=10_000, random_seed=1).calculate_risk(
        p, secondary_loss_subtractor=0.0
    )
    assert np.allclose(base["risk_distribution"], sub["risk_distribution"])


def test_subtractor_shifts_then_floors_at_zero():
    eng = FAIREngine(iterations=5_000, random_seed=2)
    p = _const_params(tef=1.0, vuln=1.0, pl=0.0, sl=1000.0)
    res = eng.calculate_risk(p, secondary_loss_subtractor=300.0)
    assert np.allclose(res["loss_magnitude_distribution"], 700.0)


def test_subtractor_floors_negative_at_zero():
    eng = FAIREngine(iterations=2_000, random_seed=3)
    p = _const_params(tef=1.0, vuln=1.0, pl=0.0, sl=100.0)
    res = eng.calculate_risk(p, secondary_loss_subtractor=500.0)
    assert np.allclose(res["loss_magnitude_distribution"], 0.0)


def test_vulnerability_multiplier_scales_lef():
    eng = FAIREngine(iterations=20_000, random_seed=4)
    p = _const_params(tef=4.0, vuln=1.0, pl=1000.0, sl=0.0)
    full = eng.calculate_risk(p)
    quart = FAIREngine(iterations=20_000, random_seed=4).calculate_risk(
        p, vulnerability_multiplier=0.25
    )
    assert np.isclose(quart["ale_mean"], 0.25 * full["ale_mean"], rtol=1e-9)


def test_vulnerability_multiplier_clips_above_one():
    eng = FAIREngine(iterations=10_000, random_seed=5)
    p = _const_params(tef=1.0, vuln=0.8, pl=100.0, sl=0.0)
    res = eng.calculate_risk(p, vulnerability_multiplier=2.0)
    assert np.allclose(res["lef_distribution"], 1.0)


def test_vulnerability_multiplier_zero_yields_zero_lef():
    # Perfect vulnerability control (node multiplier == 0). Must NOT raise.
    eng = FAIREngine(iterations=5_000, random_seed=8)
    p = _const_params(tef=3.0, vuln=0.7, pl=1000.0, sl=0.0)
    res = eng.calculate_risk(p, vulnerability_multiplier=0.0)
    assert np.allclose(res["lef_distribution"], 0.0)
    assert np.allclose(res["risk_distribution"], 0.0)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1.0])
def test_subtractor_rejects_non_finite_or_negative(bad):
    eng = FAIREngine(iterations=100, random_seed=6)
    p = _const_params(tef=1.0, vuln=1.0, pl=0.0, sl=1000.0)
    with pytest.raises(ValueError):
        eng.calculate_risk(p, secondary_loss_subtractor=bad)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -0.5])
def test_vuln_multiplier_rejects_non_finite_or_negative(bad):
    eng = FAIREngine(iterations=100, random_seed=7)
    p = _const_params(tef=1.0, vuln=1.0, pl=0.0, sl=1000.0)
    with pytest.raises(ValueError):
        eng.calculate_risk(p, vulnerability_multiplier=bad)


def test_finite_guard_rejects_non_finite_risk():
    # A pathological lognormal tail (huge sigma) overflows to inf; the engine
    # must REFUSE to emit a corrupt distribution (Sec-B1 / #307 class).
    p = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 20.0, "sigma": 200.0}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )
    eng = FAIREngine(iterations=100_000, random_seed=9)
    with pytest.raises(ValueError, match="non-finite"):
        eng.calculate_risk(p)
