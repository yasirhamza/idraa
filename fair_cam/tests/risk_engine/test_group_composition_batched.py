"""Equivalence: finalize_reduce_batched(...)[k] must match the SCALAR path
(finalize_composition + reduction_from_composition) element-for-element for
every draw k. This is the correctness contract that lets the weight-robustness
ensemble batch the K-draw finalize/reduce instead of calling the scalar path
16.8M times. Exercises every LEC branch (OR-trio, detection/response gate,
currency subtractor, weak-AND response) plus VMC/DSC meta uplift and the
availability-self-detection recovery gate.
"""

from __future__ import annotations

import copy

import numpy as np
import pytest

from fair_cam.models.composition_topology import GROUP_NODE_MAPPING
from fair_cam.models.sub_function import SUB_FUNCTION_UNITS, FairCamSubFunction, UnitType
from fair_cam.risk_engine.control_attribution import reduction_from_composition, scenario_base_ale
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters
from fair_cam.risk_engine.group_composition import finalize_composition, precompose_parts
from fair_cam.risk_engine.group_composition_batched import (
    finalize_reduce_batched,
    stack_node_weight_arrays,
)
from fair_cam.tests.risk_engine._helpers import make_control

# One control per sub-function, so random subsets exercise every group/gate.
_ALL_SUBFUNCTIONS = list(FairCamSubFunction)


def _cap_for_unit(unit: UnitType, rng: np.random.Generator) -> float:
    if unit == UnitType.PROBABILITY:
        return float(rng.uniform(0.1, 0.9))
    if unit == UnitType.ELAPSED_TIME:
        return float(rng.uniform(1.0, 72.0))  # hours-ish, positive
    if unit == UnitType.CURRENCY:
        return float(rng.uniform(1_000.0, 50_000.0))
    return float(rng.uniform(0.1, 0.9))


def _make_universe(rng: np.random.Generator) -> list:
    controls = []
    for i, sf in enumerate(_ALL_SUBFUNCTIONS):
        unit = SUB_FUNCTION_UNITS[sf]
        controls.append(
            make_control(
                control_id=f"c{i}_{sf.value}",
                assignments=[(sf.value, unit.value, _cap_for_unit(unit, rng))],
                coverage=float(rng.uniform(0.3, 1.0)),
                reliability=float(rng.uniform(0.2, 1.0)),
            )
        )
    return controls


def _random_node_mappings(k: int, rng: np.random.Generator) -> list:
    """K perturbed copies of GROUP_NODE_MAPPING with weights in (0,1)."""
    draws = []
    for _ in range(k):
        nm = copy.deepcopy(GROUP_NODE_MAPPING)
        for mapping in nm.values():
            for target in mapping.targets:
                mapping.weights[target] = float(rng.uniform(0.01, 0.99))
        draws.append(nm)
    return draws


def _base() -> tuple[float, float, float, float, float]:
    params = FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 2.0, "mode": 6.0, "high": 12.0}
        ),
        vulnerability=FAIRDistribution(
            DistributionType.PERT, {"low": 0.05, "mode": 0.15, "high": 0.35}
        ),
        primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 11.5, "sigma": 0.8}),
        secondary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 10.0, "sigma": 1.0}),
    )
    return scenario_base_ale(params, "mean")


@pytest.mark.parametrize("availability", [False, True])
@pytest.mark.parametrize("seed", list(range(12)))
def test_batched_matches_scalar_random_subsets(seed: int, availability: bool) -> None:
    rng = np.random.default_rng(seed)
    universe = _make_universe(rng)
    base = _base()
    k = 24
    kappa_arr = rng.uniform(0.0, 1.0, size=k)
    node_mappings = _random_node_mappings(k, rng)
    node_weight_arrs = stack_node_weight_arrays(node_mappings)

    # a random non-empty subset of controls
    m = int(rng.integers(1, len(universe) + 1))
    subset = list(rng.choice(universe, size=m, replace=False))
    parts = precompose_parts(subset)

    scalar = np.array(
        [
            reduction_from_composition(
                base,
                finalize_composition(parts, kappa=float(kappa_arr[i])),
                node_mappings[i],
                availability_self_detection=availability,
            )
            for i in range(k)
        ]
    )
    batched = finalize_reduce_batched(
        parts, base, kappa_arr, node_weight_arrs, availability_self_detection=availability
    )
    # Operation order is preserved, so this is bit-identical, not just close.
    np.testing.assert_array_equal(batched, scalar)


def test_batched_matches_scalar_full_universe() -> None:
    """All sub-functions present at once (every group populated, all gates active)."""
    rng = np.random.default_rng(999)
    universe = _make_universe(rng)
    base = _base()
    k = 40
    kappa_arr = rng.uniform(0.0, 1.0, size=k)
    node_mappings = _random_node_mappings(k, rng)
    node_weight_arrs = stack_node_weight_arrays(node_mappings)
    parts = precompose_parts(universe)

    for availability in (False, True):
        scalar = np.array(
            [
                reduction_from_composition(
                    base,
                    finalize_composition(parts, kappa=float(kappa_arr[i])),
                    node_mappings[i],
                    availability_self_detection=availability,
                )
                for i in range(k)
            ]
        )
        batched = finalize_reduce_batched(
            parts, base, kappa_arr, node_weight_arrs, availability_self_detection=availability
        )
        np.testing.assert_array_equal(batched, scalar)


def test_kappa_zero_and_one_endpoints() -> None:
    """κ=0 (no meta uplift) and κ=1 (full) must both match scalar exactly."""
    rng = np.random.default_rng(7)
    universe = _make_universe(rng)
    base = _base()
    kappa_arr = np.array([0.0, 1.0, 0.5])
    node_mappings = _random_node_mappings(3, rng)
    node_weight_arrs = stack_node_weight_arrays(node_mappings)
    parts = precompose_parts(universe)
    scalar = np.array(
        [
            reduction_from_composition(
                base, finalize_composition(parts, kappa=float(kappa_arr[i])), node_mappings[i]
            )
            for i in range(3)
        ]
    )
    batched = finalize_reduce_batched(parts, base, kappa_arr, node_weight_arrs)
    np.testing.assert_array_equal(batched, scalar)
