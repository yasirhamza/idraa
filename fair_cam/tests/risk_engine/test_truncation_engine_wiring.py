"""Engine wiring pins for PR2 capacity bound (Task 2): the LOGNORMAL branch
of `FAIRDistribution.sample` with an optional `max` key, and the
truncate-then-subtract ordering through `FAIREngine.calculate_risk`.

The formula itself is pinned in `test_truncation.py`; this file pins that
`fair_core.py` wires it in correctly (delegation, not a re-derivation or a
clamp) and that the currency subtractor composes AFTER truncation, not
before or instead of it.
"""

from __future__ import annotations

import math

import numpy as np

from fair_cam.risk_engine._truncation import truncated_lognormal
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIREngine,
    FAIRParameters,
)

_MEANLOG = math.log(1e6)
_SIGMA = 1.7
_MAX = 1e9


def _lognormal_dist(*, max_value: float | None = None) -> FAIRDistribution:
    params: dict[str, float] = {"mean": _MEANLOG, "sigma": _SIGMA}
    if max_value is not None:
        params["max"] = max_value
    return FAIRDistribution(DistributionType.LOGNORMAL, params)


def test_delegation_pin_lognormal_with_max_matches_direct_call():
    """`FAIRDistribution.sample()` with `max` present must be
    `np.array_equal` to a direct `truncated_lognormal` call at the same
    seed -- kills clamp (np.minimum) and swapped-arg implementations,
    which can pass a `max() <= cap` assertion while producing different
    values."""
    n = 50_000
    dist = _lognormal_dist(max_value=_MAX)

    engine_samples = dist.sample(size=n, rng=np.random.default_rng(4242))
    direct_samples = truncated_lognormal(np.random.default_rng(4242), _MEANLOG, _SIGMA, n, _MAX)

    np.testing.assert_array_equal(engine_samples, direct_samples)


def test_lognormal_without_max_is_byte_identical_to_pre_pr2_path():
    """Absent `max`, the LOGNORMAL branch must be byte-identical to the
    pre-PR2 `rng.lognormal` call at the same seed -- PR2 must not touch
    the untruncated stream."""
    n = 50_000
    dist = _lognormal_dist(max_value=None)

    engine_samples = dist.sample(size=n, rng=np.random.default_rng(17))
    pre_pr2_samples = np.random.default_rng(17).lognormal(_MEANLOG, _SIGMA, n)

    np.testing.assert_array_equal(engine_samples, pre_pr2_samples)


def test_multi_component_mixture_without_max_is_byte_identical_to_pre_pr2_path():
    """Absent `max`, LOGNORMAL_MIXTURE's multi-component branch must also
    be byte-identical to the pre-PR2 rng.choice + rng.lognormal(gather)
    path at the same seed -- this branch's uncapped code path is
    unmodified (same lines as before PR2), and the pre-existing
    fair_cam/tests/risk_engine/test_mixture_sampling.py suite continues to
    exercise it; this is a dedicated regression pin for the "without max"
    half of the criterion."""
    n = 50_000
    components = [
        {"mean": 8.0, "sigma": 0.5, "weight": 0.3},
        {"mean": 10.0, "sigma": 0.8, "weight": 0.5},
        {"mean": 12.0, "sigma": 0.3, "weight": 0.2},
    ]
    dist = FAIRDistribution(DistributionType.LOGNORMAL_MIXTURE, {"components": components})

    engine_samples = dist.sample(size=n, rng=np.random.default_rng(31))

    rng = np.random.default_rng(31)
    mean_arr = np.array([c["mean"] for c in components])
    sigma_arr = np.array([c["sigma"] for c in components])
    weight_arr = np.array([c["weight"] for c in components])
    idx = rng.choice(3, size=n, p=weight_arr)
    pre_pr2_samples = rng.lognormal(mean_arr[idx], sigma_arr[idx], size=n)

    np.testing.assert_array_equal(engine_samples, pre_pr2_samples)


def _const(v: float) -> FAIRDistribution:
    return FAIRDistribution(DistributionType.UNIFORM, {"low": v, "high": v})


def test_secondary_loss_truncation_applies_before_subtractor():
    """The `max` support bound on secondary_loss truncates the RAW draw;
    the currency subtractor (`calculate_risk`'s `secondary_loss_subtractor`)
    then applies to that ALREADY-TRUNCATED value, never the other way
    round. Pinned via array_equal against a manual replay of the engine's
    exact rng-consumption order (tef, vulnerability, primary_loss,
    secondary_loss -- the dict-literal construction order in
    `_generate_samples`), which itself calls `truncated_lognormal`
    directly (not a re-derivation)."""
    seed = 99
    n = 20_000
    subtractor = 50_000.0

    p = FAIRParameters(
        threat_event_frequency=_const(1.0),
        vulnerability=_const(1.0),
        primary_loss=_const(0.0),
        secondary_loss=_lognormal_dist(max_value=_MAX),
    )
    eng = FAIREngine(iterations=n, random_seed=seed)
    res = eng.calculate_risk(p, secondary_loss_subtractor=subtractor)

    # Replay the exact same rng-consumption order calculate_risk's
    # _generate_samples uses (dict-literal evaluation order: tef,
    # vulnerability, primary_loss, secondary_loss), so the secondary_loss
    # draw lands on the identical rng state.
    rng_replay = np.random.default_rng(seed)
    rng_replay.uniform(1.0, 1.0, n)  # tef
    rng_replay.uniform(1.0, 1.0, n)  # vulnerability
    rng_replay.uniform(0.0, 0.0, n)  # primary_loss
    raw_secondary_loss = truncated_lognormal(rng_replay, _MEANLOG, _SIGMA, n, _MAX)

    expected_loss_magnitude = np.maximum(0.0, np.maximum(raw_secondary_loss, 0.0) - subtractor)

    np.testing.assert_array_equal(expected_loss_magnitude, res["loss_magnitude_distribution"])
    # Sanity: truncation actually bound the raw draw below `max` -- if it
    # hadn't (e.g. subtractor applied to an untruncated draw with no cap
    # at all), this wouldn't hold and the ordering pin would be vacuous.
    assert raw_secondary_loss.max() < _MAX
