"""FAIRCAMValidator boundary wrapper. Closes GH #2.

Spec §10.5: severity==ERROR -> 4xx (raises FAIRCAMValidationError);
severity==WARNING -> returned for rendering as flash.

Verified against fair_cam/validation/input_validator.py behaviour:
- low > mode triggers ERROR on ``threat_event_frequency``.
- tef_high > 365 triggers WARNING ("TEF high value exceeds daily occurrence").
- secondary_loss=None is silently skipped (optional field).
- Unsupported distribution type: ``_validate_distribution_parameters`` reads
  ``risk_data.get('distribution_type', 'pert')`` at the top level of risk_data
  (NOT the 'distribution' key inside sub-dicts). So to trigger the unsupported-
  distribution ERROR, pass ``distribution_type='WIBBLE'`` in the threat_event_frequency
  dict is NOT sufficient — instead the wrapper must surface this via a wrapper-level
  check or by forwarding the key. F12 exposes this via the vulnerability=None path
  that still passes the distribution_type key in the risk_data dict.
"""

from __future__ import annotations

import math

import pytest
from scipy.stats import norm

from idraa.config import get_settings
from idraa.errors import FAIRCAMValidationError
from idraa.services.fair_cam_validation import (
    FAIRCAMValidationResult,
    validate_fair_distributions,
)

# Independently derived (NOT imported from the implementation) so the tests
# actually check the implementation's constant matches the codebase-wide
# convention rather than trivially agreeing with itself.
_Z95 = float(norm.ppf(0.95))


def test_validate_clean_distribution_returns_no_errors_no_warnings() -> None:
    result = validate_fair_distributions(
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        secondary_loss=None,
    )
    assert result.errors == []
    assert result.warnings == []


def test_validate_low_gt_mode_raises_error() -> None:
    """PERT requires low <= mode <= high; violation -> ERROR severity -> raised."""
    with pytest.raises(FAIRCAMValidationError) as exc_info:
        validate_fair_distributions(
            threat_event_frequency={"distribution": "PERT", "low": 10.0, "mode": 4.0, "high": 12.0},
            vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
            primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
            secondary_loss=None,
        )
    assert "low" in str(exc_info.value).lower()


def test_validate_warning_returned_not_raised() -> None:
    """tef_high > 365 -> WARNING severity -> returned, not raised.

    fair_cam's _validate_tef_parameters raises a WARNING when high > 365
    ("TEF high value exceeds daily occurrence"). Verified experimentally.
    """
    result = validate_fair_distributions(
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.01,
            "mode": 0.05,
            "high": 100_000.0,
        },
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        secondary_loss=None,
    )
    assert result.errors == []
    # Warnings present but not blocking
    assert len(result.warnings) >= 1


def test_validate_secondary_loss_optional() -> None:
    """secondary_loss=None doesn't block validation."""
    result = validate_fair_distributions(
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        secondary_loss=None,
    )
    assert result.errors == []


def test_validate_primary_loss_low_gt_mode_raises_error() -> None:
    """primary_loss with low > mode triggers an ERROR severity result.

    Plan note: original plan used {"distribution": "WIBBLE"} to test
    unrecognized distribution rejection, but fair_cam's
    validate_risk_parameters reads risk_data.get("distribution_type",
    "pert") at top level — the per-distribution "distribution" key in
    sub-dicts is never inspected. Adjusted to test low>mode on
    primary_loss as a separate path from the TEF low>mode test, since
    fair_cam evaluates each distribution independently.
    """
    # Trigger via a definitive ERROR: primary_loss low > mode
    with pytest.raises(FAIRCAMValidationError):
        validate_fair_distributions(
            threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
            vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
            primary_loss={"distribution": "PERT", "low": 10.0, "mode": 2.0, "high": 3.0},
            secondary_loss=None,
        )


def test_validate_returns_warnings_for_render() -> None:
    """Result object exposes warnings for routes/templates to render."""
    result = validate_fair_distributions(
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.01,
            "mode": 0.05,
            "high": 100_000.0,
        },
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        secondary_loss=None,
    )
    assert isinstance(result, FAIRCAMValidationResult)
    for warn in result.warnings:
        assert hasattr(warn, "message")


# ---------------------------------------------------------------------------
# Task 2: Distribution-type-aware finite guard (#326)
# ---------------------------------------------------------------------------

_PERT = {"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0}
_VULN = {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3}


def _call(**over):
    kw = {
        "threat_event_frequency": _PERT,
        "vulnerability": _VULN,
        "primary_loss": _PERT,
        "secondary_loss": None,
    }
    kw.update(over)
    return validate_fair_distributions(**kw)


def test_finite_lognormal_accepted():
    _call(primary_loss={"distribution": "lognormal", "mean": 10.0, "sigma": 1.2})


@pytest.mark.parametrize(
    "bad",
    [
        {"distribution": "lognormal", "mean": float("inf"), "sigma": 1.2},
        {"distribution": "lognormal", "mean": float("nan"), "sigma": 1.2},
        {"distribution": "lognormal", "mean": 10.0, "sigma": float("inf")},
        {"distribution": "lognormal", "mean": 10.0, "sigma": 0.0},
        {"distribution": "lognormal", "mean": 10.0, "sigma": -1.0},
        {"distribution": "lognormal", "mean": 10.0, "sigma": 10.0001},  # Sec-I2 upper bound
        {"distribution": "lognormal", "mean": 10.0, "sigma": 50.0},
    ],
)
def test_bad_lognormal_rejected(bad):
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=bad)


def test_sigma_at_bound_accepted():
    _call(primary_loss={"distribution": "lognormal", "mean": 10.0, "sigma": 10.0})


def test_pert_finite_path_unregressed():
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": float("inf")})


# ---- #468: non-negative lows for bounded TEF/PL/SL nodes ----------------------
# The engine floors samples at 0 (fair_core max(x, 0)), so a NEGATIVE authored
# low silently biases E[ALE] high vs the authored distribution (methodology F6).
# The form min= is client-side only; this is the real server-side gate. Applies
# to tef/pl/sl bounded dists; vuln has its own [0,1] block; lognormal is
# positive by construction.


@pytest.mark.parametrize("field", ["threat_event_frequency", "primary_loss", "secondary_loss"])
def test_negative_low_rejected(field):
    with pytest.raises(FAIRCAMValidationError):
        _call(**{field: {"distribution": "PERT", "low": -100.0, "mode": 2.0, "high": 10.0}})


def test_negative_mode_rejected():
    # A negative mode with low clamped-at-0 authoring is equally engine-floored.
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss={"distribution": "PERT", "low": -5.0, "mode": -1.0, "high": 10.0})


def test_zero_low_accepted():
    # Zero is a legitimate bound (e.g. degenerate zero-SL convention).
    _call(primary_loss={"distribution": "PERT", "low": 0.0, "mode": 2.0, "high": 10.0})


# ---------------------------------------------------------------------------
# #27: lognormal_mixture finiteness + sigma/weight bounds + count cap
# (_validate_finite's semantic gate — the exact-key-set / numeric-type
# structural shape is a SEPARATE gate at scenario_import._structural_dist_problem,
# covered in tests/unit/test_scenario_import_validate.py).
# ---------------------------------------------------------------------------


def _mixture(components: list[dict[str, object]]) -> dict[str, object]:
    return {"distribution": "lognormal_mixture", "components": components}


def _good_component(
    mean: float = 10.0, sigma: float = 1.0, weight: float = 0.5
) -> dict[str, object]:
    return {"mean": mean, "sigma": sigma, "weight": weight}


def test_finite_mixture_two_component_accepted():
    _call(
        primary_loss=_mixture(
            [
                _good_component(mean=8.06, sigma=0.70, weight=0.5),
                _good_component(mean=15.77, sigma=1.19, weight=0.5),
            ]
        )
    )


def test_mixture_empty_components_rejected():
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=_mixture([]))


def test_mixture_component_count_over_cap_rejected():
    # Sec-N1: component count is coupled to Settings.max_smes_per_fieldset —
    # derive the cap from settings rather than hardcoding it.
    cap = get_settings().max_smes_per_fieldset
    n = cap + 1
    w = 1.0 / n
    components = [_good_component(mean=10.0, sigma=1.0, weight=w) for _ in range(n)]
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=_mixture(components))


def _three_components_with_last_bad(bad: dict[str, object]) -> list[dict[str, object]]:
    """3-component mixture with the malformed component at the LAST index —
    proves per-component iteration (not a components[0]-only check)."""
    return [
        _good_component(mean=8.0, sigma=0.7, weight=1 / 3),
        _good_component(mean=12.0, sigma=0.9, weight=1 / 3),
        bad,
    ]


@pytest.mark.parametrize(
    "bad",
    [
        # Non-finite mean.
        {"mean": float("inf"), "sigma": 1.0, "weight": 1 / 3},
        {"mean": float("nan"), "sigma": 1.0, "weight": 1 / 3},
        # Non-finite sigma — NaN specifically (Sec-B1 BLOCKER: NaN <= 0 and
        # NaN > 10 are both False, so a NaN sigma must be caught by the
        # finiteness check BEFORE any range comparison, or it silently passes).
        {"mean": 10.0, "sigma": float("inf"), "weight": 1 / 3},
        {"mean": 10.0, "sigma": float("nan"), "weight": 1 / 3},
        # sigma <= 0.
        {"mean": 10.0, "sigma": 0.0, "weight": 1 / 3},
        {"mean": 10.0, "sigma": -1.0, "weight": 1 / 3},
        # sigma > _SIGMA_MAX (10).
        {"mean": 10.0, "sigma": 10.0001, "weight": 1 / 3},
        {"mean": 10.0, "sigma": 50.0, "weight": 1 / 3},
        # weight <= 0.
        {"mean": 10.0, "sigma": 1.0, "weight": 0.0},
        {"mean": 10.0, "sigma": 1.0, "weight": -0.1},
        # weight non-finite.
        {"mean": 10.0, "sigma": 1.0, "weight": float("inf")},
        {"mean": 10.0, "sigma": 1.0, "weight": float("nan")},
    ],
)
def test_mixture_malformed_last_component_rejected(bad):
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=_mixture(_three_components_with_last_bad(bad)))


def test_mixture_bad_weight_sum_rejected():
    # Individually valid weights that don't sum to 1 (±1e-9).
    components = [
        _good_component(mean=8.0, sigma=0.7, weight=0.3),
        _good_component(mean=12.0, sigma=0.9, weight=0.3),
        _good_component(mean=16.0, sigma=1.1, weight=0.3),
    ]
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=_mixture(components))


def test_mixture_sigma_at_bound_accepted():
    _call(
        primary_loss=_mixture(
            [
                _good_component(mean=10.0, sigma=10.0, weight=0.5),
                _good_component(mean=12.0, sigma=1.0, weight=0.5),
            ]
        )
    )


# ---------------------------------------------------------------------------
# Task 3b — D19: `max > p95` floor at the validation chokepoint.
# Only the floor fires here; `require_loss_max` requiredness is Task 6.
# ---------------------------------------------------------------------------


def test_max_absent_is_noop():
    """No `max` key at all -> the floor never fires, even for a dist that
    would fail it if `max` were present. Existing behaviour must be
    unchanged until the producers (Tasks 4a/5) start minting `max`."""
    _call(primary_loss={"distribution": "lognormal", "mean": 10.0, "sigma": 1.0})


def test_max_none_is_noop():
    """An explicit `"max": None` (e.g. a round-tripped dict) is also a NO-OP,
    not a malformed-type error -- absence-of-cap, not presence-of-garbage."""
    _call(primary_loss={"distribution": "lognormal", "mean": 10.0, "sigma": 1.0, "max": None})


def test_max_present_on_non_lognormal_kind_is_noop():
    """D19: `max` is applied to lognormal / lognormal_mixture loss fields
    ONLY. A stray `max` key on a PERT dict is inert here -- the design
    doc is explicit that a PERT-only scenario is never compared against
    capacity at all."""
    _call(primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0, "max": 0.5})


def test_max_above_p95_lognormal_accepted():
    mean, sigma = 10.0, 1.0
    p95 = math.exp(mean + _Z95 * sigma)
    _call(
        primary_loss={"distribution": "lognormal", "mean": mean, "sigma": sigma, "max": p95 * 1.5}
    )


def test_max_at_p95_boundary_lognormal_rejected():
    """`max == p95` exactly is REJECTED ("at or below" blocks) -- the floor
    requires strictly exceeding the p95, not merely reaching it."""
    mean, sigma = 10.0, 1.0
    p95 = math.exp(mean + _Z95 * sigma)
    with pytest.raises(FAIRCAMValidationError) as exc_info:
        _call(primary_loss={"distribution": "lognormal", "mean": mean, "sigma": sigma, "max": p95})
    assert "p95" in str(exc_info.value).lower()


def test_max_below_p95_lognormal_rejected():
    mean, sigma = 10.0, 1.0
    p95 = math.exp(mean + _Z95 * sigma)
    with pytest.raises(FAIRCAMValidationError):
        _call(
            primary_loss={
                "distribution": "lognormal",
                "mean": mean,
                "sigma": sigma,
                "max": p95 * 0.5,
            }
        )


def test_secondary_loss_floor_also_enforced():
    """The floor fires on secondary_loss too, not just primary_loss."""
    mean, sigma = 10.0, 1.0
    p95 = math.exp(mean + _Z95 * sigma)
    with pytest.raises(FAIRCAMValidationError):
        _call(
            secondary_loss={"distribution": "lognormal", "mean": mean, "sigma": sigma, "max": p95}
        )


def test_max_huge_mean_blocks_without_overflow():
    """The load-bearing overflow-safety case: mean=1000 makes
    `exp(mean + z95*sigma)` OverflowError (`math.exp(1000)` is already
    far beyond a float64's ~1.8e308 ceiling). A correct log-space
    comparison rejects this cleanly via FAIRCAMValidationError; a buggy
    real-space comparison would raise OverflowError instead, which is NOT
    caught by FAIRCAMValidationError-oriented callers and would 500."""
    mean, sigma = 1000.0, 1.0
    max_value = 1e300  # finite float64; ln(max_value) ~= 690.78, well under
    # ln(p95) = mean + z95*sigma ~= 1001.6449 -> must BLOCK.
    assert math.log(max_value) < mean + _Z95 * sigma  # sanity-check the test's own premise
    with pytest.raises(FAIRCAMValidationError) as exc_info:
        _call(
            primary_loss={
                "distribution": "lognormal",
                "mean": mean,
                "sigma": sigma,
                "max": max_value,
            }
        )
    assert "p95" in str(exc_info.value).lower()


def test_max_nonnumeric_rejected():
    with pytest.raises(FAIRCAMValidationError):
        _call(
            primary_loss={
                "distribution": "lognormal",
                "mean": 10.0,
                "sigma": 1.0,
                "max": "a lot",
            }
        )


@pytest.mark.parametrize("bad_max", [0.0, -1.0, float("inf"), float("nan")])
def test_max_non_positive_or_non_finite_rejected(bad_max):
    with pytest.raises(FAIRCAMValidationError):
        _call(
            primary_loss={
                "distribution": "lognormal",
                "mean": 10.0,
                "sigma": 1.0,
                "max": bad_max,
            }
        )


def test_max_present_but_mean_missing_rejected_not_500():
    """Malformed input (missing `mean` alongside a present `max`) must
    surface as FAIRCAMValidationError, never let a TypeError/AttributeError
    escape as a 500."""
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss={"distribution": "lognormal", "sigma": 1.0, "max": 100.0})


def test_max_present_but_sigma_missing_rejected_not_500():
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss={"distribution": "lognormal", "mean": 10.0, "max": 100.0})


# ---- mixture floor: every-component p95, NOT largest-meanlog -------------


_MIXTURE_MEANS_SIGMAS: list[tuple[float, float]] = [(8.0, 0.7), (10.0, 0.9)]


def _mixture_components(means_sigmas: list[tuple[float, float]]) -> list[dict[str, object]]:
    n = len(means_sigmas)
    return [_good_component(mean=mean, sigma=sigma, weight=1.0 / n) for mean, sigma in means_sigmas]


def _worst_p95(means_sigmas: list[tuple[float, float]]) -> float:
    return max(math.exp(mean + _Z95 * sigma) for mean, sigma in means_sigmas)


def test_mixture_floor_accepts_when_max_exceeds_every_component_p95():
    components = _mixture_components(_MIXTURE_MEANS_SIGMAS)
    worst_p95 = _worst_p95(_MIXTURE_MEANS_SIGMAS)
    _call(primary_loss=_mixture(components) | {"max": worst_p95 * 2.0})


def test_mixture_floor_rejects_when_max_at_or_below_any_component_p95():
    components = _mixture_components(_MIXTURE_MEANS_SIGMAS)
    worst_p95 = _worst_p95(_MIXTURE_MEANS_SIGMAS)
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=_mixture(components) | {"max": worst_p95 * 0.9})


def test_mixture_floor_uses_every_component_not_largest_meanlog():
    """THE discriminating case: component A has the LARGEST meanlog but a
    tiny sigma (small p95); component B has a smaller meanlog but a much
    larger sigma, giving it the LARGEST p95. `argmax_i mu_i` (A) !=
    `argmax_i p95_i` (B) here by construction.

    `max` is set strictly between the two p95s. The correct
    every-component reading must REJECT (max <= B's p95). A largest-
    meanlog implementation would incorrectly compare only against A's
    (smaller) p95, see max > p95_A, and ACCEPT -- so this test FAILS
    (no exception raised) under that wrong reading, which is exactly the
    conflation Task 3's methodology review flagged must not recur here.
    """
    mean_a, sigma_a = 10.0, 0.1  # largest meanlog, small p95
    mean_b, sigma_b = 8.0, 3.0  # smaller meanlog, LARGEST p95
    ln_p95_a = mean_a + _Z95 * sigma_a
    ln_p95_b = mean_b + _Z95 * sigma_b
    assert mean_a > mean_b  # A is argmax_i mu_i ...
    assert ln_p95_b > ln_p95_a  # ... but B is argmax_i p95_i. This is the conflation.

    ln_max = (ln_p95_a + ln_p95_b) / 2.0  # strictly between the two thresholds
    assert ln_p95_a < ln_max < ln_p95_b
    max_value = math.exp(ln_max)

    components = _mixture_components([(mean_a, sigma_a), (mean_b, sigma_b)])
    with pytest.raises(FAIRCAMValidationError) as exc_info:
        _call(primary_loss=_mixture(components) | {"max": max_value})
    assert "p95" in str(exc_info.value).lower()


def test_mixture_components_non_list_with_max_present_rejected_not_500():
    with pytest.raises(FAIRCAMValidationError):
        _call(
            primary_loss={
                "distribution": "lognormal_mixture",
                "components": "not-a-list",
                "max": 100.0,
            }
        )


def test_mixture_components_empty_with_max_present_rejected():
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=_mixture([]) | {"max": 100.0})


def test_mixture_component_non_dict_with_max_present_rejected_not_500():
    with pytest.raises(FAIRCAMValidationError):
        _call(
            primary_loss=_mixture([_good_component(), "not-a-dict"]) | {"max": 100.0}  # type: ignore[list-item]
        )


def test_mixture_component_missing_mean_with_max_present_rejected_not_500():
    bad_component: dict[str, object] = {"sigma": 1.0, "weight": 0.5}
    with pytest.raises(FAIRCAMValidationError):
        _call(primary_loss=_mixture([_good_component(weight=0.5), bad_component]) | {"max": 100.0})
