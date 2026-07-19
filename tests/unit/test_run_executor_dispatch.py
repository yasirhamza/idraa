"""Unit tests for _dict_to_fair_distribution dispatch (Task 3, Epic B #326).

Verifies the lognormal branch and regression-guards the existing PERT branch.
"""

from __future__ import annotations

import pytest
from fair_cam.risk_engine.fair_core import DistributionType

from idraa.services.run_executor import _dict_to_fair_distribution


def test_lognormal_dispatch():
    d = _dict_to_fair_distribution({"distribution": "lognormal", "mean": 10.0, "sigma": 1.2})
    assert d.distribution_type == DistributionType.LOGNORMAL
    assert d.parameters == {"mean": 10.0, "sigma": 1.2}


def test_pert_dispatch_unregressed():
    d = _dict_to_fair_distribution({"distribution": "pert", "low": 1, "mode": 2, "high": 3})
    assert d.distribution_type == DistributionType.PERT
    assert d.parameters == {"low": 1.0, "mode": 2.0, "high": 3.0}


def test_lognormal_missing_key_raises():
    with pytest.raises((KeyError, ValueError)):
        _dict_to_fair_distribution({"distribution": "lognormal", "mean": 10.0})


# ---- lognormal_mixture dispatch (issue #27 Task 6) -------------------------


def test_lognormal_mixture_dispatch():
    """Stored mixture dict maps to a LOGNORMAL_MIXTURE FAIRDistribution with
    float-coerced component params -- the same type-coercion-only contract
    as the plain lognormal branch (validation happens upstream at store time)."""
    d = _dict_to_fair_distribution(
        {
            "distribution": "lognormal_mixture",
            "components": [
                {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
                {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
            ],
        }
    )
    assert d.distribution_type == DistributionType.LOGNORMAL_MIXTURE
    assert d.parameters == {
        "components": [
            {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
            {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
        ]
    }


def test_lognormal_mixture_int_components_coerced_to_float():
    """Components arriving as ints (e.g. from a hand-authored JSON import)
    coerce to float, mirroring the plain lognormal/PERT branches' contract."""
    d = _dict_to_fair_distribution(
        {
            "distribution": "lognormal_mixture",
            "components": [{"mean": 8, "sigma": 1, "weight": 1}],
        }
    )
    for c in d.parameters["components"]:
        assert isinstance(c["mean"], float)
        assert isinstance(c["sigma"], float)
        assert isinstance(c["weight"], float)


def test_lognormal_mixture_missing_component_key_raises():
    with pytest.raises((KeyError, ValueError)):
        _dict_to_fair_distribution(
            {
                "distribution": "lognormal_mixture",
                "components": [{"mean": 8.06, "sigma": 0.70}],
            }
        )


def test_lognormal_mixture_sampling_analytic_mean_matches():
    """Analytic-mean check at a fixed seed (Task 6 requirement): sampled mean
    from the mapped FAIRDistribution must track Sigma w_i * exp(mean_i +
    sigma_i**2/2) within tolerance -- proves the executor mapping produces a
    SAMPLING-CORRECT distribution, not just a structurally-shaped one.
    """
    import math

    import numpy as np

    mix_payload = {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
            {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
        ],
    }
    dist = _dict_to_fair_distribution(mix_payload)
    rng = np.random.default_rng(20260719)
    samples = dist.sample(400_000, rng=rng)

    analytic_mean = sum(
        c["weight"] * math.exp(c["mean"] + c["sigma"] ** 2 / 2.0) for c in mix_payload["components"]
    )
    empirical_mean = float(np.mean(samples))
    # side-by-side (verification-reporting convention). Gate-measured
    # rel_err at this pinned seed is ~0.64%; 2% leaves headroom without
    # being a no-op tolerance.
    print(f"analytic mean={analytic_mean:.4f}  empirical mean={empirical_mean:.4f}")
    assert empirical_mean == pytest.approx(analytic_mean, rel=0.02)
