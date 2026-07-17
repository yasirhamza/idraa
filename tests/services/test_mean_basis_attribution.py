"""Hand-math anchors for the MEAN-basis attribution chain (side-by-side, 2026-07-04).

The mean chain exists so per-control figures are scale-coherent with the MC
mean headline: under the engine's independence assumption expectation factors,
E[TEF]*E[Vuln]*E[Loss] = mean ALE, so mean-basis v(S) is the mean-ALE reduction
(exact when the currency-subtractor clip is inactive — Jensen caveat on
``representative_mean``).

Every case carries its full hand derivation (the #131 discipline).
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest
from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.control_attribution import (
    representative_mean,
    representative_value,
    scenario_base_ale,
)
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)
from fair_cam.tests.risk_engine._helpers import make_fair_parameters

from idraa.services.run_executor import (
    _compute_loo_by_scenario,
    _compute_shapley_by_scenario,
    _inject_loo,
    _inject_shapley,
)

# --------------------------------------------------------------------------- #
# representative_mean — one closed form per distribution type, hand-derived.
# --------------------------------------------------------------------------- #


def test_representative_mean_closed_forms() -> None:
    """PERT (Vose gamma=4): (l+4m+h)/6 -> (1+8+9)/6 = 3.0 (mode is 2 — mean
    pulls right on the skewed triple, unlike representative_value's mode).
    TRIANGULAR: (1+2+9)/3 = 4.0.  UNIFORM: (2+4)/2 = 3.0.  NORMAL: mean.
    LOGNORMAL(mu=ln 10^6, sigma=1): exp(mu + 1/2) = 10^6 * e^0.5.
    BETA(2,6): 2/8 = 0.25 (mode would be (2-1)/(2+6-2) = 0.166...).
    """
    pert = FAIRDistribution(DistributionType.PERT, {"low": 1.0, "mode": 2.0, "high": 9.0})
    assert representative_mean(pert) == pytest.approx(3.0, rel=1e-12)
    assert representative_value(pert) == 2.0  # mode — the divergence being displayed

    tri = FAIRDistribution(DistributionType.TRIANGULAR, {"low": 1.0, "mode": 2.0, "high": 9.0})
    assert representative_mean(tri) == pytest.approx(4.0, rel=1e-12)

    uni = FAIRDistribution(DistributionType.UNIFORM, {"low": 2.0, "high": 4.0})
    assert representative_mean(uni) == pytest.approx(3.0, rel=1e-12)

    norm = FAIRDistribution(DistributionType.NORMAL, {"mean": 7.5, "std": 2.0})
    assert representative_mean(norm) == pytest.approx(7.5, rel=1e-12)

    logn = FAIRDistribution(
        DistributionType.LOGNORMAL, {"mean": math.log(1_000_000.0), "sigma": 1.0}
    )
    assert representative_mean(logn) == pytest.approx(1_000_000.0 * math.exp(0.5), rel=1e-12)
    assert representative_value(logn) == pytest.approx(1_000_000.0, rel=1e-12)  # median

    beta = FAIRDistribution(DistributionType.BETA, {"alpha": 2.0, "beta": 6.0})
    assert representative_mean(beta) == pytest.approx(0.25, rel=1e-12)


def test_scenario_base_ale_statistic_validation() -> None:
    rp = make_fair_parameters(tef=1.0, vuln=0.5, primary=100.0, secondary=50.0)
    with pytest.raises(ValueError, match="unknown attribution statistic"):
        scenario_base_ale(rp, "median")


def test_lognormal_mean_matches_mc_sampling() -> None:
    """The independence-factoring claim, checked against the engine's own
    sampler: MC mean of a lognormal ~= exp(mu + sigma^2/2) (rel 1%, n=200k,
    fixed seed). This is the closed form the mean chain relies on."""
    logn = FAIRDistribution(
        DistributionType.LOGNORMAL, {"mean": math.log(1_000_000.0), "sigma": 1.0}
    )
    rng = np.random.default_rng(42)
    samples = logn.sample(200_000, rng)
    assert float(np.mean(samples)) == pytest.approx(representative_mean(logn), rel=0.01)


# --------------------------------------------------------------------------- #
# End-to-end chains through the executor passes.
# --------------------------------------------------------------------------- #


def _ctrl(cid: str, assigns: list[tuple[str, float | None, float, float]]) -> Control:
    return Control(
        control_id=cid,
        name=cid,
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.PREVENTIVE,
        assignments=[
            FairCamControlFunctionAssignment(
                sub_function=FairCamSubFunction(sf),
                capability_value=cap,
                coverage=cov,
                reliability=rel,
            )
            for sf, cap, cov, rel in assigns
        ],
    )


def _calculator(controls: dict[str, Control]) -> SimpleNamespace:
    return SimpleNamespace(control_registry=SimpleNamespace(get_control=controls.get))


def _lognormal_rp() -> FAIRParameters:
    """Divergent-basis scenario. Hand math:
    TEF  PERT(1,2,3): typical (mode) = 2 ; mean (1+8+3)/6 = 2  [equal]
    Vuln UNIFORM{0.5}: 0.5 both.
    PL   LOGNORMAL(mu=ln 10^6, sigma=1):   typical 10^6 ; mean 10^6*e^0.5
    SL   LOGNORMAL(mu=ln 5*10^5, sigma=.5): typical 5*10^5 ; mean 5*10^5*e^0.125
    A_typical = 2*0.5*(10^6 + 5*10^5)            = 1,500,000
    A_mean    = 2*0.5*(10^6 e^0.5 + 5*10^5 e^0.125) = 2,215,295.4977...
    """
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 1.0, "mode": 2.0, "high": 3.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.5, "high": 0.5}),
        primary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL, {"mean": math.log(1_000_000.0), "sigma": 1.0}
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.LOGNORMAL, {"mean": math.log(500_000.0), "sigma": 0.5}
        ),
    )


A_TYPICAL = 1_500_000.0
A_MEAN = 1_000_000.0 * math.exp(0.5) + 500_000.0 * math.exp(0.125)  # tef*vuln = 1


def test_prevention_control_mean_vs_typical_exact_ratio() -> None:
    """A prevention-only control multiplies TEF/Vuln nodes, so
    v = A_basis * (1 - mtef*mvuln): the basis enters ONLY through A. Therefore
    v_mean / v_typical == A_mean / A_typical EXACTLY (no weight knowledge
    needed — the multiplier algebra cancels), pinning the whole chain
    (representative_mean -> scenario_base_ale -> value_fn) end to end.

    Both LOO and Shapley must satisfy it; with one control they coincide
    (LOO == phi == v(N))."""
    controls = {"r1": _ctrl("r1", [("lec_prev_resistance", 1.0, 1.0, 1.0)])}
    rp = _lognormal_rp()
    inputs = [("s1", "scenario one", rp)]
    cache: dict = {}

    loo_typ, sk_t = _compute_loo_by_scenario(
        _calculator(controls), inputs, None, ["r1"], composition_cache=cache
    )
    loo_mean, sk_m = _compute_loo_by_scenario(
        _calculator(controls),
        inputs,
        None,
        ["r1"],
        composition_cache=cache,
        statistic="mean",
    )
    assert sk_t == [] and sk_m == []
    v_typ = loo_typ["s1"]["r1"]
    v_mean = loo_mean["s1"]["r1"]
    assert v_typ > 0.0
    assert v_mean / v_typ == pytest.approx(A_MEAN / A_TYPICAL, rel=1e-12)
    # A_mean hand value: 1,648,721.2707 + 566,574.2265 = 2,215,295.4973
    assert pytest.approx(2_215_295.4973, rel=1e-9) == A_MEAN

    sh_typ, _ = _compute_shapley_by_scenario(
        _calculator(controls), inputs, None, ["r1"], composition_cache=cache
    )
    sh_mean, _ = _compute_shapley_by_scenario(
        _calculator(controls),
        inputs,
        None,
        ["r1"],
        composition_cache=cache,
        statistic="mean",
    )
    assert sh_typ["s1"]["r1"] == pytest.approx(v_typ, rel=1e-12)
    assert sh_mean["s1"]["r1"] == pytest.approx(v_mean, rel=1e-12)


def test_symmetric_inputs_mean_equals_typical() -> None:
    """The shared test helper builds symmetric PERT triples (mean == mode) and
    a point-mass vulnerability, so BOTH chains must agree exactly — pins that
    the statistic switch changes nothing except the representative scalars."""
    controls = {
        "r1": _ctrl("r1", [("lec_prev_resistance", 0.9, 1.0, 1.0)]),
        "r2": _ctrl("r2", [("lec_prev_avoidance", 0.7, 1.0, 1.0)]),
    }
    rp = make_fair_parameters(tef=2.0, vuln=0.5, primary=1_000_000.0, secondary=500_000.0)
    inputs = [("s1", "scenario one", rp)]
    cache: dict = {}
    for fn in (_compute_loo_by_scenario, _compute_shapley_by_scenario):
        typ, _ = fn(_calculator(controls), inputs, None, list(controls), composition_cache=cache)
        mean, _ = fn(
            _calculator(controls),
            inputs,
            None,
            list(controls),
            composition_cache=cache,
            statistic="mean",
        )
        for cid in controls:
            assert mean["s1"][cid] == pytest.approx(typ["s1"][cid], rel=1e-12)


def test_composition_cache_shared_across_statistics() -> None:
    """The composition cache is statistic-INVARIANT (base scalars enter only
    reduction_from_composition), so the mean pass after a typical pass must add
    ZERO new cache entries — the design's 'costs only arithmetic' claim."""
    controls = {
        "r1": _ctrl("r1", [("lec_prev_resistance", 0.9, 1.0, 1.0)]),
        "r2": _ctrl("r2", [("lec_prev_avoidance", 0.7, 1.0, 1.0)]),
    }
    rp = _lognormal_rp()
    inputs = [("s1", "scenario one", rp)]
    cache: dict = {}
    _compute_shapley_by_scenario(
        _calculator(controls), inputs, None, list(controls), composition_cache=cache
    )
    n_after_typical = len(cache)
    assert n_after_typical > 0
    _compute_shapley_by_scenario(
        _calculator(controls),
        inputs,
        None,
        list(controls),
        composition_cache=cache,
        statistic="mean",
    )
    assert len(cache) == n_after_typical


def test_injectors_write_mean_twin_keys() -> None:
    """key= param: the mean maps land in *_mean without touching the typical
    keys, absent-scenario convention preserved for both."""
    payload = [
        {"scenario_id": "s1", "control_adjustments": [{"control_id": "c1"}]},
        {"scenario_id": "s2", "control_adjustments": [{"control_id": "c1"}]},
    ]
    _inject_shapley(payload, {"s1": {"c1": 10.0}})
    _inject_shapley(payload, {"s1": {"c1": 25.0}}, key="shapley_value_mean")
    _inject_loo(payload, {"s1": {"c1": 5.0}})
    _inject_loo(payload, {"s1": {"c1": 12.5}}, key="if_removed_value_mean")
    adj1 = payload[0]["control_adjustments"][0]
    assert adj1["shapley_value"] == 10.0
    assert adj1["shapley_value_mean"] == 25.0
    assert adj1["if_removed_value"] == 5.0
    assert adj1["if_removed_value_mean"] == 12.5
    adj2 = payload[1]["control_adjustments"][0]
    for k in (
        "shapley_value",
        "shapley_value_mean",
        "if_removed_value",
        "if_removed_value_mean",
    ):
        assert k not in adj2  # absent != 0.0, both bases
