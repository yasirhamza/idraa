"""R-oracle pinning for combine_norm per MD-1 + MD-4a.

R-oracle-dependent parametrized tests skip when fixture JSON is absent
(R unavailable). Python-only sentinel tests always run.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest
from fair_cam.quantile_pooling import (
    NormalTruncFit,
    QuantilePoolingError,
    combine_norm,
)

_FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "r_oracle_outputs.json"
_POOLING_IDS = ["3", "4"]


def _load_normal_fixtures() -> dict:
    if not _FIXTURE_PATH.exists():
        return {}
    return json.loads(_FIXTURE_PATH.read_text()).get("normal", {})


_NORMAL = _load_normal_fixtures()


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
    """Issue #27: ``combine_norm`` intentionally DEPARTS from the
    evaluator/collector R oracle for multi-component pooling. The R port
    (MD-1, R/fit_distributions.R:124-128) that this fixture was generated
    against parameter-averaged divergent fits into one distribution
    covering neither expert's stated range. The linear opinion pool
    (Clemen & Winkler 1999, "Combining Probability Distributions From
    Experts in Risk Analysis", Risk Analysis 19(2), pp. 187-203) instead
    keeps every fit as its own explicit mixture component -- the pool no
    longer collapses to the R oracle's averaged (mean, sd) at all. See
    docs/superpowers/specs/2026-07-19-mixture-pooling-design.md
    "Decision record" (2026-07-19) for the full rationale. This test
    exists to pin the departure (using the R fixture as the
    counterexample it now diverges from), not to assert agreement.
    """
    f = _NORMAL[fid]
    if "pooling" not in f:
        pytest.skip(f"fixture {fid} is a single-SME fit")
    pool = f["pooling"]
    components = [
        NormalTruncFit(
            mean=c["mean"],
            sd=c["sd"],
            min_support=c["min_support"],
            max_support=_max_support(c["max_support"]),
        )
        for c in pool["components"]
    ]
    weights = [float(w) for w in pool["weights"]]
    actual = combine_norm(components, weights)
    # The pool no longer collapses -- every input fit survives as its own
    # component, verbatim (not averaged toward the R oracle's "pooled" key).
    assert actual.components == tuple(components)
    total_w = sum(weights)
    expected_weights = tuple(w / total_w for w in weights)
    assert actual.weights == pytest.approx(expected_weights)


# ----------------------------------------------------------------------------
# Sentinel tests (Python-only)
# ----------------------------------------------------------------------------


def test_rejects_empty_fits() -> None:
    with pytest.raises(QuantilePoolingError, match=">=1 fit"):
        combine_norm([])


def test_rejects_zero_total_weight() -> None:
    fits = [NormalTruncFit(0.5, 0.1, 0.0, 1.0)]
    with pytest.raises(QuantilePoolingError, match="sum\\(weights\\)"):
        combine_norm(fits, weights=[0.0])


def test_equal_weight_arithmetic_mean() -> None:
    """RETIRED semantics (issue #27): this test used to pin the MD-1
    parameter-averaging port -- ``combine_norm`` no longer averages
    (mean, sd, min_support, max_support) into a single fit. The linear
    opinion pool (Clemen & Winkler 1999) keeps each SME's fit as an
    explicit mixture component; the historical assertions (mean=~0.4,
    sd=~0.15 -- the arithmetic mean of the two inputs) are gone BY
    DESIGN, not by regression. Name kept for git-blame continuity with
    the #343/#27 history; see
    docs/superpowers/specs/2026-07-19-mixture-pooling-design.md
    "Decision record" for the rationale.
    """
    fits = [
        NormalTruncFit(mean=0.2, sd=0.1, min_support=0.0, max_support=1.0),
        NormalTruncFit(mean=0.6, sd=0.2, min_support=0.0, max_support=1.0),
    ]
    pooled = combine_norm(fits)
    assert pooled.components == tuple(fits)
    assert pooled.weights == pytest.approx((0.5, 0.5))


def test_nonuniform_weights() -> None:
    """RETIRED semantics (issue #27): explicit weights normalize the
    mixture; they no longer weight an arithmetic average. See
    ``test_equal_weight_arithmetic_mean`` for the full rationale."""
    fits = [
        NormalTruncFit(mean=0.2, sd=0.1, min_support=0.0, max_support=1.0),
        NormalTruncFit(mean=0.6, sd=0.2, min_support=0.0, max_support=1.0),
    ]
    pooled = combine_norm(fits, weights=[3.0, 1.0])
    assert pooled.components == tuple(fits)
    # 3/(3+1)=0.75, 1/(3+1)=0.25 -- normalized, not param-weighted-averaged.
    assert pooled.weights == pytest.approx((0.75, 0.25))


def test_pooled_mean_outside_support_acceptable() -> None:
    """RETIRED semantics (issue #27): the OLD arithmetic-mean pool could
    itself land outside [min_support, max_support]; MD-4a accepted that
    because ``normal_to_pert_approx`` clamps the mode downstream. Under
    the mixture there is no aggregate "pooled mean" to land out-of-bounds
    -- each component's own (possibly out-of-support) mean is preserved
    verbatim as elicited; the SAME clamp responsibility now lives in
    ``normal_mixture_to_pert_approx`` (Task 2 of the mixture-pooling
    plan). This test pins that ``combine_norm`` does not reject or alter
    an out-of-support component mean at construction time.
    """
    fits = [
        NormalTruncFit(mean=-0.05, sd=0.1, min_support=0.0, max_support=1.0),
        NormalTruncFit(mean=-0.03, sd=0.1, min_support=0.0, max_support=1.0),
    ]
    pooled = combine_norm(fits)
    # combine_norm does NOT clamp or reject; both out-of-support means survive.
    assert pooled.components == tuple(fits)
    assert all(c.mean < 0.0 for c in pooled.components)
