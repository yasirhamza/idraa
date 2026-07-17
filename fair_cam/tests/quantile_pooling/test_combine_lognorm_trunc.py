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
def test_combine_matches_r_oracle(fid: str) -> None:
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
    expected = pool["pooled"]
    actual = combine_lognorm_trunc(components, weights)
    assert actual.meanlog == pytest.approx(expected["meanlog"], abs=1e-4, rel=1e-4)
    assert actual.sdlog == pytest.approx(expected["sdlog"], abs=1e-4, rel=1e-4)
    assert actual.min_support == pytest.approx(expected["min_support"], abs=1e-4, rel=1e-4)
    # max_support may be infinite -> skip approx for that case
    if math.isfinite(expected["max_support"]) and math.isfinite(actual.max_support):
        assert actual.max_support == pytest.approx(expected["max_support"], abs=1e-4, rel=1e-4)
    else:
        assert math.isinf(actual.max_support) and math.isinf(expected["max_support"])


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
    """Per MD-1: weighted arithmetic mean of params (NOT mixture)."""
    fits = [
        LogNormalTruncFit(meanlog=2.0, sdlog=0.5, min_support=0.0, max_support=100.0),
        LogNormalTruncFit(meanlog=4.0, sdlog=1.5, min_support=10.0, max_support=200.0),
    ]
    pooled = combine_lognorm_trunc(fits)
    assert pooled.meanlog == pytest.approx(3.0)
    assert pooled.sdlog == pytest.approx(1.0)
    assert pooled.min_support == pytest.approx(5.0)
    assert pooled.max_support == pytest.approx(150.0)


def test_nonuniform_weights() -> None:
    fits = [
        LogNormalTruncFit(meanlog=2.0, sdlog=0.5, min_support=0.0, max_support=100.0),
        LogNormalTruncFit(meanlog=4.0, sdlog=1.5, min_support=10.0, max_support=200.0),
    ]
    pooled = combine_lognorm_trunc(fits, weights=[3.0, 1.0])
    # (3*2 + 1*4) / 4 = 2.5
    assert pooled.meanlog == pytest.approx(2.5)
    # (3*0.5 + 1*1.5) / 4 = 0.75
    assert pooled.sdlog == pytest.approx(0.75)


def test_default_weights_are_equal() -> None:
    fits = [
        LogNormalTruncFit(meanlog=1.0, sdlog=0.5, min_support=0.0, max_support=math.inf),
        LogNormalTruncFit(meanlog=2.0, sdlog=0.7, min_support=0.0, max_support=math.inf),
        LogNormalTruncFit(meanlog=3.0, sdlog=0.9, min_support=0.0, max_support=math.inf),
    ]
    a = combine_lognorm_trunc(fits)
    b = combine_lognorm_trunc(fits, weights=[1.0, 1.0, 1.0])
    assert a == b
