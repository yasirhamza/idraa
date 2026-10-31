"""Ties in the weight-robustness ensemble are broken by the canonical order, not by id.

A control that contributes EXACTLY 0.0 (a partially authored one: a lone Detection
control whose AND-trio is incomplete, a Response control gated by the resulting zero
Detection-Response pair, a VMC monitoring control with no Correction partner) ties with
every other such control in every draw. The displayed ranking breaks that tie by display
name; the per-draw ranking used to break it by control id. Where the two orders disagree
(ids are UUIDs, names are words) every such pair flipped in 100% of draws and was reported
as "too close to call", with "unstable" badges on controls that are simply worth $0.

The per-draw ranking now sorts stably over the canonical order. These tests pin that
(synthetic and through the real attribution path) and that no dollar figure depends on it.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

import numpy as np
from fair_cam.models.composition_topology import KAPPA_META_RELIABILITY
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from fair_cam.tests.risk_engine._helpers import make_control

from idraa.services import run_executor as rex
from idraa.services.aggregate_run_view_model import displayed_control_order
from idraa.services.weight_robustness import FINALIZE_TO_COMPOSE_COST_RATIO, run_weight_ensemble

# display name -> control id. The ids sort in the REVERSE order of the names inside each
# group, so a tie broken by id and a tie broken by name disagree for every tied pair.
_POSITIVE = {
    "CrowdStrike Falcon EDR": "ffe1c2a4-0000-4000-8000-000000000001",
    "Palo Alto Firewall": "ffd0a1b3-0000-4000-8000-000000000002",
}
_INERT = {
    "Cohesity Recovery": "00f495a6-0000-4000-8000-000000000016",
    "Elastic SIEM": "00e5a4b5-0000-4000-8000-000000000015",
    "Rubrik Recovery": "00d6b3c4-0000-4000-8000-000000000014",
    "Splunk SIEM": "00c7c2d3-0000-4000-8000-000000000013",
    "Tenable Scanner": "00b8d1e2-0000-4000-8000-000000000012",
    "Veeam Backup": "00a9e0f1-0000-4000-8000-000000000011",
}


def _names() -> dict[str, str]:
    return {cid: name for name, cid in {**_POSITIVE, **_INERT}.items()}


def _flat(fn: Callable[[Any], dict[str, float]]) -> Callable[[list[Any]], list[dict[str, float]]]:
    return lambda draws: [fn(d) for d in draws]


def _canonical(totals: dict[str, float]) -> list[str]:
    return displayed_control_order(totals, _names())


def _ensemble(
    canon: list[str], value_fn: Callable[[Any], dict[str, float]], **kw: Any
) -> dict[str, Any]:
    return run_weight_ensemble(
        per_control_value_fn=_flat(value_fn),
        control_ids=canon,
        rng=np.random.default_rng(11),
        draws=32,
        eval_cost_per_draw=8,
        sampler=lambda r: (None, KAPPA_META_RELIABILITY),
        **kw,
    )


def _zero_pairs(res: dict[str, Any], zeros: set[str]) -> list[list[str]]:
    return [p for p in res["indistinguishable_pairs"] if p[0] in zeros and p[1] in zeros]


def test_exact_zero_controls_are_not_reported_as_unstable_or_too_close_to_call() -> None:
    zeros = set(_INERT.values())
    totals = dict.fromkeys(zeros, 0.0)
    totals[_POSITIVE["CrowdStrike Falcon EDR"]] = 900.0
    totals[_POSITIVE["Palo Alto Firewall"]] = 400.0
    canon = _canonical(totals)

    # precondition: among the $0 controls, canonical (name) order differs from id order
    canon_zero_block = [c for c in canon if c in zeros]
    assert canon_zero_block != sorted(canon_zero_block)
    assert canon_zero_block == sorted(zeros, key=lambda c: _names()[c])

    def value(_: Any) -> dict[str, float]:
        return {c: totals[c] for c in canon}  # constant: positives never swap, zeros tie

    res = _ensemble(canon, value)

    assert res["state"] == "ok"
    assert _zero_pairs(res, zeros) == []  # 0 "too close to call" pairs among them
    assert res["indistinguishable_pairs"] == []
    for cid in zeros:
        pc = res["per_control"][cid]
        assert pc["stability_class"] == "stable"  # 0 "unstable" badges
        assert pc["rank_min"] == pc["rank_max"] == canon.index(cid)
    assert res["kendall_tau_p50"] == 1.0
    assert len(list(itertools.combinations(sorted(zeros), 2))) == 15  # non-vacuous: 15 pairs


def test_dollar_figures_do_not_depend_on_how_ties_are_broken() -> None:
    """Only rank fields move with the tie-break: every dollar band and the headline are
    identical whichever order the tied block is presented in."""
    zeros = set(_INERT.values())
    totals = dict.fromkeys(zeros, 0.0)
    totals[_POSITIVE["CrowdStrike Falcon EDR"]] = 900.0
    totals[_POSITIVE["Palo Alto Firewall"]] = 400.0
    canon = _canonical(totals)
    shuffled = [c for c in canon if c not in zeros] + sorted(zeros)  # id order, not name order
    assert shuffled != canon

    def value(_: Any) -> dict[str, float]:
        return {c: totals[c] for c in totals}

    a = _ensemble(canon, value)
    b = _ensemble(shuffled, value)
    assert a["headline"] == b["headline"]
    for cid in totals:
        for key in ("reduction_p5", "reduction_p50", "reduction_p95"):
            assert a["per_control"][cid][key] == b["per_control"][cid][key], (cid, key)


# --------------------------------------------------------------------------- #
# through the real attribution path (partial-authoring pattern)                #
# --------------------------------------------------------------------------- #
def _params() -> FAIRParameters:
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 1.0, "mode": 4.0, "high": 9.0}
        ),
        vulnerability=FAIRDistribution(
            DistributionType.PERT, {"low": 0.05, "mode": 0.2, "high": 0.4}
        ),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 11.0, "sigma": 0.7}),
        secondary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 10.0, "sigma": 0.9}),
    )


# name -> (sub_function, unit, capability, coverage, reliability). The two resistance
# controls prevent loss; the other six are structurally inert on their own: three lone
# Detection-visibility controls (the Detection AND-trio is incomplete), two Response
# controls (gated by the resulting zero Detection-Response pair) and one VMC monitoring
# control with no Correction partner.
_AUTHORED = {
    "CrowdStrike Falcon EDR": ("lec_prev_resistance", "probability", 0.62, 0.84, 0.91),
    "Palo Alto Firewall": ("lec_prev_resistance", "probability", 0.48, 0.77, 0.86),
    "Splunk SIEM": ("lec_det_visibility", "probability", 0.55, 0.9, 0.8),
    "Elastic SIEM": ("lec_det_visibility", "probability", 0.4, 0.7, 0.85),
    "Veeam Backup": ("lec_resp_resilience", "probability", 0.6, 0.8, 0.9),
    "Rubrik Recovery": ("lec_resp_resilience", "probability", 0.5, 0.75, 0.88),
    "Cohesity Recovery": ("lec_resp_resilience", "probability", 0.45, 0.7, 0.83),
    "Tenable Scanner": ("vmc_id_control_monitoring", "probability", 0.5, 0.8, 0.9),
}


def test_inert_controls_through_the_real_ensemble_have_no_false_verdicts() -> None:
    ids = {**_POSITIVE, **_INERT}
    controls = [
        make_control(
            control_id=ids[name],
            assignments=[(sf, unit, cap)],
            coverage=cov,
            reliability=rel,
        )
        for name, (sf, unit, cap, cov, rel) in _AUTHORED.items()
    ]
    universe = [c.control_id for c in controls]
    calculator = NativeControlAwareRiskCalculator(controls=controls, n_simulations=100)
    inputs = [("s1", "one", _params()), ("s2", "two", _params())]
    per_scenario_dict = {"s1": universe, "s2": universe}

    by, _ = rex._compute_shapley_by_scenario(
        calculator, inputs, per_scenario_dict, universe, statistic="mean"
    )
    canonical = rex._aggregate_clean(by)
    zeros = {cid for cid, v in canonical.items() if v == 0.0}
    assert zeros == set(_INERT.values())  # the fixture really produces exact $0 controls

    band = {
        "logit_sigma": 0.5,
        "distribution": "logit_normal",
        "seed": 11,
        "draws": 24,
        "eval_budget": 10_000_000,
        "min_draws": 4,
        "shapley_permutations": None,
        "cost_model": "cache_credited",
        "finalize_cost_ratio": FINALIZE_TO_COMPOSE_COST_RATIO,
    }
    res = rex._build_weight_robustness(
        calculator=calculator,
        universe=universe,
        control_names=_names(),
        per_scenario_inputs=inputs,
        per_scenario_dict=per_scenario_dict,
        canonical_values=canonical,
        canonical_values_typical=None,
        persisted={"band": band},
        random_seed=11,
        compute_rank_stability=True,
    )

    assert res["state"] == "ok"
    assert _zero_pairs(res, zeros) == []
    for cid in zeros:
        pc = res["per_control"][cid]
        assert pc["stability_class"] == "stable"
        assert pc["rank_min"] == pc["rank_max"]
