"""Engine-consistency regression test (Task 3 D2, Epic B #326).

Proves that the closed-form lognormal_from_quantiles -> _dict_to_fair_distribution
-> FAIRDistribution.sample pipeline lands empirical p5/p95 on the entered
low/high interval within 5% relative tolerance.

Guards against any future regression where a truncated-fit path or parameter
mis-wiring causes sampled percentiles to drift off the analyst's entered bounds.
"""

from __future__ import annotations

import numpy as np
import pytest
from fair_cam.quantile_pooling import lognormal_from_quantiles

from idraa.services.run_executor import _dict_to_fair_distribution


def test_sampled_percentiles_match_entered_low_high():
    # Entering low=p5=1000, high=p95=1_000_000 must sample back to ~those p5/p95.
    params = lognormal_from_quantiles(1000.0, 1_000_000.0)
    dist = _dict_to_fair_distribution({"distribution": "lognormal", **params})
    rng = np.random.default_rng(12345)
    samples = dist.sample(200_000, rng=rng)
    p5, p95 = np.percentile(samples, [5, 95])
    assert p5 == pytest.approx(1000.0, rel=0.05)
    assert p95 == pytest.approx(1_000_000.0, rel=0.05)
