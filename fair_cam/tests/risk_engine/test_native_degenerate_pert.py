"""Degenerate PERT supports sample as constants in the native engine (#328).

Distilled from the retired ``test_pert_loss_reduction_inversion`` (whose
remaining assertions pinned the retired param-level
``_apply_control_adjustments`` path): the #258 crash class — pyfair's
``FairBetaPert`` rejecting a collapsed support — no longer exists. The native
sampler handles ``low == high`` by constant fill (``fair_core`` PERT branch),
so a fully-eliminated loss ({0,0,0}, e.g. a subtractor exceeding every bound
at sample level) and any collapsed support are finite, exact constants.
"""

from __future__ import annotations

import numpy as np

from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution


def _samples(low: float, mode: float, high: float, n: int = 5_000) -> np.ndarray:
    dist = FAIRDistribution(
        distribution_type=DistributionType.PERT,
        parameters={"low": low, "mode": mode, "high": high},
    )
    return dist.sample(n, np.random.default_rng(7))


def test_zero_collapsed_support_samples_constant_zero() -> None:
    s = _samples(0.0, 0.0, 0.0)
    assert np.all(s == 0.0) and np.all(np.isfinite(s))


def test_nonzero_collapsed_support_samples_constant() -> None:
    s = _samples(42_000.0, 42_000.0, 42_000.0)
    assert np.all(s == 42_000.0)
