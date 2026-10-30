"""#299: the attribution ComposedParts caches are bounded, and bounding them changes
speed only.

ComposedParts is a pure function of the control subset, so a full cache recomputes
on a miss and does not store. These tests pin (a) the cap is honoured at every
cache creation site, (b) Shapley / leave-one-out / ensemble outputs under a tiny
cap are EXACTLY (`==`, same process) those of an unbounded cache, and (c) the
constant keeps a worst-case cache under 1 GiB (the arithmetic is in the constant's
comment in services/run_executor.py).
"""

from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters
from fair_cam.risk_engine.group_composition import ComposedParts, precompose_parts
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from fair_cam.tests.risk_engine._helpers import make_control
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlDomain, FairCamSubFunction
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.scenario_control import ScenarioControl
from idraa.services import run_executor as rex
from idraa.services.runs import RunService
from idraa.services.weight_robustness import FINALIZE_TO_COMPOSE_COST_RATIO

TINY_CAP = 3
_REAL_FACTORY = rex._new_composition_cache  # un-spied; spies wrap this, never each other
# Worst measured bytes per cache entry (key + ComposedParts), 64-player corner.
_WORST_BYTES_PER_ENTRY = 7_400


def _parts() -> ComposedParts:
    return precompose_parts(
        [make_control(assignments=[("lec_prev_resistance", "probability", 0.5)])]
    )


# --------------------------------------------------------------------------- #
# the cache class and the constant                                             #
# --------------------------------------------------------------------------- #
def test_bounded_cache_never_exceeds_cap_and_existing_keys_keep_hitting() -> None:
    cache = rex._BoundedCompositionCache(TINY_CAP)
    parts = _parts()
    keys = [frozenset({f"c{i}"}) for i in range(10)]
    for k in keys:
        cache[k] = parts
        assert len(cache) <= TINY_CAP
    assert len(cache) == TINY_CAP
    assert list(cache) == keys[:TINY_CAP]  # first-come entries are kept
    assert all(cache.get(k) is parts for k in keys[:TINY_CAP])
    assert all(cache.get(k) is None for k in keys[TINY_CAP:])  # skipped, not stored
    # an existing key may be overwritten even when full (no growth)
    other = _parts()
    cache[keys[0]] = other
    assert cache[keys[0]] is other and len(cache) == TINY_CAP


def test_none_cap_is_unbounded() -> None:
    cache = rex._BoundedCompositionCache(None)
    parts = _parts()
    for i in range(500):
        cache[frozenset({f"c{i}"})] = parts
    assert len(cache) == 500


def test_factory_reads_the_module_constant_at_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rex, "MAX_COMPOSITION_CACHE_ENTRIES", 5)
    c5 = rex._new_composition_cache()
    monkeypatch.setattr(rex, "MAX_COMPOSITION_CACHE_ENTRIES", None)
    cn = rex._new_composition_cache()
    parts = _parts()
    for i in range(20):
        c5[frozenset({f"c{i}"})] = parts
        cn[frozenset({f"c{i}"})] = parts
    assert (len(c5), len(cn)) == (5, 20)


def test_cap_constant_keeps_worst_case_cache_under_one_gib() -> None:
    cap = rex.MAX_COMPOSITION_CACHE_ENTRIES
    assert isinstance(cap, int) and cap > 0
    assert cap * _WORST_BYTES_PER_ENTRY < 1024**3


_CACHE_NAMES = {"_comp_cache", "_canon_comp_cache"}


def test_every_cache_creation_site_uses_the_bounded_factory() -> None:
    """No `{}` literal may create (or default) an attribution composition cache."""
    tree = ast.parse(Path(rex.__file__).read_text(encoding="utf-8"))
    sites = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if name not in _CACHE_NAMES or value is None:
            continue
        sites += 1
        calls = {
            n.func.id
            for n in ast.walk(value)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_new_composition_cache" in calls, (name, node.lineno)
    # shapley default, LOO default, ensemble, SINGLE canonical, AGGREGATE canonical
    assert sites == 5


# --------------------------------------------------------------------------- #
# executor attribution functions: cap changes speed, never values              #
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


# (id, sub_function, unit, capability, coverage, reliability). Two LEC resistance
# controls and two VMC variance controls share a sub-function, so OR-composition
# across controls (order-sensitive in the last ulp) is exercised by every subset
# that holds both; the capabilities are deliberately non-round.
_SPEC = [
    ("lec_a", "lec_prev_resistance", "probability", 0.371, 0.83, 0.79),
    ("lec_b", "lec_prev_resistance", "probability", 0.529, 0.71, 0.88),
    ("lec_c", "lec_det_visibility", "probability", 0.417, 0.92, 0.81),
    ("lec_d", "lec_resp_resilience", "probability", 0.633, 0.64, 0.93),
    ("vmc_a", "vmc_prev_reduce_variance_prob", "percent_reduction", 0.331, 0.77, 0.86),
    ("vmc_b", "vmc_prev_reduce_variance_prob", "percent_reduction", 0.468, 0.69, 0.74),
    ("vmc_c", "vmc_id_control_monitoring", "probability", 0.552, 0.88, 0.82),
    ("vmc_d", "vmc_id_threat_intelligence", "probability", 0.297, 0.73, 0.91),
    ("dsc_a", "dsc_prev_defined_expectations", "probability", 0.61, 0.81, 0.77),
]


def _calculator() -> tuple[NativeControlAwareRiskCalculator, list[str]]:
    controls = [
        make_control(
            control_id=cid,
            assignments=[(sf, unit, cap)],
            coverage=cov,
            reliability=rel,
        )
        for cid, sf, unit, cap, cov, rel in _SPEC
    ]
    universe = [c.control_id for c in controls]
    return NativeControlAwareRiskCalculator(controls=controls, n_simulations=100), universe


def _battery(
    cap: int | None, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], list[dict[frozenset[str], ComposedParts]]]:
    """Every attribution pass the executor runs, under `cap`; returns the values
    and every cache the run created (to assert the bound)."""
    monkeypatch.setattr(rex, "MAX_COMPOSITION_CACHE_ENTRIES", cap)
    created: list[dict[frozenset[str], ComposedParts]] = []

    def spy() -> dict[frozenset[str], ComposedParts]:
        cache = _REAL_FACTORY()
        created.append(cache)
        return cache

    monkeypatch.setattr(rex, "_new_composition_cache", spy)

    calculator, universe = _calculator()
    # s1 holds all nine controls (512 subsets); s2 an overlapping seven, so the
    # subset-keyed cache is hit across scenarios when it can store them.
    per_scenario_inputs = [("s1", "one", _params()), ("s2", "two", _params())]
    per_scenario_dict = {"s1": universe, "s2": universe[:3] + universe[4:8]}
    avail = {"s1": False, "s2": True}

    out: dict[str, Any] = {}
    shared = rex._new_composition_cache()  # the executor's shared canonical cache
    for stat in ("typical", "mean"):
        out[f"shapley_{stat}"] = rex._compute_shapley_by_scenario(
            calculator,
            per_scenario_inputs,
            per_scenario_dict,
            universe,
            per_scenario_availability=avail,
            composition_cache=shared,
            statistic=stat,
        )
        out[f"loo_{stat}"] = rex._compute_loo_by_scenario(
            calculator,
            per_scenario_inputs,
            per_scenario_dict,
            universe,
            per_scenario_availability=avail,
            composition_cache=shared,
            statistic=stat,
        )
    # default path (composition_cache=None -> each call makes its own bounded cache)
    out["shapley_default"] = rex._compute_shapley_by_scenario(
        calculator, per_scenario_inputs, per_scenario_dict, universe
    )
    out["loo_default"] = rex._compute_loo_by_scenario(
        calculator, per_scenario_inputs, per_scenario_dict, universe
    )
    # the weight-uncertainty ensemble (its own cross-draw cache)
    canonical = rex._aggregate_clean(out["shapley_mean"][0])
    band = {
        "logit_sigma": 0.5,
        "distribution": "logit_normal",
        "seed": 11,
        "draws": 12,
        "eval_budget": 10_000_000,
        "min_draws": 4,
        "shapley_permutations": None,
        "cost_model": "cache_credited",
        "finalize_cost_ratio": FINALIZE_TO_COMPOSE_COST_RATIO,
    }
    out["ensemble"] = rex._build_weight_robustness(
        calculator=calculator,
        universe=universe,
        control_names={cid: cid for cid in universe},
        per_scenario_inputs=per_scenario_inputs,
        per_scenario_dict=per_scenario_dict,
        canonical_values=canonical,
        canonical_values_typical=None,
        persisted={"band": band},
        random_seed=11,
        compute_rank_stability=True,
        per_scenario_availability=avail,
    )
    return out, created


def test_attribution_under_tiny_cap_is_bit_identical_to_unbounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unbounded, unbounded_caches = _battery(None, monkeypatch)
    capped, capped_caches = _battery(TINY_CAP, monkeypatch)

    # (a) the cap is honoured by every cache the passes created, and it bit
    assert len(capped_caches) == len(unbounded_caches) == 4  # shared, 2 defaults, ensemble
    assert all(len(c) <= TINY_CAP for c in capped_caches)
    assert max(len(c) for c in unbounded_caches) > TINY_CAP  # the cap really cut
    assert all(len(c) == TINY_CAP for c in capped_caches)

    # (b) exact equality: same process, same hash order, same operations
    for key in ("shapley_typical", "shapley_mean", "loo_typical", "loo_mean"):
        assert capped[key] == unbounded[key], key
    assert capped["shapley_default"] == unbounded["shapley_default"]
    assert capped["loo_default"] == unbounded["loo_default"]
    assert json.dumps(capped["ensemble"], sort_keys=True) == json.dumps(
        unbounded["ensemble"], sort_keys=True
    )
    # non-vacuous: real attribution values were compared
    shap_s1 = capped["shapley_typical"][0]["s1"]
    assert len(shap_s1) == 9 and any(v != 0.0 for v in shap_s1.values())
    assert capped["ensemble"]["state"] == "ok" and capped["ensemble"]["draws_used"] == 12


# --------------------------------------------------------------------------- #
# through execute_run (SINGLE + AGGREGATE canonical-cache sites, with inheritance) #
# --------------------------------------------------------------------------- #
async def _hybrid(
    db: AsyncSession, seed_control_factory: Any, seed_organization: Any, name: str
) -> Control:
    ctrl: Control = await seed_control_factory(
        name=name,
        domain=ControlDomain.VARIANCE_MANAGEMENT,
        sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    )
    db.add(
        ControlFunctionAssignment(
            control_id=ctrl.id,
            organization_id=seed_organization.id,
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=0.9,
            coverage=0.8,
            reliability=0.9,
        )
    )
    await db.commit()
    return ctrl


async def _run(
    db: AsyncSession, org: Any, user: Any, scenario_ids: list[uuid.UUID]
) -> RiskAnalysisRun:
    run = await RunService(db).create_and_dispatch(
        organization_id=org.id,
        scenario_ids=scenario_ids,
        mc_iterations_override=300,
        created_by=user.id,
        background_tasks=BackgroundTasks(),
        random_seed=7,
    )
    await db.refresh(run)
    assert run.status == RunStatus.COMPLETED, run.error_message
    return run


def _attribution_view(run: RiskAnalysisRun) -> str:
    assert run.simulation_results is not None
    payload = run.simulation_results
    scenarios = payload.get("per_scenario") or [payload]
    view = {
        "adjustments": [
            sorted(
                (
                    {
                        k: a.get(k)
                        for k in (
                            "control_id",
                            "shapley_value",
                            "shapley_value_mean",
                            "if_removed_value",
                            "if_removed_value_mean",
                        )
                    }
                    for a in s["control_adjustments"]
                ),
                key=lambda d: str(d["control_id"]),
            )
            for s in scenarios
        ],
        "weight_robustness": run.weight_robustness,
    }
    return json.dumps(view, sort_keys=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("n_scenarios", [1, 2], ids=["single", "aggregate"])
async def test_execute_run_values_identical_with_tiny_cap(
    n_scenarios: int,
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both canonical-cache sites in `_execute_run_body`, with an org-wide variant
    and two explicit LEC links per scenario (3 players: 8 subsets > the cap)."""
    org_wide = await _hybrid(db_session, seed_control_factory, seed_organization, "Patch Mgmt")
    org_wide.org_wide_meta = True
    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    fw = await seed_control_factory(name="FW", domain=ControlDomain.LOSS_EVENT)
    scenario_ids: list[uuid.UUID] = []
    for i in range(n_scenarios):
        s = await seed_scenario_factory(name=f"S{i}")
        db_session.add_all(
            [
                ScenarioControl(scenario_id=s.id, control_id=edr.id),
                ScenarioControl(scenario_id=s.id, control_id=fw.id),
            ]
        )
        scenario_ids.append(s.id)
    await db_session.commit()

    created: list[dict[frozenset[str], ComposedParts]] = []

    def spy() -> dict[frozenset[str], ComposedParts]:
        cache = _REAL_FACTORY()
        created.append(cache)
        return cache

    monkeypatch.setattr(rex, "_new_composition_cache", spy)

    monkeypatch.setattr(rex, "MAX_COMPOSITION_CACHE_ENTRIES", None)
    unbounded = await _run(db_session, seed_organization, seed_user, scenario_ids)
    unbounded_caches = list(created)
    created.clear()
    monkeypatch.setattr(rex, "MAX_COMPOSITION_CACHE_ENTRIES", TINY_CAP)
    capped = await _run(db_session, seed_organization, seed_user, scenario_ids)

    assert created and all(len(c) <= TINY_CAP for c in created)
    assert max(len(c) for c in unbounded_caches) > TINY_CAP  # the unbounded run needed more
    assert _attribution_view(capped) == _attribution_view(unbounded)
    # non-vacuous: the compared view holds real attribution numbers for the variant too
    assert capped.simulation_results is not None
    scenarios = capped.simulation_results.get("per_scenario") or [capped.simulation_results]
    adj = scenarios[0]["control_adjustments"]
    assert any(a["control_id"].endswith(":meta") for a in adj)
    # AGGREGATE injects Shapley into each adjustment; SINGLE injects "if removed" there
    # and carries its Shapley in weight_robustness (both are in the compared view).
    key = "shapley_value" if n_scenarios > 1 else "if_removed_value"
    assert all(a.get(key) is not None for a in adj)
    assert capped.weight_robustness is not None
