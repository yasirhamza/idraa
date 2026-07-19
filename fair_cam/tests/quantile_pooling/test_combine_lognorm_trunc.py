"""R-oracle pinning for combine_lognorm_trunc per MD-1 + MD-3a.

R-oracle-dependent parametrized tests skip when the fixture JSON is absent
(R unavailable in dev env). Python-only sentinel tests always run.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest
from fair_cam.quantile_pooling import (
    LogNormalTruncFit,
    QuantilePoolingError,
    combine_lognorm_trunc,
)

_FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "r_oracle_outputs.json"
_POOLING_IDS = ["4", "5", "6", "7"]


def _load_lognormal_fixtures() -> dict:
    if not _FIXTURE_PATH.exists():
        return {}
    return json.loads(_FIXTURE_PATH.read_text()).get("lognormal", {})


_LOGNORMAL = _load_lognormal_fixtures()


def _max_support(v: float) -> float:
    return v if math.isfinite(v) else math.inf


@pytest.mark.skipif(
    not _FIXTURE_PATH.exists(),
    reason=(
        "R-oracle fixture not generated (R unavailable). "
        "Run `Rscript scripts/regen_r_oracle.R` to enable."
    ),
)
@pytest.mark.parametrize("fid", _POOLING_IDS)
def test_combine_departs_from_r_oracle_documented(fid: str) -> None:
    """Issue #27: ``combine_lognorm_trunc`` intentionally DEPARTS from the
    evaluator/collector R oracle for multi-component pooling. The R port
    (MD-1, R/fit_distributions.R:67-79) that this fixture was generated
    against parameter-averaged divergent fits into one distribution
    covering neither expert's stated range. The linear opinion pool
    (Clemen & Winkler 1999, "Combining Probability Distributions From
    Experts in Risk Analysis", Risk Analysis 19(2), pp. 187-203) instead
    keeps every fit as its own explicit mixture component -- the pool no
    longer collapses to the R oracle's averaged (meanlog, sdlog) at all.
    See docs/superpowers/specs/2026-07-19-mixture-pooling-design.md
    "Decision record" (2026-07-19) for the full rationale. This test
    exists to pin the departure (using the R fixture as the
    counterexample it now diverges from), not to assert agreement.
    """
    f = _LOGNORMAL[fid]
    if "pooling" not in f:
        pytest.skip(f"fixture {fid} is a single-SME fit, not pooling")
    pool = f["pooling"]
    components = [
        LogNormalTruncFit(
            meanlog=c["meanlog"],
            sdlog=c["sdlog"],
            min_support=c["min_support"],
            max_support=_max_support(c["max_support"]),
        )
        for c in pool["components"]
    ]
    weights = [float(w) for w in pool["weights"]]
    actual = combine_lognorm_trunc(components, weights)
    # The pool no longer collapses -- every input fit survives as its own
    # component, verbatim (not averaged toward the R oracle's "pooled" key).
    assert actual.components == tuple(components)
    total_w = sum(weights)
    expected_weights = tuple(w / total_w for w in weights)
    assert actual.weights == pytest.approx(expected_weights)


# ----------------------------------------------------------------------------
# Sentinel tests (Python-only, no R fixture dependency)
# ----------------------------------------------------------------------------


def test_rejects_empty_fits() -> None:
    with pytest.raises(QuantilePoolingError, match=">=1 fit"):
        combine_lognorm_trunc([])


def test_rejects_weights_length_mismatch() -> None:
    fits = [LogNormalTruncFit(1.0, 0.5, 0.0, math.inf), LogNormalTruncFit(2.0, 0.7, 0.0, math.inf)]
    with pytest.raises(QuantilePoolingError, match="len\\(weights\\)"):
        combine_lognorm_trunc(fits, weights=[1.0])


def test_rejects_zero_total_weight() -> None:
    fits = [LogNormalTruncFit(1.0, 0.5, 0.0, math.inf)]
    with pytest.raises(QuantilePoolingError, match="sum\\(weights\\)"):
        combine_lognorm_trunc(fits, weights=[0.0])


def test_equal_weight_arithmetic_mean() -> None:
    """RETIRED semantics (issue #27): this test used to pin the MD-1
    parameter-averaging port -- ``combine_lognorm_trunc`` no longer
    averages (meanlog, sdlog, min_support, max_support) into a single
    fit. The linear opinion pool (Clemen & Winkler 1999) keeps each SME's
    fit as an explicit mixture component; the historical assertions
    (meanlog=~3.0, sdlog=~1.0 -- the arithmetic mean of the two inputs)
    are gone BY DESIGN, not by regression. Name kept for git-blame
    continuity with the #343/#27 history; see
    docs/superpowers/specs/2026-07-19-mixture-pooling-design.md
    "Decision record" for the rationale.
    """
    fits = [
        LogNormalTruncFit(meanlog=2.0, sdlog=0.5, min_support=0.0, max_support=100.0),
        LogNormalTruncFit(meanlog=4.0, sdlog=1.5, min_support=10.0, max_support=200.0),
    ]
    pooled = combine_lognorm_trunc(fits)
    assert pooled.components == tuple(fits)
    assert pooled.weights == pytest.approx((0.5, 0.5))


def test_nonuniform_weights() -> None:
    """RETIRED semantics (issue #27): explicit weights normalize the
    mixture; they no longer weight an arithmetic average. See
    ``test_equal_weight_arithmetic_mean`` for the full rationale."""
    fits = [
        LogNormalTruncFit(meanlog=2.0, sdlog=0.5, min_support=0.0, max_support=100.0),
        LogNormalTruncFit(meanlog=4.0, sdlog=1.5, min_support=10.0, max_support=200.0),
    ]
    pooled = combine_lognorm_trunc(fits, weights=[3.0, 1.0])
    assert pooled.components == tuple(fits)
    # 3/(3+1)=0.75, 1/(3+1)=0.25 -- normalized, not param-weighted-averaged.
    assert pooled.weights == pytest.approx((0.75, 0.25))


def test_default_weights_are_equal() -> None:
    fits = [
        LogNormalTruncFit(meanlog=1.0, sdlog=0.5, min_support=0.0, max_support=math.inf),
        LogNormalTruncFit(meanlog=2.0, sdlog=0.7, min_support=0.0, max_support=math.inf),
        LogNormalTruncFit(meanlog=3.0, sdlog=0.9, min_support=0.0, max_support=math.inf),
    ]
    a = combine_lognorm_trunc(fits)
    b = combine_lognorm_trunc(fits, weights=[1.0, 1.0, 1.0])
    assert a == b
