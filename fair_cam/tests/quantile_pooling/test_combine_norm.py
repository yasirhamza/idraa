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
def test_combine_matches_r_oracle(fid: str) -> None:
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
    expected = pool["pooled"]
    actual = combine_norm(components, weights)
    assert actual.mean == pytest.approx(expected["mean"], abs=1e-4, rel=1e-4)
    assert actual.sd == pytest.approx(expected["sd"], abs=1e-4, rel=1e-4)
    assert actual.min_support == pytest.approx(expected["min_support"], abs=1e-4, rel=1e-4)
    assert actual.max_support == pytest.approx(expected["max_support"], abs=1e-4, rel=1e-4)


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
    fits = [
        NormalTruncFit(mean=0.2, sd=0.1, min_support=0.0, max_support=1.0),
        NormalTruncFit(mean=0.6, sd=0.2, min_support=0.0, max_support=1.0),
    ]
    pooled = combine_norm(fits)
    assert pooled.mean == pytest.approx(0.4)
    assert pooled.sd == pytest.approx(0.15)
    assert pooled.min_support == pytest.approx(0.0)
    assert pooled.max_support == pytest.approx(1.0)


def test_nonuniform_weights() -> None:
    fits = [
        NormalTruncFit(mean=0.2, sd=0.1, min_support=0.0, max_support=1.0),
        NormalTruncFit(mean=0.6, sd=0.2, min_support=0.0, max_support=1.0),
    ]
    pooled = combine_norm(fits, weights=[3.0, 1.0])
    # (3*0.2 + 1*0.6) / 4 = 0.3
    assert pooled.mean == pytest.approx(0.3)
    # (3*0.1 + 1*0.2) / 4 = 0.125
    assert pooled.sd == pytest.approx(0.125)


def test_pooled_mean_outside_support_acceptable() -> None:
    """MD-4a: pooled mean MAY land outside [min_support, max_support];
    normal_to_pert_approx is responsible for the clamp."""
    fits = [
        NormalTruncFit(mean=-0.05, sd=0.1, min_support=0.0, max_support=1.0),
        NormalTruncFit(mean=-0.03, sd=0.1, min_support=0.0, max_support=1.0),
    ]
    pooled = combine_norm(fits)
    # combine_norm does NOT clamp; the negative pooled mean is allowed.
    assert pooled.mean < 0.0
