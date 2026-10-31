"""#299: the attribution caches are bounded, and bounding them changes speed only.

ComposedParts is a pure function of the control subset - its controls are composed
in SORTED id order, so the last-ulp fold order of ``or_compose`` cannot follow a
frozenset's iteration order - hence a full cache recomputes on a miss exactly the
value a hit would have returned. These tests pin (a) the bounds are honoured at every
cache creation site, found by structure rather than by variable name, (b) Shapley /
leave-one-out / ensemble outputs under a tiny cap or byte budget are `==` (same process)
to an unbounded cache on ORDER-SENSITIVE and MULTI-ASSIGNMENT fixtures, (c) the cache's
ACTUAL tracemalloc bytes stay within its byte budget on controls that carry 9 LEC
assignments each, for the Shapley-walk and the leave-one-out shapes (an entry's size grows
with the subset and with each member's assignments, so an entry count alone bounds
nothing), and (d) the ensemble's draw-vector cache holds one scenario at a time.
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
_MIB = 1024**2
# tracemalloc over the cache must stay within its estimated-byte budget plus this slack
# (dict table growth and allocator rounding the estimator does not itemize). The estimator
# is an upper envelope (1.06x-2.0x the measured bytes), so in practice it is never reached.
_BUDGET_TOLERANCE = 0.05


def _parts() -> ComposedParts:
    return precompose_parts(
        [make_control(assignments=[("lec_prev_resistance", "probability", 0.5)])]
    )


# --------------------------------------------------------------------------- #
# the cache class and the constant                                             #
# --------------------------------------------------------------------------- #
def test_bounded_cache_never_exceeds_cap_and_existing_keys_keep_hitting() -> None:
    cache = rex._BoundedCompositionCache(TINY_CAP, 64, None)
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
    cache = rex._BoundedCompositionCache(None, 64, None)
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


def test_subsets_above_the_control_cap_are_never_stored() -> None:
    cache = rex._BoundedCompositionCache(None, 4, None)  # no entry cap, subsets up to 4 controls
    parts = _parts()
    fits = frozenset({"a", "b", "c", "d"})
    too_big = frozenset({"a", "b", "c", "d", "e"})
    cache[fits] = parts
    cache[too_big] = parts
    assert list(cache) == [fits]  # the limit is inclusive; one more control is not stored
    assert cache.get(too_big) is None


def test_factory_uses_the_attribution_control_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rex, "MAX_ATTRIBUTION_CONTROLS", 2)
    cache = rex._new_composition_cache()
    parts = _parts()
    cache[frozenset({"a", "b"})] = parts
    cache[frozenset({"a", "b", "c"})] = parts
    assert len(cache) == 1


def test_byte_budget_stops_new_entries_but_not_hits_or_overwrites() -> None:
    parts = _parts()
    key = frozenset({"a"})
    one = rex._estimated_entry_bytes(key, parts)
    cache = rex._BoundedCompositionCache(None, 64, 3 * one)  # room for exactly three
    keys = [frozenset({f"c{i}"}) for i in range(8)]
    for k in keys:
        cache[k] = parts
    assert list(cache) == keys[:3] and cache.estimated_bytes == 3 * one
    assert all(cache.get(k) is parts for k in keys[:3])
    cache[keys[0]] = _parts()  # an overwrite of the same size neither grows nor is refused
    assert len(cache) == 3 and cache.estimated_bytes == 3 * one


def test_entry_estimate_grows_with_the_subset_and_its_assignments() -> None:
    def parts_for(n_controls: int, per_control: int) -> tuple[frozenset[str], ComposedParts]:
        controls = _heavy_controls(n_controls, per_control)
        return frozenset(c.control_id for c in controls), precompose_parts(controls)

    small = rex._estimated_entry_bytes(*parts_for(8, 1))
    more_controls = rex._estimated_entry_bytes(*parts_for(32, 1))
    more_assignments = rex._estimated_entry_bytes(*parts_for(8, 9))
    assert more_controls > small and more_assignments > small
    # 9 LEC assignments on each of 64 controls is the heaviest entry the cache admits
    heavy = rex._estimated_entry_bytes(*parts_for(64, 9))
    assert heavy > 3 * rex._estimated_entry_bytes(*parts_for(64, 1))


def test_budget_constants_are_sane() -> None:
    budget = rex.MAX_COMPOSITION_CACHE_BYTES
    assert isinstance(budget, int) and 0 < budget <= 1024 * _MIB
    entries = rex.MAX_COMPOSITION_CACHE_ENTRIES
    assert isinstance(entries, int) and entries > 0


# The nine Loss-Event sub-functions (units from fair_cam's SUB_FUNCTION_UNITS) and their
# capability values; a control may carry one assignment per sub-function.
_LEC9 = [
    ("lec_prev_resistance", "probability", 0.7),
    ("lec_prev_avoidance", "probability", 0.7),
    ("lec_prev_deterrence", "probability", 0.7),
    ("lec_det_visibility", "probability", 0.7),
    ("lec_det_monitoring", "elapsed_time", 5.0),
    ("lec_det_recognition", "probability", 0.7),
    ("lec_resp_event_termination", "elapsed_time", 5.0),
    ("lec_resp_resilience", "probability", 0.7),
    ("lec_resp_loss_reduction", "currency", 10_000.0),
]


def _heavy_controls(n: int, per_control: int) -> list[Any]:
    """n controls with ``per_control`` Loss-Event assignments each (up to the 9 the schema
    allows), cycling the sub-functions so different controls cover different ones."""
    return [
        make_control(
            control_id=f"{i:08x}-0000-4000-8000-{i:012x}",
            assignments=[_LEC9[(i + k) % 9] for k in range(per_control)],
            coverage=0.8,
            reliability=0.85,
        )
        for i in range(n)
    ]


def _retained_bytes(fill: Any) -> tuple[Any, int]:
    """Run ``fill(cache)`` under tracemalloc; return (cache, bytes the cache retains)."""
    import gc
    import tracemalloc

    gc.collect()
    tracemalloc.start()
    base, _ = tracemalloc.get_traced_memory()
    cache = rex._new_composition_cache()
    result = fill(cache)
    del result
    gc.collect()
    retained, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return cache, retained - base


@pytest.mark.parametrize("shape", ["shapley_walk", "leave_one_out"])
def test_cache_bytes_stay_within_the_budget_on_nine_assignment_controls(
    shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real cache, the real passes, controls carrying 9 LEC assignments each at 64-65
    controls: the heaviest entries the bounds admit (~69 KB each; 75,000 of them were 4.7 GiB
    under the old entry cap). Scaled to a 12 MiB budget so the test is quick (the estimator is
    linear, so the check is the same at 512 MiB): the budget must bind, and tracemalloc's
    ACTUAL retained bytes must stay within it, for the Shapley-walk shape (random subsets of a
    64-control scenario) and the leave-one-out shape (64-control N-minus-one subsets)."""
    budget = 12 * _MIB
    monkeypatch.setattr(rex, "MAX_COMPOSITION_CACHE_BYTES", budget)
    n = 64 if shape == "shapley_walk" else 65
    controls = _heavy_controls(n * 2, 9)
    universe = [c.control_id for c in controls]
    calc = NativeControlAwareRiskCalculator(controls=controls, n_simulations=100)
    inputs = [(f"s{i}", f"s{i}", _params()) for i in range(3)]
    import random

    rng = random.Random(3)
    per_scenario = {sid: sorted(rng.sample(universe, n)) for sid, _n, _p in inputs}

    def fill(cache: Any) -> Any:
        if shape == "shapley_walk":
            # a 15-permutation sampled walk per scenario (the production walk uses 4,612)
            return rex._compute_shapley_by_scenario(
                calc,
                inputs,
                per_scenario,
                universe,
                sample_permutations=15,
                total_eval_budget=10**9,
                composition_cache=cache,
            )
        return rex._compute_loo_by_scenario(
            calc, inputs, per_scenario, universe, composition_cache=cache
        )

    cache, retained = _retained_bytes(fill)

    assert cache.estimated_bytes <= budget  # the bound the code enforces
    assert cache.estimated_bytes >= 0.9 * budget  # ...and it really bound (non-vacuous)
    assert retained <= budget * (1 + _BUDGET_TOLERANCE), (retained / _MIB, budget / _MIB)
    assert retained <= cache.estimated_bytes  # the estimate is an upper envelope
    assert max(len(k) for k in cache) <= 64


def test_results_are_identical_under_a_tiny_byte_budget_on_multi_assignment_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pure memo: with a byte budget so small that nearly every subset is recomputed,
    typical and mean Shapley and LOO are `==` to an unbounded run, on controls that share
    sub-functions and carry up to 9 LEC assignments each."""
    controls = [
        make_control(
            control_id=f"c{i:02d}",
            assignments=[_LEC9[(i + k) % 9] for k in range(1 + (i % 5) * 2)],  # 1, 3, 5, 7, 9
            coverage=0.6 + 0.03 * (i % 7),
            reliability=0.7 + 0.02 * (i % 9),
        )
        for i in range(10)
    ]
    universe = [c.control_id for c in controls]
    calc = NativeControlAwareRiskCalculator(controls=controls, n_simulations=100)
    inputs = [("s1", "one", _params()), ("s2", "two", _params())]
    scenario_controls = {"s1": universe, "s2": universe[2:9]}

    def run(budget: int | None) -> tuple[dict[str, Any], dict[frozenset[str], ComposedParts]]:
        monkeypatch.setattr(rex, "MAX_COMPOSITION_CACHE_BYTES", budget)
        cache = rex._new_composition_cache()
        out: dict[str, Any] = {}
        for stat in ("typical", "mean"):
            out[f"shapley_{stat}"] = rex._compute_shapley_by_scenario(
                calc,
                inputs,
                scenario_controls,
                universe,
                composition_cache=cache,
                statistic=stat,
            )
            out[f"loo_{stat}"] = rex._compute_loo_by_scenario(
                calc,
                inputs,
                scenario_controls,
                universe,
                composition_cache=cache,
                statistic=stat,
            )
        return out, cache

    unbounded, full = run(None)
    tiny, small = run(60_000)  # a handful of 10-30 KB entries

    assert len(small) < len(full) and len(small) >= 1
    assert tiny == unbounded
    assert any(v != 0.0 for v in unbounded["loo_typical"][0]["s1"].values())  # non-vacuous


def _src_tree() -> ast.Module:
    return ast.parse(Path(rex.__file__).read_text(encoding="utf-8"))


def _called_names(node: ast.AST) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _has_empty_dict(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Dict) and not n.keys:
            return True
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "dict"
            and not n.args
            and not n.keywords
        ):
            return True
    return False


def _bound_names(tree: ast.Module, callee: str) -> set[str]:
    """Every name (any spelling) assigned a value that calls ``callee``."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and callee in _called_names(n.value):
            out.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif (
            isinstance(n, ast.AnnAssign)
            and n.value is not None
            and isinstance(n.target, ast.Name)
            and callee in _called_names(n.value)
        ):
            out.add(n.target.id)
    return out


def test_every_composition_cache_creation_site_uses_the_bounded_factory() -> None:
    """Structural, not name-based: whatever a future memo is called, no `{}` may create
    or default a ComposedParts cache, and every dict that `precompose_parts` results
    are written into must come from `_new_composition_cache()` or be a parameter."""
    tree = _src_tree()

    # (1) the factory is used exactly at the five known creation sites
    factory_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_new_composition_cache"
    ]
    # shapley default, LOO default, ensemble, SINGLE canonical, AGGREGATE canonical,
    # plus the factory's own definition is a FunctionDef, not a Call
    assert len(factory_calls) == 5

    # (2) no empty-dict creation of anything typed as a ComposedParts cache, no
    # `composition_cache={}` argument, no non-None default for that parameter
    for n in ast.walk(tree):
        if isinstance(n, ast.AnnAssign) and n.value is not None:
            annotated = {x.id for x in ast.walk(n.annotation) if isinstance(x, ast.Name)}
            if "ComposedParts" in annotated:
                assert not _has_empty_dict(n.value), n.lineno
        if isinstance(n, ast.keyword) and n.arg == "composition_cache":
            assert not _has_empty_dict(n.value)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            pos = n.args.posonlyargs + n.args.args
            defaults = [None] * (len(pos) - len(n.args.defaults)) + list(n.args.defaults)
            pairs = list(zip(pos, defaults, strict=True)) + list(
                zip(n.args.kwonlyargs, n.args.kw_defaults, strict=True)
            )
            for arg, default in pairs:
                if arg.arg == "composition_cache" and default is not None:
                    assert isinstance(default, ast.Constant) and default.value is None, n.lineno

    # (3) data flow: each `X[...] = <precompose_parts result>` writes into a dict that
    # is a parameter of an enclosing function or was built by the bounded factory
    good = _bound_names(tree, "_new_composition_cache")
    parents: dict[ast.AST, ast.AST] = {
        c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)
    }

    def enclosing_functions(node: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        chain = []
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                chain.append(node)
        return chain

    sites = 0
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "precompose_parts"
        ):
            continue
        sites += 1
        assign = parents[call]
        assert isinstance(assign, ast.Assign) and isinstance(assign.targets[0], ast.Name)
        parts_var = assign.targets[0].id
        chain = enclosing_functions(call)
        params = {
            a.arg for fn in chain for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        }
        writes = [
            w
            for w in ast.walk(chain[0])
            if isinstance(w, ast.Assign)
            and isinstance(w.value, ast.Name)
            and w.value.id == parts_var
            and isinstance(w.targets[0], ast.Subscript)
            and isinstance(w.targets[0].value, ast.Name)
        ]
        assert writes, f"precompose_parts at line {call.lineno} has no recognizable memo write"
        for w in writes:
            target = w.targets[0]
            assert isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
            assert target.value.id in good | params, (target.value.id, w.lineno)
    # value fn + ensemble _coalition_vec; a third composition path must update this guard
    assert sites == 2


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


# (id, sub_function, unit, capability, coverage, reliability). ORDER-SENSITIVE on
# purpose: four controls share lec_prev_resistance, three share lec_det_visibility and
# three share vmc_prev_reduce_variance_prob, with non-round parameters, so
# OR-composition over THREE or more operands is exercised. A two-operand
# 1 - (1-a)(1-b) is commutative in IEEE-754; three or more operands are not, so
# without the sorted composition order different frozenset instances of one subset
# fold to different last-ulp values (and different tie order among inert controls).
_SPEC = [
    ("lec_a", "lec_prev_resistance", "probability", 0.371, 0.83, 0.79),
    ("lec_b", "lec_prev_resistance", "probability", 0.529, 0.71, 0.88),
    ("lec_c", "lec_prev_resistance", "probability", 0.613, 0.77, 0.69),
    ("lec_d", "lec_prev_resistance", "probability", 0.447, 0.93, 0.74),
    ("lec_e", "lec_det_visibility", "probability", 0.417, 0.92, 0.81),
    ("lec_f", "lec_det_visibility", "probability", 0.283, 0.66, 0.93),
    ("lec_g", "lec_det_visibility", "probability", 0.571, 0.74, 0.84),
    ("lec_h", "lec_resp_resilience", "probability", 0.633, 0.64, 0.93),
    ("vmc_a", "vmc_prev_reduce_variance_prob", "percent_reduction", 0.331, 0.77, 0.86),
    ("vmc_b", "vmc_prev_reduce_variance_prob", "percent_reduction", 0.468, 0.69, 0.74),
    ("vmc_c", "vmc_prev_reduce_variance_prob", "percent_reduction", 0.297, 0.88, 0.91),
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
    # s1 holds all twelve controls (4096 subsets, exact Shapley); s2 an overlapping
    # nine in a different order, so the subset-keyed cache is hit across scenarios
    # when it can store them and frozensets of one subset are built several ways.
    per_scenario_inputs = [("s1", "one", _params()), ("s2", "two", _params())]
    per_scenario_dict = {
        "s1": universe,
        "s2": universe[3:5] + universe[9:] + universe[:2] + universe[7:9],
    }
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
    assert len(shap_s1) == 12 and any(v != 0.0 for v in shap_s1.values())
    assert capped["ensemble"]["state"] == "ok" and capped["ensemble"]["draws_used"] == 12


def test_results_are_identical_when_large_subsets_are_not_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave-one-out composes the full set and every N-minus-one subset; the cache refuses to
    store any subset over MAX_ATTRIBUTION_CONTROLS. Shrinking that limit to 4 makes almost
    every subset of the 12-control fixture "too big", so nearly everything is recomputed:
    typical and mean Shapley and LOO must still be `==` to the run that stored them all."""
    calculator, universe = _calculator()
    inputs = [("s1", "one", _params()), ("s2", "two", _params())]
    scenario_controls = {
        "s1": universe,
        "s2": universe[3:5] + universe[9:] + universe[:2] + universe[7:9],
    }

    def run(limit: int) -> tuple[dict[str, Any], dict[frozenset[str], ComposedParts]]:
        monkeypatch.setattr(rex, "MAX_ATTRIBUTION_CONTROLS", limit)
        cache = rex._new_composition_cache()
        out: dict[str, Any] = {}
        for stat in ("typical", "mean"):
            out[f"shapley_{stat}"] = rex._compute_shapley_by_scenario(
                calculator,
                inputs,
                scenario_controls,
                universe,
                composition_cache=cache,
                statistic=stat,
            )
            out[f"loo_{stat}"] = rex._compute_loo_by_scenario(
                calculator,
                inputs,
                scenario_controls,
                universe,
                composition_cache=cache,
                statistic=stat,
            )
        return out, cache

    stored, stored_cache = run(64)
    unstored, unstored_cache = run(4)

    assert max(len(k) for k in stored_cache) == 12  # the full set and its N-minus-one subsets
    assert max(len(k) for k in unstored_cache) <= 4  # over the limit: computed, never stored
    assert len(unstored_cache) < len(stored_cache)
    assert unstored == stored  # a pure memo: only speed differs
    loo = stored["loo_typical"][0]["s1"]
    assert len(loo) == 12 and any(v != 0.0 for v in loo.values())  # non-vacuous


def test_controls_are_composed_in_sorted_order_at_both_composition_sites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deterministic half of the order-sensitivity guard. `precompose_parts` folds
    `or_compose` in list order, so both sites must hand it the controls sorted by id
    whatever the frozenset's iteration order is. Records every call made by the scalar
    value fn AND the ensemble's `_coalition_vec` (both run in the battery)."""
    import fair_cam.risk_engine.group_composition as gcomp

    seen: list[list[str]] = []
    real = gcomp.precompose_parts

    def spy(controls: list[Any]) -> ComposedParts:
        seen.append([c.control_id for c in controls])
        return real(controls)

    monkeypatch.setattr(gcomp, "precompose_parts", spy)
    _battery(TINY_CAP, monkeypatch)

    assert len(seen) > 1000 and any(len(ids) >= 3 for ids in seen)  # non-vacuous
    assert all(ids == sorted(ids) for ids in seen)


# --------------------------------------------------------------------------- #
# the ensemble's draw-vector cache holds one scenario at a time                #
# --------------------------------------------------------------------------- #
def test_scenario_vector_cache_clears_when_the_scenario_changes() -> None:
    import numpy as np

    cache = rex._ScenarioVectorCache()
    a, b = frozenset({"x"}), frozenset({"x", "y"})
    vec = np.zeros(3)
    cache.put("s1", a, vec)
    cache.put("s1", b, vec)
    assert cache.get("s1", a) is vec and cache.get("s1", b) is vec
    assert cache.get("s2", a) is None  # entering s2 dropped s1's entries
    cache.put("s2", a, vec)
    assert cache.get("s1", b) is None and cache.get("s1", a) is None
    assert len(cache._vecs) == 0  # the last call entered s1 and found nothing


def test_ensemble_walk_clears_the_draw_vector_cache_per_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard that the executor's ensemble really scopes its cache: across the two
    scenarios of the battery the cache is entered once per scenario, is EMPTY at each
    entry (s1's vectors are gone when s2 starts), and never holds more than one
    scenario's subsets (<= 2^12)."""
    instances: list[Any] = []

    class _Spy(rex._ScenarioVectorCache):
        def __init__(self) -> None:
            super().__init__()
            self.entered: list[str] = []
            self.live_after_entry: list[int] = []
            self.max_live = 0
            instances.append(self)

        def _enter(self, sid: str) -> None:
            changed = sid != self._sid
            super()._enter(sid)
            if changed:
                self.entered.append(sid)
                self.live_after_entry.append(len(self._vecs))

        def put(self, sid: str, subset: frozenset[str], vec: Any) -> None:
            super().put(sid, subset, vec)
            self.max_live = max(self.max_live, len(self._vecs))

    monkeypatch.setattr(rex, "_ScenarioVectorCache", _Spy)
    _battery(None, monkeypatch)

    assert instances, "the ensemble never built its draw-vector cache"
    for inst in instances:
        assert inst.entered == ["s1", "s2"]  # each scenario visited once, in order
        assert inst.live_after_entry == [0, 0]  # nothing from the previous scenario survives
        assert 0 < inst.max_live <= 2**12


def test_ensemble_is_identical_with_and_without_the_per_scenario_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clearing changes memory only: the ensemble blob equals the one produced by the
    pre-fix cache, which kept every (scenario, subset) vector for the whole walk."""

    class _Unscoped(rex._ScenarioVectorCache):
        def __init__(self) -> None:
            super().__init__()
            self._all: dict[tuple[str, frozenset[str]], Any] = {}

        def get(self, sid: str, subset: frozenset[str]) -> Any:
            return self._all.get((sid, subset))

        def put(self, sid: str, subset: frozenset[str], vec: Any) -> None:
            self._all[(sid, subset)] = vec

    scoped, _ = _battery(None, monkeypatch)
    monkeypatch.setattr(rex, "_ScenarioVectorCache", _Unscoped)
    unscoped, _ = _battery(None, monkeypatch)
    assert json.dumps(scoped["ensemble"], sort_keys=True) == json.dumps(
        unscoped["ensemble"], sort_keys=True
    )
    assert scoped["ensemble"]["state"] == "ok"


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


@pytest.mark.asyncio
async def test_single_run_frees_the_canonical_cache_before_the_ensemble(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE used to keep its canonical composition cache alive under the ensemble,
    which builds a second one of its own (1,773 MiB vs 1,207 MiB at 64 controls). Like
    the AGGREGATE `del _canon_comp_cache`, it must be gone when the ensemble starts."""
    import weakref

    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    fw = await seed_control_factory(name="FW", domain=ControlDomain.LOSS_EVENT)
    s = await seed_scenario_factory(name="S0")
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s.id, control_id=edr.id),
            ScenarioControl(scenario_id=s.id, control_id=fw.id),
        ]
    )
    await db_session.commit()

    created: list[weakref.ref[dict[frozenset[str], ComposedParts]]] = []

    def spy_factory() -> dict[frozenset[str], ComposedParts]:
        cache = _REAL_FACTORY()
        created.append(weakref.ref(cache))
        return cache

    alive_when_ensemble_starts: list[int] = []
    real_ensemble = rex._build_weight_robustness

    def spy_ensemble(*args: Any, **kwargs: Any) -> Any:
        alive_when_ensemble_starts.append(sum(ref() is not None for ref in created))
        return real_ensemble(*args, **kwargs)

    monkeypatch.setattr(rex, "_new_composition_cache", spy_factory)
    monkeypatch.setattr(rex, "_build_weight_robustness", spy_ensemble)
    run = await _run(db_session, seed_organization, seed_user, [s.id])

    assert run.weight_robustness is not None
    assert len(created) >= 1  # the canonical cache existed
    assert alive_when_ensemble_starts == [0]  # ...and was already freed
