"""R-oracle pinning for fit_norm_trunc per MD-4a + MD-3a.

R-oracle-dependent parametrized tests skip when fixture JSON is absent
(R unavailable). Python-only sentinel tests always run.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest
from fair_cam.quantile_pooling import QuantilePoolingError, fit_norm_trunc

_FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "r_oracle_outputs.json"
_SINGLE_SME_IDS = ["1", "2"]


def _load_normal_fixtures() -> dict:
    if not _FIXTURE_PATH.exists():
        return {}
    return json.loads(_FIXTURE_PATH.read_text()).get("normal", {})


_NORMAL = _load_normal_fixtures()


@pytest.mark.skipif(
    not _FIXTURE_PATH.exists(),
    reason=(
        "R-oracle fixture not generated (R unavailable). "
        "Run `Rscript scripts/regen_r_oracle.R` to enable."
    ),
)
@pytest.mark.parametrize("fid", _SINGLE_SME_IDS)
def test_fit_matches_r_oracle(fid: str) -> None:
    f = _NORMAL[fid]
    if "inputs" not in f:
        pytest.skip(f"fixture {fid} is a pooling fixture")
    inp = f["inputs"]
    expected = f["fit"]
    max_sup = inp["max_support"] if math.isfinite(inp["max_support"]) else math.inf
    actual = fit_norm_trunc(
        low=inp["low"],
        high=inp["high"],
        min_support=inp["min_support"],
        max_support=max_sup,
    )
    assert actual.mean == pytest.approx(expected["mean"], abs=1e-4, rel=1e-4)
    assert actual.sd == pytest.approx(expected["sd"], abs=1e-4, rel=1e-4)


# ----------------------------------------------------------------------------
# Sentinel tests (Python-only)
# ----------------------------------------------------------------------------


def test_rejects_high_less_than_low() -> None:
    with pytest.raises(QuantilePoolingError, match=">= low"):
        fit_norm_trunc(low=0.9, high=0.1)


def test_optimizer_method_pinned_to_nelder_mead(monkeypatch) -> None:
    """Meth-3 R2 regression: must use Nelder-Mead with R-matching defaults."""
    captured: dict = {}
    from scipy import optimize

    real_minimize = optimize.minimize

    def spy(*args, **kwargs):
        captured["method"] = kwargs.get("method")
        captured["x0"] = list(args[1]) if len(args) > 1 else list(kwargs.get("x0", []))
        captured["options"] = kwargs.get("options", {})
        return real_minimize(*args, **kwargs)

    monkeypatch.setattr("fair_cam.quantile_pooling._normal.minimize", spy)
    fit_norm_trunc(low=0.1, high=0.5)
    assert captured["method"] == "Nelder-Mead"
    assert captured["x0"] == [0.01, 1.0]
    assert captured["options"]["xatol"] == 1e-6
    assert captured["options"]["fatol"] == 1e-6


def test_deadline_callback_raises_on_timeout() -> None:
    with pytest.raises(QuantilePoolingError, match="wall_clock_ms"):
        fit_norm_trunc(low=0.1, high=0.5, wall_clock_ms=1, maxiter=100_000)


def test_fit_recovers_quantiles_within_tolerance() -> None:
    """Internal self-consistency: fitted dist should reproduce input
    quantiles within ~1e-3 (no R-oracle dependency)."""
    from fair_cam.quantile_pooling._normal import _qnormtrunc

    fit = fit_norm_trunc(low=0.2, high=0.8, min_support=0.0, max_support=1.0)
    q_low = _qnormtrunc(0.05, fit.mean, fit.sd, fit.min_support, fit.max_support)
    q_high = _qnormtrunc(0.95, fit.mean, fit.sd, fit.min_support, fit.max_support)
    assert q_low == pytest.approx(0.2, abs=1e-2)
    assert q_high == pytest.approx(0.8, abs=1e-2)
