"""PR2 Task 8b: truncated web display rows for a capped lognormal /
lognormal_mixture loss field.

``idraa.app.lognormal_display_rows`` / ``lognormal_mixture_display_rows``
render p5/median/mean/p95 for the scenario/library-entry detail pages. When
the stored dict carries a "max" (capacity cap), every rendered quantile AND
the mean must come from the TRUNCATED distribution the engine actually
samples -- not the untruncated one -- and a visible "max" row must appear so
an analyst who authored a D17 cap can see it after saving. Without a "max"
key the functions must be byte-unchanged from pre-PR2 behaviour.

SYNTHETIC values only in this file (mu=ln(1e6), sigma=1.7-style fixtures) --
never derived from annual_revenue (CLAUDE.md rule (d); these fixtures are
tracked source, not deployment data).
"""

from __future__ import annotations

import math

import pytest
from fair_cam.quantile_pooling import (
    lognormal_mean,
    truncated_lognormal_mean,
    truncated_lognormal_mixture_mean,
)

from idraa.app import (  # type: ignore[attr-defined]
    lognormal_display_rows,
    lognormal_mixture_display_rows,
)

_MU = math.log(1_000_000.0)
_SIGMA = 1.7


# ---------------------------------------------------------------------------
# lognormal_display_rows (single lognormal)
# ---------------------------------------------------------------------------


def test_lognormal_display_rows_byte_unchanged_when_max_absent() -> None:
    dist = {"distribution": "lognormal", "mean": _MU, "sigma": _SIGMA}
    rows = lognormal_display_rows(dist)
    assert rows is not None
    assert rows["max"] is None
    # Same values as the pre-PR2 untruncated computation.
    from fair_cam.quantile_pooling import lognormal_quantiles

    p5, p50, p95 = lognormal_quantiles(_MU, _SIGMA, (0.05, 0.5, 0.95))
    assert rows["p5"] == pytest.approx(p5)
    assert rows["median"] == pytest.approx(p50)
    assert rows["p95"] == pytest.approx(p95)
    assert rows["mean"] == pytest.approx(lognormal_mean(_MU, _SIGMA))


def test_lognormal_display_rows_binding_cap_truncates_all_rows() -> None:
    """BINDING fixture max (at the field's parent median, NOT an aggressive
    CAPACITY_K -- lognormal_display_rows never reads Settings.capacity_k)."""
    cap = 500_000.0  # half of exp(_MU) == 1,000,000 -- binding
    untruncated = lognormal_display_rows(
        {"distribution": "lognormal", "mean": _MU, "sigma": _SIGMA}
    )
    truncated = lognormal_display_rows(
        {"distribution": "lognormal", "mean": _MU, "sigma": _SIGMA, "max": cap}
    )
    assert untruncated is not None and truncated is not None
    assert truncated["max"] == pytest.approx(cap)
    for key in ("p5", "median", "mean", "p95"):
        print(f"{key}: untruncated={untruncated[key]!r} truncated={truncated[key]!r}")
        assert truncated[key] != pytest.approx(untruncated[key]), (
            f"{key} did not change under a BINDING cap"
        )
    # Every truncated quantile/mean must lie strictly below the cap.
    for key in ("p5", "median", "mean", "p95"):
        assert truncated[key] < cap


def test_lognormal_display_rows_mean_matches_fair_cam_kernel() -> None:
    cap = 500_000.0
    truncated = lognormal_display_rows(
        {"distribution": "lognormal", "mean": _MU, "sigma": _SIGMA, "max": cap}
    )
    assert truncated is not None
    assert truncated["mean"] == pytest.approx(truncated_lognormal_mean(_MU, _SIGMA, cap), rel=1e-9)


def test_lognormal_display_rows_hand_math_quantile() -> None:
    """Hand-derived truncated median: exp(mu + sigma*Phi^-1(0.5*Phi(b))),
    b=(ln(cap)-mu)/sigma."""
    from scipy.special import ndtr, ndtri

    cap = 500_000.0
    b = (math.log(cap) - _MU) / _SIGMA
    expected_median = math.exp(_MU + _SIGMA * float(ndtri(0.5 * float(ndtr(b)))))
    truncated = lognormal_display_rows(
        {"distribution": "lognormal", "mean": _MU, "sigma": _SIGMA, "max": cap}
    )
    assert truncated is not None
    print(f"median: expected(hand-math)={expected_median!r} actual={truncated['median']!r}")
    assert truncated["median"] == pytest.approx(expected_median, rel=1e-9)


def test_lognormal_display_rows_non_lognormal_returns_none() -> None:
    assert lognormal_display_rows({"distribution": "PERT", "low": 1, "mode": 2, "high": 3}) is None
    assert lognormal_display_rows(None) is None
    assert lognormal_display_rows({}) is None


# ---------------------------------------------------------------------------
# lognormal_mixture_display_rows
# ---------------------------------------------------------------------------

_MIXTURE_COMPONENTS = [
    {"mean": math.log(1_000.0), "sigma": 0.5, "weight": 0.4},
    {"mean": math.log(1_000_000_000.0), "sigma": 0.8, "weight": 0.6},
]


def test_lognormal_mixture_display_rows_byte_unchanged_when_max_absent() -> None:
    dist = {"distribution": "lognormal_mixture", "components": _MIXTURE_COMPONENTS}
    rows = lognormal_mixture_display_rows(dist)
    assert rows is not None
    assert rows["max"] is None
    expected_mean = sum(
        c["weight"] * math.exp(c["mean"] + c["sigma"] ** 2 / 2.0) for c in _MIXTURE_COMPONENTS
    )
    assert rows["mean"] == pytest.approx(expected_mean, rel=1e-9)


def test_lognormal_mixture_display_rows_binding_cap_truncates_all_rows() -> None:
    """BINDING fixture max, below the pooled mixture's own median."""
    dist_untrunc = {"distribution": "lognormal_mixture", "components": _MIXTURE_COMPONENTS}
    cap = 1_500.0  # binding on BOTH components (near/above component 1's own
    # median of 1,000, and orders of magnitude below component 2's 1e9)
    dist_trunc = {
        "distribution": "lognormal_mixture",
        "components": _MIXTURE_COMPONENTS,
        "max": cap,
    }
    untruncated = lognormal_mixture_display_rows(dist_untrunc)
    truncated = lognormal_mixture_display_rows(dist_trunc)
    assert untruncated is not None and truncated is not None
    assert truncated["max"] == pytest.approx(cap)
    for key in ("p5", "median", "mean", "p95"):
        print(f"{key}: untruncated={untruncated[key]!r} truncated={truncated[key]!r}")
        assert truncated[key] != pytest.approx(untruncated[key]), (
            f"{key} did not change under a BINDING cap"
        )
        assert truncated[key] < cap


def test_lognormal_mixture_display_rows_mean_matches_fair_cam_kernel() -> None:
    cap = 5_000_000.0
    dist_trunc = {
        "distribution": "lognormal_mixture",
        "components": _MIXTURE_COMPONENTS,
        "max": cap,
    }
    truncated = lognormal_mixture_display_rows(dist_trunc)
    assert truncated is not None
    expected = truncated_lognormal_mixture_mean(
        [(c["weight"], c["mean"], c["sigma"]) for c in _MIXTURE_COMPONENTS], cap
    )
    assert truncated["mean"] == pytest.approx(expected, rel=1e-9)


def test_lognormal_mixture_display_rows_non_mixture_returns_none() -> None:
    assert lognormal_mixture_display_rows({"distribution": "PERT"}) is None
    assert lognormal_mixture_display_rows(None) is None
