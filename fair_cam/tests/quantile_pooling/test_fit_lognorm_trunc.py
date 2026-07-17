"""R-oracle pinning per MD-3a. Tolerance 1e-4 abs+rel.

NOTE: R/collector is not installed in the local dev environment. When the
fixture JSON is absent, R-oracle-dependent tests skip with an informative
reason. The sentinel/regression tests (rejects_zero_low / rejects_high_less
/ optimizer_method_pinned / deadline_callback) DO NOT depend on the fixture
and always run.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest
from fair_cam.quantile_pooling import QuantilePoolingError, fit_lognorm_trunc

_FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "r_oracle_outputs.json"
_SINGLE_SME_IDS = ["1", "2", "3", "8"]


def _load_lognormal_fixtures() -> dict:
    if not _FIXTURE_PATH.exists():
        return {}
    return json.loads(_FIXTURE_PATH.read_text()).get("lognormal", {})


_LOGNORMAL = _load_lognormal_fixtures()


@pytest.mark.skipif(
    not _FIXTURE_PATH.exists(),
    reason=(
        "R-oracle fixture not generated (R unavailable). "
        "Run `Rscript scripts/regen_r_oracle.R` to enable."
    ),
)
@pytest.mark.parametrize("fid", _SINGLE_SME_IDS)
def test_fit_matches_r_oracle(fid: str) -> None:
    f = _LOGNORMAL[fid]
    if "inputs" not in f:
        pytest.skip(f"fixture {fid} is a pooling fixture")
    inp = f["inputs"]
    expected = f["fit"]
    max_sup = inp["max_support"] if math.isfinite(inp["max_support"]) else math.inf
    actual = fit_lognorm_trunc(
        low=inp["low"],
        high=inp["high"],
        min_support=inp["min_support"],
        max_support=max_sup,
    )
    assert actual.meanlog == pytest.approx(expected["meanlog"], abs=1e-4, rel=1e-4)
    assert actual.sdlog == pytest.approx(expected["sdlog"], abs=1e-4, rel=1e-4)


def test_rejects_zero_low() -> None:
    with pytest.raises(QuantilePoolingError, match="must be > 0"):
        fit_lognorm_trunc(low=0, high=100)


def test_rejects_high_less_than_low() -> None:
    with pytest.raises(QuantilePoolingError, match=">= low"):
        fit_lognorm_trunc(low=100, high=50)


def test_optimizer_method_pinned_to_nelder_mead(monkeypatch) -> None:
    """Meth-3 R2 regression: must use Nelder-Mead with R-matching x0/tolerances."""
    captured: dict = {}
    from scipy import optimize

    real_minimize = optimize.minimize

    def spy(*args, **kwargs):
        captured["method"] = kwargs.get("method")
        captured["x0"] = list(args[1]) if len(args) > 1 else list(kwargs.get("x0", []))
        captured["options"] = kwargs.get("options", {})
        return real_minimize(*args, **kwargs)

    monkeypatch.setattr("fair_cam.quantile_pooling._lognormal.minimize", spy)
    fit_lognorm_trunc(low=100, high=200)
    assert captured["method"] == "Nelder-Mead"
    assert captured["x0"] == [0.01, 1.0]
    assert captured["options"]["xatol"] == 1e-6
    assert captured["options"]["fatol"] == 1e-6


def test_deadline_callback_raises_on_timeout() -> None:
    with pytest.raises(QuantilePoolingError, match="wall_clock_ms"):
        fit_lognorm_trunc(low=100, high=200, wall_clock_ms=1, maxiter=100_000)
