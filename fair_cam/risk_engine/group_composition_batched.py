"""Array-native batched finalize+reduce over the ensemble draw axis (K,).

The weight-robustness ensemble evaluates each coalition's ``v(S)`` for K draws
that differ ONLY in ``(kappa, node_mapping)`` (the coalition set is identical
across draws — ``_shapley_sampled`` uses a fixed seed). The scalar path runs
``finalize_composition(parts, kappa)`` + ``reduction_from_composition(base,
comp, node_mapping)`` once per (coalition x draw) — ~16.8M Python calls on a
14-control aggregate, ~90% of run wall time (all N-independent).

This module computes the SAME reduction for ALL K draws of a fixed coalition in
one pass: every κ-/weight-dependent quantity becomes a ``(K,)`` numpy array, so
the Python object overhead (dict/enum construction) is paid ONCE per coalition
instead of once per (coalition x draw).

It is a faithful array mirror of the *scalar* ``finalize_composition`` (LEC side
only — the meta side is κ-invariant and precomputed in ``ComposedParts``) +
``reduction_from_composition`` + ``_group_comp_to_node_multipliers``. Operation
ORDER is preserved element-for-element, so ``finalize_reduce_batched(...)[k]``
is bit-identical to the scalar composition at draw ``k``. The scalar functions
remain the single source of truth and are UNCHANGED; this path is used ONLY by
the ensemble. Equivalence is pinned by ``test_group_composition_batched.py``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

import numpy as np

from fair_cam.models.composition_topology import (
    _NON_OPEFF_SUB_FUNCTIONS,
    GROUP_MEMBERSHIP,
    GROUP_NODE_MAPPING,
    GROUP_TYPE,
    PAIR_GROUPS,
    PAIR_RECIPES,
    BooleanGroup,
    GroupType,
    NodeMapping,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.control_aware import _NODE_KEYS
from fair_cam.risk_engine.group_composition import _LEC_GROUPS, ComposedParts

__all__ = ["finalize_reduce_batched", "stack_node_weight_arrays"]


# --- array-native mirrors of the fair_cam.composition scalar primitives ------
# Each matches its scalar counterpart element-for-element (same product/sum
# order); operands are lists of (K,) float64 arrays.


def _or_arr(values: Sequence[np.ndarray], k: int) -> np.ndarray:
    """1 - prod(1 - x_i); empty -> zeros. Mirrors composition.or_compose."""
    if not values:
        return np.zeros(k)
    product = np.ones(k)
    for x in values:
        product = product * (1.0 - x)
    return 1.0 - product


def _and_arr(values: Sequence[np.ndarray], k: int) -> np.ndarray:
    """prod(x_i); empty -> ones. Mirrors composition.and_compose."""
    if not values:
        return np.ones(k)
    product = np.ones(k)
    for x in values:
        product = product * x
    return product


def _weak_and_arr(values: Sequence[np.ndarray]) -> np.ndarray | None:
    """Uniform mean of present operands; empty -> None. Mirrors
    composition.weak_and_compose with weights=None (w_i = 1/n)."""
    n = len(values)
    if n == 0:
        return None
    acc = np.zeros(values[0].shape[0])
    w = 1.0 / n
    for x in values:
        acc = acc + w * x
    return acc


def stack_node_weight_arrays(
    node_mappings: Sequence[Mapping[BooleanGroup, NodeMapping]],
) -> dict[BooleanGroup, dict[str, np.ndarray]]:
    """Stack K per-draw node_mappings into ``group -> node -> (K,)`` weight
    arrays (targets are structural/invariant across draws; only weights are
    perturbed). Computed ONCE per ensemble, reused for every coalition."""
    out: dict[BooleanGroup, dict[str, np.ndarray]] = {}
    for group, canon in GROUP_NODE_MAPPING.items():
        if not canon.targets:
            continue
        per_target: dict[str, np.ndarray] = {}
        for target in canon.targets:
            per_target[target] = np.array(
                [nm[group].weights[target] for nm in node_mappings], dtype=np.float64
            )
        out[group] = per_target
    return out


def _batched_lec_group_effectiveness(
    parts: ComposedParts, kappa_arr: np.ndarray
) -> dict[BooleanGroup, np.ndarray | None]:
    """Batched LEC group_effectiveness (K,) arrays, mirroring the LEC branch of
    ``finalize_composition`` -> ``_compose_leaf_and_pair_groups(_LEC_GROUPS)``.

    r_eff uplift ``r0 + (1-r0)*kappa*e_meta`` is applied per LEC opeff/currency
    item; e_meta is the κ-invariant scalar from ``parts``.
    """
    k = kappa_arr.shape[0]
    e_meta = parts.e_meta

    def uplift(r0: float) -> np.ndarray:
        return r0 + (1.0 - r0) * kappa_arr * e_meta

    # Accumulate κ-uplifted LEC opeffs per (group, sub_function), in lec_items
    # order (matches the scalar lec_by_group append order).
    by_group: dict[BooleanGroup, dict[FairCamSubFunction, list[np.ndarray]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for group, sf, part, r0 in parts.lec_items:
        by_group[group][sf].append(part * uplift(r0))

    group_eff: dict[BooleanGroup, np.ndarray | None] = {}

    # Pass 1 — leaf LEC groups (iterate BooleanGroup for stable order; skip pairs).
    for group in BooleanGroup:
        if group not in _LEC_GROUPS or group in PAIR_GROUPS:
            continue
        sub_effs: dict[FairCamSubFunction, np.ndarray] = {
            sf: _or_arr(opeffs, k) for sf, opeffs in by_group.get(group, {}).items()
        }
        members = GROUP_MEMBERSHIP[group]
        gtype = GROUP_TYPE[group]
        opeff_members = [sf for sf in members if sf not in _NON_OPEFF_SUB_FUNCTIONS]
        if not opeff_members:
            group_eff[group] = None
            continue
        if gtype == GroupType.WEAK_AND:
            operands = [sub_effs[sf] for sf in opeff_members if sf in sub_effs]
            group_eff[group] = _weak_and_arr(operands) if operands else None
        elif gtype == GroupType.AND:
            operands = [sub_effs.get(sf, np.zeros(k)) for sf in opeff_members]
            group_eff[group] = _and_arr(operands, k) if operands else None
        else:  # GroupType.OR
            operands = [sub_effs.get(sf, np.zeros(k)) for sf in opeff_members]
            group_eff[group] = _or_arr(operands, k) if operands else None

    # Pass 2 — LEC pair groups (AND of two child leaf outputs; None if either None).
    for pair, (left, right) in PAIR_RECIPES.items():
        if pair not in _LEC_GROUPS:
            continue
        left_eff = group_eff.get(left)
        right_eff = group_eff.get(right)
        group_eff[pair] = (
            _and_arr([left_eff, right_eff], k)
            if left_eff is not None and right_eff is not None
            else None
        )

    return group_eff


def finalize_reduce_batched(
    parts: ComposedParts,
    base: tuple[float, float, float, float, float],
    kappa_arr: np.ndarray,
    node_weight_arrs: Mapping[BooleanGroup, Mapping[str, np.ndarray]],
    *,
    availability_self_detection: bool = False,
) -> np.ndarray:
    """``v(S)`` (subset reduction, dollars) for ALL K draws of one coalition.

    Bit-identical, per element ``k``, to::

        comp = finalize_composition(parts, kappa_arr[k])
        reduction_from_composition(base, comp, node_mapping[k],
                                   availability_self_detection=...)

    ``node_weight_arrs`` is the ``stack_node_weight_arrays`` output for the K
    draws (group -> node -> (K,) weights). ``kappa_arr`` is (K,).
    """
    k = kappa_arr.shape[0]
    group_eff = _batched_lec_group_effectiveness(parts, kappa_arr)

    # currency subtractor total (K,), in currency_items order (matches scalar sum).
    e_meta = parts.e_meta

    def uplift(r0: float) -> np.ndarray:
        return r0 + (1.0 - r0) * kappa_arr * e_meta

    currency_total = np.zeros(k)
    for _group, _sf, _cid, cap_x_cov, r0 in parts.currency_items:
        currency_total = currency_total + cap_x_cov * uplift(r0)

    # node multipliers m[node] = prod_groups (1 - E_g * w_{g,node}); iterate
    # GROUP_NODE_MAPPING order to match the scalar left-to-right product exactly.
    multipliers: dict[str, np.ndarray] = {node: np.ones(k) for node in _NODE_KEYS}
    for group, canon in GROUP_NODE_MAPPING.items():
        if not canon.targets:
            continue
        if group == BooleanGroup.LEC_DETECTION_RESPONSE_PAIR:
            continue
        if group == BooleanGroup.LEC_RESPONSE:
            if availability_self_detection:
                eff = group_eff.get(BooleanGroup.LEC_RESPONSE)
            else:
                eff = group_eff.get(BooleanGroup.LEC_DETECTION_RESPONSE_PAIR)
        else:
            # This batched path only computes LEC group effectivenesses (the meta
            # side is κ-invariant and folded into parts.e_meta), so it relies on
            # meta groups carrying NO node targets — true in the current topology
            # (composition_topology GROUP_NODE_MAPPING: all VMC/DSC targets are
            # empty). If a future topology re-targets a meta group onto a FAIR
            # node, the scalar _group_comp_to_node_multipliers would apply that
            # multiplier while this path would silently drop it — fail loud
            # instead of diverging. (test_batched_matches_scalar_full_universe
            # also guards this, but assert here so a topology change trips at the
            # source, not only under that test.)
            if group not in _LEC_GROUPS:
                raise NotImplementedError(
                    f"batched finalize/reduce reached non-LEC group {group!r} with node "
                    f"targets {canon.targets!r}; meta groups must have empty targets for "
                    "the batched ensemble path (composition_topology invariant changed)."
                )
            eff = group_eff.get(group)
        if eff is None:
            continue
        weights = node_weight_arrs[group]
        for target in canon.targets:
            multipliers[target] = multipliers[target] * (1.0 - eff * weights[target])

    base_tef, base_vuln, base_primary, base_secondary, original_ale = base
    adjusted_secondary = np.maximum(
        0.0, base_secondary * multipliers["secondary_loss"] - currency_total
    )
    adjusted_ale = (
        base_tef
        * multipliers["threat_event_frequency"]
        * base_vuln
        * multipliers["vulnerability"]
        * (base_primary * multipliers["primary_loss"] + adjusted_secondary)
    )
    reduction = original_ale - adjusted_ale
    if np.any(reduction < -1e-9):  # broken 1-E*w invariant -> fail loud (mirrors scalar)
        bad = float(reduction.min())
        raise ValueError(f"negative subset reduction {bad!r}: 1-E*w invariant violated")
    # .clip(min=0.0) == np.maximum(0.0, reduction) elementwise (floor FP noise only),
    # but returns a typed ndarray so mypy's warn_return_any is satisfied.
    clamped: np.ndarray = reduction.clip(min=0.0)
    return clamped
