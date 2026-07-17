"""End-to-end native-engine lognormal guard (Epic A #324, Task 8 Step 1).

A LOGNORMAL ``FAIRParameters`` samples through ``NativeControlAwareRiskCalculator``
to a finite, strict-JSON-serializable, heavy-tailed distribution — the property
the old ``FAIRParameters -> RiskParameters -> pyfair`` bridge destroyed by
flattening LOGNORMAL{mean, sigma} to an approximate PERT{p10, median, p90}
triplet (compressing the tail). This test guards the cutover: native must keep
the lognormal tail AND refuse to emit Infinity/NaN (Sec-N1, #307 class).
"""

import json

import numpy as np
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator


def test_lognormal_params_sample_without_flatten_and_serialize_strict():
    p = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 11.0, "sigma": 1.2}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )
    out = NativeControlAwareRiskCalculator(
        controls=[], n_simulations=100_000, random_seed=1
    ).calculate_control_enhanced_risk(p, [], "lognormal")
    arr = out.base_risk.simulation_results
    assert np.all(np.isfinite(arr))
    # Heavy tail preserved: a PERT flatten would compress p99/median far below 5x.
    assert np.percentile(arr, 99) > 5 * np.median(arr)
    # Strict JSON (allow_nan=False) round-trips — no Infinity/NaN reaches storage.
    assert json.loads(json.dumps(arr.tolist(), allow_nan=False)) is not None
