# fair_cam/tests/risk_engine/test_capacity_bound_scaling.py
"""PR2 capacity bound (Task 3): `_scale_distribution` threading `max` through
its LOGNORMAL and LOGNORMAL_MIXTURE branches.

`_scale_distribution` is the residual-scaler helper behind
`FAIRParameters.scaled` -- before this task it rebuilt param dicts from a
hardcoded key list (mean/sigma or components), silently dropping an
optional top-level `max` even when present on the input distribution.

Scale-equivariance rationale (why `max` is multiplied by the same
multiplier as the real-space scale, not left fixed): real-space scaling by
`k` shifts the log-space meanlog by `+ln(k)` (existing LOGNORMAL branch).
The truncated-lognormal sampler's boundary parameter is
`b = (ln(max) - meanlog) / sigma` (fair_cam/risk_engine/_truncation.py).
Substituting `meanlog' = meanlog + ln(k)` and `max' = k * max`:

    ln(max') - meanlog' = ln(k) + ln(max) - meanlog - ln(k)
                         = ln(max) - meanlog

so `b' == b` exactly (up to float rounding) -- the truncation point in
STANDARDIZED space never moves, and the truncated residual equals `k`
times the truncated inherent draw. This is NOT "otherwise uncapped" (that
phrasing is false): failing to scale `max` would leave the cap fixed in
real-space while the distribution's mass shifts underneath it, silently
changing how much of the tail the cap removes.

The `mult == 0` (uniform-zero, perfect control) case never reaches this
function -- `FAIRParameters.apply_node_multipliers`'s `_node` helper
short-circuits a zero multiplier to a `UNIFORM(0, 0)` point mass before
`_scale_distribution` is ever called (`_scale_distribution` cannot
represent a real-space-zero distribution in log-space, `log(0)`), so no
`max` handling is needed for that path.
"""

from __future__ import annotations

import math

import pytest

from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    _scale_distribution,
)

_MEANLOG = math.log(1_000_000.0)
_SIGMA = 1.7
_MAX = 1e9

# N = 3 components (adapter-iteration contract minimum) -- mirrors the
# shared fixture in test_mixture_sampling.py.
_MIX_COMPONENTS = [
    {"mean": 8.0, "sigma": 0.5, "weight": 0.3},
    {"mean": 10.0, "sigma": 0.8, "weight": 0.5},
    {"mean": 12.0, "sigma": 0.3, "weight": 0.2},
]


def _lognormal(max_value: float | None) -> FAIRDistribution:
    params: dict[str, float] = {"mean": _MEANLOG, "sigma": _SIGMA}
    if max_value is not None:
        params["max"] = max_value
    return FAIRDistribution(DistributionType.LOGNORMAL, params)


def _mixture(components: list[dict[str, float]], max_value: float | None) -> FAIRDistribution:
    params: dict[str, object] = {"components": components}
    if max_value is not None:
        params["max"] = max_value
    return FAIRDistribution(DistributionType.LOGNORMAL_MIXTURE, params)


# ---- max scales by the multiplier ------------------------------------------


def test_lognormal_max_scales_by_multiplier():
    multiplier = 3.0
    scaled = _scale_distribution(_lognormal(_MAX), multiplier)
    expected = _MAX * multiplier
    print(f"lognormal max: expected={expected!r} vs actual={scaled.parameters['max']!r}")
    assert scaled.parameters["max"] == pytest.approx(expected, rel=1e-12)


def test_mixture_max_scales_by_multiplier_and_preserves_all_n_components():
    """Adapter-iteration contract: N=3 components in -> N=3 components out
    (not a `[0]`/`[-1]` shortcut), each with its scaled meanlog; the shared
    top-level `max` also scales."""
    multiplier = 1.6
    components = [dict(c) for c in _MIX_COMPONENTS]
    scaled = _scale_distribution(_mixture(components, _MAX), multiplier)

    scaled_components = scaled.parameters["components"]
    assert len(scaled_components) == len(_MIX_COMPONENTS) == 3

    for orig_c, new_c in zip(_MIX_COMPONENTS, scaled_components, strict=True):
        assert math.isclose(new_c["mean"], orig_c["mean"] + math.log(multiplier), rel_tol=1e-12)
        assert math.isclose(new_c["sigma"], orig_c["sigma"], rel_tol=1e-12)
        assert math.isclose(new_c["weight"], orig_c["weight"], rel_tol=1e-12)

    expected_max = _MAX * multiplier
    print(f"mixture max: expected={expected_max!r} vs actual={scaled.parameters['max']!r}")
    assert scaled.parameters["max"] == pytest.approx(expected_max, rel=1e-12)


# ---- absent max stays absent ------------------------------------------------


def test_lognormal_without_max_emits_no_max_key():
    scaled = _scale_distribution(_lognormal(None), 2.0)
    assert "max" not in scaled.parameters


def test_mixture_without_max_emits_no_max_key():
    components = [dict(c) for c in _MIX_COMPONENTS]
    scaled = _scale_distribution(_mixture(components, None), 2.0)
    assert "max" not in scaled.parameters


# ---- b-invariance property test: the REASON max scales ---------------------


@pytest.mark.parametrize("multiplier", [0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 37.5, 1000.0])
def test_lognormal_b_invariant_under_scaling(multiplier: float):
    """b = (ln(max) - meanlog) / sigma is the exact boundary parameter the
    truncated-lognormal inverse-CDF formula is built on (_truncation.py:
    `b = (ln(max_value) - meanlog) / sigma`). Scale-equivariance means b
    computed from the SCALED distribution's (max, meanlog, sigma) must
    equal b computed from the ORIGINAL distribution's, to ~1e-12 -- this
    pins the REASON max is multiplied by the multiplier (not left fixed,
    not scaled by some other rule)."""
    b_original = (math.log(_MAX) - _MEANLOG) / _SIGMA

    scaled = _scale_distribution(_lognormal(_MAX), multiplier)
    b_scaled = (math.log(scaled.parameters["max"]) - scaled.parameters["mean"]) / scaled.parameters[
        "sigma"
    ]

    print(
        f"multiplier={multiplier}: b expected(original)={b_original!r} "
        f"vs actual(scaled)={b_scaled!r}"
    )
    assert math.isclose(b_scaled, b_original, rel_tol=0, abs_tol=1e-12)


@pytest.mark.parametrize("multiplier", [0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 37.5, 1000.0])
def test_mixture_b_invariant_under_scaling_for_every_component(multiplier: float):
    """Same b-invariance property, checked independently for EVERY
    component (adapter-iteration contract extended to the property test
    itself -- a bug that only broke e.g. the last component's meanlog
    shift would otherwise slip through a single-component check)."""
    components = [dict(c) for c in _MIX_COMPONENTS]
    scaled = _scale_distribution(_mixture(components, _MAX), multiplier)
    scaled_max = scaled.parameters["max"]

    for orig_c, new_c in zip(_MIX_COMPONENTS, scaled.parameters["components"], strict=True):
        b_original = (math.log(_MAX) - orig_c["mean"]) / orig_c["sigma"]
        b_scaled = (math.log(scaled_max) - new_c["mean"]) / new_c["sigma"]
        print(
            f"multiplier={multiplier} component mean={orig_c['mean']}: "
            f"b expected(original)={b_original!r} vs actual(scaled)={b_scaled!r}"
        )
        assert math.isclose(b_scaled, b_original, rel_tol=0, abs_tol=1e-12)
