# tests/integration/test_run_executor_max_adapter.py
"""PR2 capacity bound (Task 3): `max` reaching the read adapter.

`_dict_to_fair_distribution` (run_executor.py) is the ORM-dict -> fair_cam
FAIRDistribution read adapter. Before this task it rebuilt LOGNORMAL /
LOGNORMAL_MIXTURE param dicts from a hardcoded key list, so an optional
top-level `max` key on the stored distribution was silently dropped even
if present in the payload. This file pins:

- `max` reaches `parameters["max"]` for a plain lognormal.
- `max` reaches `parameters["max"]` for a mixture with N >= 3 components,
  with ALL N components preserved (adapter-iteration contract --
  feedback_data_contract_enforcement.md; guards against a future `[0]`/
  `[-1]` optimization silently dropping components).
- Absent `max` stays absent -- no key invented on either branch.
- The read-adapter guard is STRICTER than finite/positive: it rejects a
  cap at or below the distribution's median (`exp(meanlog)`; for a
  mixture, the LARGEST-meanlog component's median, since every component
  shares the one cap and medians are `exp(mu_i)`). This guard exists
  because a too-small-but-finite-and-positive cap drives
  `b = (ln(max) - meanlog) / sigma` deeply negative, and
  `scipy.special.ndtr(b)` underflows SILENTLY to exactly 0.0 once
  `b <~ -38` -- every draw becomes exactly $0 (finite, so the engine's
  finite-output guard never catches it). `services/fair_cam_validation.py`
  blocks a too-small `max` at store time, but a raw-SQL migration row
  bypasses that chokepoint -- this read adapter is the LAST chokepoint
  before the sampler, so it must fail loud rather than silently produce an
  all-$0 run.
"""

from __future__ import annotations

import math

import pytest

from idraa.services.run_executor import _dict_to_fair_distribution

_MEANLOG = math.log(1_000_000.0)  # median = 1,000,000
_SIGMA = 1.7
_SAFE_MAX = 1e9  # comfortably above the median

# N = 3 components (adapter-iteration contract minimum). Largest meanlog is
# 12.0 -> median = exp(12.0) ~= 162,754.79.
_MIX_COMPONENTS = [
    {"mean": 8.0, "sigma": 0.5, "weight": 0.3},
    {"mean": 10.0, "sigma": 0.8, "weight": 0.5},
    {"mean": 12.0, "sigma": 0.3, "weight": 0.2},
]
_MIX_LARGEST_MEANLOG = 12.0


# ---- max reaches parameters (present) --------------------------------------


def test_lognormal_max_reaches_parameters():
    d = _dict_to_fair_distribution(
        {"distribution": "lognormal", "mean": _MEANLOG, "sigma": _SIGMA, "max": _SAFE_MAX}
    )
    assert d.parameters["max"] == _SAFE_MAX


def test_mixture_max_reaches_parameters_and_preserves_all_n_components():
    payload = {
        "distribution": "lognormal_mixture",
        "components": [dict(c) for c in _MIX_COMPONENTS],
        "max": _SAFE_MAX,
    }
    d = _dict_to_fair_distribution(payload)

    assert d.parameters["max"] == _SAFE_MAX
    assert len(d.parameters["components"]) == len(_MIX_COMPONENTS) == 3
    for orig, mapped in zip(_MIX_COMPONENTS, d.parameters["components"], strict=True):
        assert mapped["mean"] == orig["mean"]
        assert mapped["sigma"] == orig["sigma"]
        assert mapped["weight"] == orig["weight"]


# ---- max absent stays absent ------------------------------------------------


def test_lognormal_without_max_key_invents_none():
    d = _dict_to_fair_distribution({"distribution": "lognormal", "mean": _MEANLOG, "sigma": _SIGMA})
    assert "max" not in d.parameters


def test_mixture_without_max_key_invents_none():
    payload = {
        "distribution": "lognormal_mixture",
        "components": [dict(c) for c in _MIX_COMPONENTS],
    }
    d = _dict_to_fair_distribution(payload)
    assert "max" not in d.parameters


# ---- median guard: stricter than finite/positive ---------------------------


def test_lognormal_max_at_median_rejected():
    """`max` exactly AT the median (b == 0) must be rejected -- the guard
    is "at or below", not "strictly below"."""
    median = math.exp(_MEANLOG)
    with pytest.raises(ValueError, match="median"):
        _dict_to_fair_distribution(
            {"distribution": "lognormal", "mean": _MEANLOG, "sigma": _SIGMA, "max": median}
        )


def test_lognormal_max_below_median_rejected():
    below_median = math.exp(_MEANLOG) * 0.5
    with pytest.raises(ValueError, match="median"):
        _dict_to_fair_distribution(
            {"distribution": "lognormal", "mean": _MEANLOG, "sigma": _SIGMA, "max": below_median}
        )


@pytest.mark.parametrize("bad_max", [float("inf"), float("nan"), 0.0, -5.0])
def test_lognormal_max_non_finite_or_non_positive_rejected(bad_max: float):
    with pytest.raises(ValueError):
        _dict_to_fair_distribution(
            {"distribution": "lognormal", "mean": _MEANLOG, "sigma": _SIGMA, "max": bad_max}
        )


def test_mixture_max_at_or_below_largest_meanlog_median_rejected():
    """Guard uses the LARGEST-meanlog component's median (12.0 -> ~162,754.79),
    not e.g. the smallest or an arbitrary index -- a cap comfortably above
    the SMALLEST component's median but at/below the LARGEST's must still
    be rejected."""
    largest_median = math.exp(_MIX_LARGEST_MEANLOG)
    payload = {
        "distribution": "lognormal_mixture",
        "components": [dict(c) for c in _MIX_COMPONENTS],
        "max": largest_median,  # at the largest-meanlog median exactly
    }
    with pytest.raises(ValueError, match="median"):
        _dict_to_fair_distribution(payload)


def test_mixture_max_above_largest_meanlog_median_accepted():
    largest_median = math.exp(_MIX_LARGEST_MEANLOG)
    payload = {
        "distribution": "lognormal_mixture",
        "components": [dict(c) for c in _MIX_COMPONENTS],
        "max": largest_median * 10,
    }
    d = _dict_to_fair_distribution(payload)
    assert d.parameters["max"] == largest_median * 10
