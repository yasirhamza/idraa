"""Composition layer helpers - Layer 1 (intra-assignment) and Layer 2
(Boolean composition).

Spec: docs/superpowers/specs/2026-05-02-pr-kappa-fair-cam-reshape-and-composition-design.md
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from .calibration.elapsed_time_taus import get_canonical_tau
from .models.composition_topology import BooleanGroup, GroupType
from .models.control import FairCamControlFunctionAssignment
from .models.sub_function import (
    DERIVABLE_SOURCES,
    SUB_FUNCTION_UNITS,
    FairCamSubFunction,
    UnitType,
)
from .normalization import elapsed_time_to_opeff


def capability_opeff(assignment: FairCamControlFunctionAssignment) -> float | None:
    """Unit-converted capability in [0, 1], before coverage and reliability.

    None for a NULL capability (the caller applies the 0.5 anchor). CURRENCY has no
    opeff and raises — callers short-circuit it first.

    Unit dispatch (issue #268; the single source of capability normalization —
    :func:`compute_assignment_part` is built on it with the BASE operation order,
    so every pre-existing value is bit-identical):
      - PROBABILITY / PERCENT_REDUCTION: the capability itself.
      - ELAPSED_TIME: exp(-t/τ) with τ = ``get_canonical_tau(sub_function)``.
    """
    unit = SUB_FUNCTION_UNITS[assignment.sub_function]
    if unit == UnitType.CURRENCY:
        raise ValueError(f"{assignment.sub_function.value} is CURRENCY; it has no opeff")
    if assignment.capability_value is None:
        return None
    if unit == UnitType.ELAPSED_TIME:
        tau = get_canonical_tau(assignment.sub_function)
        return elapsed_time_to_opeff(assignment.capability_value, tau)
    return assignment.capability_value


def compute_assignment_part(
    assignment: FairCamControlFunctionAssignment,
) -> float | None:
    """Unit-dispatched opeff WITHOUT the reliability factor (Slice 2 #439).

    Returns None for CURRENCY (subtractor, not an opeff). The reliability
    factor is applied by the caller — either the authored r0 (legacy
    behaviour) or the coupled r_eff = r0 + (1-r0)*kappa*E_meta.
    Invariant: compute_assignment_opeff_two_branch(a) ==
    compute_assignment_part(a) * a.reliability (or both None).

    Unit dispatch (the single source of truth for opeff normalization; the
    two-branch wrapper below delegates here so unit handling cannot drift —
    PR μ.1 plan-gate Arch-B3):
      - PROBABILITY / PERCENT_REDUCTION: capability * coverage (spec §3.1
        Layer-1 Option-A multiplicative, sans reliability). NULL
        capability_value → 0.5 * coverage (issue #131 Arch3-N1 NULL guard:
        six previously-ELAPSED_TIME sub-functions were reclassified to this
        branch and may legitimately store a NULL capability_value).
      - ELAPSED_TIME: exp(-t/τ) * coverage. NULL capability_value →
        0.5 * coverage (algebraic identity: elapsed_time_to_opeff(τ·ln(2), τ)
        = exp(-ln(2)) = 0.5, so the prior τ·ln(2) fallback collapsed to a
        constant).
      - CURRENCY: None — CURRENCY is a per-event dollar subtractor
        (LEC_RESP_LOSS_REDUCTION), not an effectiveness probability (#130 D3);
        it is accumulated separately.
    """
    unit = SUB_FUNCTION_UNITS[assignment.sub_function]
    if unit == UnitType.CURRENCY:
        return None
    # Unit conversion lives in capability_opeff (#268); the BASE operation order
    # (converted capability × coverage, left-associative) is preserved here so
    # every pre-existing value is bit-identical.
    x = capability_opeff(assignment)
    if x is None:
        return 0.5 * assignment.coverage
    return x * assignment.coverage


def compute_assignment_opeff_two_branch(
    assignment: FairCamControlFunctionAssignment,
) -> float | None:
    """Compute per-assignment OpEff under PR μ.1 two-branch math.

    Slice 2 (#439): now delegates to :func:`compute_assignment_part` — the
    single source of unit dispatch — and multiplies in the authored
    reliability (r0). Returns:
        - PROBABILITY / PERCENT_REDUCTION: (capability * coverage) * reliability
          (spec §3.1 Layer-1 multiplicative; the former standalone
          ``compute_assignment_opeff`` folded into this invariant); NULL
          capability_value → (0.5 * coverage) * reliability (issue #131
          Arch3-N1).
        - ELAPSED_TIME: exp(-t/τ) * coverage * reliability; NULL → the same
          0.5-anchor fallback (algebraic identity: elapsed_time_to_opeff(
          τ·ln(2), τ) = 0.5).
        - CURRENCY: None — CURRENCY does NOT have an opeff; handled separately
          via the loss_reduction_per_event / currency-subtractor accumulator.

    Invariant: ``compute_assignment_opeff_two_branch(a) ==
    compute_assignment_part(a) * a.reliability`` (or both None). Bit-identity
    holds because ``part`` is the left-associative ``X * coverage`` product and
    this multiplies by ``reliability`` last — matching the legacy
    ``X * coverage * reliability`` grouping exactly.

    Callers must handle the None case explicitly (skip CURRENCY assignments
    or accumulate them via a different code path).

    Shared by:
      - fair_cam.controls.effectiveness.calculate_control_risk_adjustment
      - fair_cam.models.control.Control.calculate_risk_reduction_factor
      - fair_cam.risk_engine.group_composition composition (Layer-2 + engine)
    """
    part = compute_assignment_part(assignment)
    if part is None:
        return None
    return part * assignment.reliability


def derive_virtual_assignment(
    source: FairCamControlFunctionAssignment,
    sub_function: FairCamSubFunction = FairCamSubFunction.DSC_CORR_MISALIGNED,
) -> FairCamControlFunctionAssignment:
    """FAIR-CAM §5.3 p.50: Correcting Misaligned Decisions is fulfilled by controls
    within other functions. Returns a PROBABILITY-unit assignment that carries the
    source's converted capability, coverage and AUTHORED reliability r0, so its
    opeff equals the source's bit-for-bit (register C12).

    Provenance (register C12): dependency PRESCRIBED (§5.3 p.50); numeric
    realisation IMPLEMENTATION-DEFINED; a lec_resp_resilience source is a labelled
    PROXY. measured_at / confirmed_by_user_at are not copied: no composition path
    reads them.

    Identity (pinned exact ``==`` in tests/test_derive_virtual_assignment.py):
    ``compute_assignment_opeff_two_branch(derived) ==
    compute_assignment_opeff_two_branch(source)`` for every source in
    ``DERIVABLE_SOURCES``, including the NULL-capability 0.5 anchor — a NULL
    capability is carried as NULL (never pre-resolved to 0.5) so the consumer
    applies the very same anchor the source gets.

    Raises ``ValueError`` for a target other than ``DSC_CORR_MISALIGNED`` or a
    source outside ``DERIVABLE_SOURCES`` (incl. CURRENCY, which has no opeff).
    """
    if sub_function is not FairCamSubFunction.DSC_CORR_MISALIGNED:
        raise ValueError(f"{sub_function.value} is not a derivable virtual sub-function")
    if source.sub_function not in DERIVABLE_SOURCES:
        raise ValueError(f"{source.sub_function.value} cannot fulfil {sub_function.value}")
    return FairCamControlFunctionAssignment(
        sub_function=sub_function,
        capability_value=capability_opeff(source),
        coverage=source.coverage,
        reliability=source.reliability,
        degradation_rate=source.degradation_rate,
    )


def resolve_derived_assignments(
    rows: Sequence[tuple[FairCamControlFunctionAssignment, FairCamSubFunction | None]],
    *,
    control_ref: str,
) -> list[FairCamControlFunctionAssignment]:
    """Resolve the Correcting-Misaligned-Decisions links of ONE control (issue #268).

    Each item is ``(mapped assignment, derived_from sub-function or None)``. A
    non-derived row passes through untouched (same object); a derived row — the
    link names the sub-function of this control's own correction/response row —
    is replaced by ``derive_virtual_assignment(<that non-derived row>)``. Order is
    preserved. The single resolver shared by the v3 run mapper (ORM rows) and the
    verification workbook (V3 snapshots), so the two cannot disagree.

    Raises ``ValueError`` naming ``control_ref`` and sub-function slugs only (no
    stored value, no user text) when: a link sits on a row other than
    ``dsc_corr_misaligned``; ``dsc_corr_misaligned`` has no link; the link names a
    sub-function that is itself a derived row, absent from this control's
    non-derived rows, or not in ``DERIVABLE_SOURCES``.
    """
    virtual = FairCamSubFunction.DSC_CORR_MISALIGNED
    derived_sfs = {a.sub_function for a, link in rows if link is not None}
    plain = {a.sub_function: a for a, link in rows if link is None}  # uq: one per sub-function
    resolved: list[FairCamControlFunctionAssignment] = []
    for assignment, link in rows:
        if link is None:
            if assignment.sub_function is virtual:
                raise ValueError(f"control {control_ref}: {virtual.value} has no source link")
            resolved.append(assignment)
            continue
        if assignment.sub_function is not virtual:
            raise ValueError(
                f"control {control_ref}: {assignment.sub_function.value} cannot carry a "
                f"derived link (only {virtual.value} is derived)"
            )
        if link in derived_sfs:
            raise ValueError(
                f"control {control_ref}: {virtual.value} link names {link.value}, "
                f"which is itself a derived row"
            )
        source = plain.get(link)
        if source is None:
            raise ValueError(
                f"control {control_ref}: {virtual.value} link names {link.value}, "
                f"which this control does not carry"
            )
        if link not in DERIVABLE_SOURCES:
            raise ValueError(f"control {control_ref}: {link.value} cannot fulfil {virtual.value}")
        resolved.append(derive_virtual_assignment(source, virtual))
    return resolved


def or_compose(values: Sequence[float]) -> float:
    """OR-style composition: 1 - product(1 - x_i).

    P(at-least-one-succeeds) under independence assumption. Used for:
    - Within-sub-function across controls (multiple controls covering same sub-function)
    - LEC Prevention OR-trio (Avoidance + Deterrence + Resistance) — PRESCRIBED
      (§3.1 p.9)
    - VMC Variance Prevention OR-pair — PRESCRIBED (§4.1.1 p.23 / §4.1.2 p.24)
    - VMC Identification OR-pair (Threat Intelligence ∨ Controls Monitoring;
      Slice 2 D3 / #439 — v3 arithmetic choice: §4.2 p.25 has the two members
      identifying DIFFERENT variance sources, complementary coverage
      approximated by OR; no intra-pair operator is Standard-prescribed. See
      `models/composition_topology.py` VMC_IDENTIFICATION comment.)
    - Per-control Layer-2 squash (across the control's own assignments)
    - E_vmc = OR(VMC Variance Prevention, VMC Identification∧Correction pair)
      and E_dsc = OR(DSC Prevention, DSC Identification∧Correction pair), and
      the top-level E_meta = OR(E_vmc, E_dsc) — all v3 arithmetic fusions with
      no Standard-prescribed cross-family operator (`risk_engine/
      group_composition.py:precompose_parts`)

    Empty list returns 0.0 (no contribution).
    """
    if len(values) == 0:
        return 0.0
    product = 1.0
    for x in values:
        product *= 1.0 - x
    return 1.0 - product


def and_compose(values: Sequence[float]) -> float:
    """AND-product composition: product(x_i).

    Multiplicative AND is monotonic in every input (key property for an
    investment-decision tool - see spec Q4). Used for:
    - LEC Detection AND-trio
    - LEC Detection-Response AND-pair (composes group outputs)
    - VMC Correction AND-group — the AND (Treatment Selection ∧ Implementation,
      §4.3.1/§4.3.2 p.28) is the RETAINED TOPOLOGY LABEL only: the generic leaf
      pass still computes the padded AND here, and `precompose_parts`
      (`group_composition.py`) OVERRIDES it with the implementation-anchored
      value (E_corr = Implementation when present, else 0; Treatment Selection
      label-only — register C8, #199 slice 1)
    - VMC Identification-Correction AND-pair — PRESCRIBED (§4 p.21)
    - DSC Identification-Correction AND-pair — PRESCRIBED (§5 p.30)

    Slice 2 (#439) topology note: VMC Identification moved to `or_compose`
    (D3 v3 arithmetic choice) and DSC Prevention moved to
    `weak_and_compose` (D3 documented deviation from the prescribed §5.1.x
    Boolean-AND) — neither is AND-composed any more. See
    `models/composition_topology.py` GROUP_TYPE for the current topology
    (source of truth).

    Empty list returns 1.0 (multiplicative identity - caller interprets as
    "no constraint applied").
    """
    if len(values) == 0:
        return 1.0
    product = 1.0
    for x in values:
        product *= x
    return product


# Provenance for the weak-AND operator (#130 D5 / spec §6, §7.1).
# Honest-labeling (no-overclaim rule): the *rule* (weak-AND) is FAIR-CAM
# Standard-cited; the *operator formula* (equal-weighted arithmetic mean) is
# implementation-defined — the Standard gives a semantic, not a formula. The
# five §6 properties are property-proven in tests/test_composition_operators.py.
# Nesting-invariance is out of scope: the Boolean topology is flat-per-group
# (one weak-AND per group across its sub-functions), so weak-ANDs never nest
# (plan-gate I-M6). This constant is the source of truth the engine/diagnostic
# emit into composition provenance metadata (Task 8); keep it code-constant.
WEAK_AND_OPERATOR_PROVENANCE = {
    "rule": "weak_and",
    "rule_citation": "FAIR-CAM Standard V1.0 §3.3.1-3.3.3, pp.18-20",
    "formula": "weighted arithmetic mean (equal weights)",
    "formula_provenance": (
        "implementation-defined; the Standard gives a semantic, not a formula. "
        "Property-proven (bounded, non-inhibition, monotonic, unanimity/idempotence, "
        "not-weak-OR) in tests/test_composition_operators.py. NOT Standard-grounded."
    ),
}


def weak_and_compose(
    values: Sequence[float],
    weights: Sequence[float] | None = None,
) -> float | None:
    """Weighted-arithmetic-mean weak-AND: sum(w_i * x_i) with default w_i = 1/n.

    Standard §3.3 weak-AND semantics: a 0 in one element does NOT zero the
    group ("deficiencies diminish but don't necessarily inhibit"). Used for:
    - LEC Response weak-AND-trio (Event Termination + Resilience + Loss
      Reduction) — PRESCRIBED (§3.3.1-3.3.3 pp.18-20)
    - DSC Prevention weak-AND-group (9 sub-functions, §5.1.x pp.36-45; Slice 2
      D3 / #439 documented DEVIATION from the Standard's nine prescribed
      Boolean-AND clauses — mean-of-present avoids zeroing on sparse
      authoring, bounded by kappa*(1-r0). See `models/composition_topology.py`
      DSC_PREVENTION comment and `risk_engine/group_composition.py`
      `_DSC_WEAK_AND_DEVIATION_NOTE`.)

    Empty list returns None (per spec §3.2.4 - distinguishes 'all operands
    time-unit excluded' from '0 effectiveness').

    Default weights are equal (1/n). PR mu may calibrate per-element weights
    if backtest data justifies; PR kappa ships with equal weights.

    Raises ValueError if weights length does not match values length, or
    weights do not sum to 1.0.
    """
    if len(values) == 0:
        return None
    n = len(values)
    if weights is None:
        weights = [1.0 / n] * n
    if len(weights) != n:
        raise ValueError(f"weights length ({len(weights)}) must match values length ({n})")
    if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
        raise ValueError(f"weights must sum to 1.0; got {sum(weights)}")
    return sum(w * x for w, x in zip(weights, values, strict=True))


@dataclass
class GroupEffectivenessReport:
    """Diagnostic Layer 2 output per Boolean group. PR kappa exposes this in run
    JSON / audit log / run-detail templates; PR mu wires it into the engine path.

    `group_effectiveness` is None when ALL operands were excluded
    (CURRENCY-only sub-functions, e.g. LEC Response's `lec_resp_loss_reduction`
    — see `_NON_OPEFF_SUB_FUNCTIONS`). Distinguishes 'no opeff operands'
    from '0 effectiveness'.
    """

    group_id: BooleanGroup
    group_type: GroupType
    sub_function_effectivenesses: dict[FairCamSubFunction, float] = field(default_factory=dict)
    group_effectiveness: float | None = None
    contributing_control_ids: list[str] = field(default_factory=list)
    # NOTE (#146): populated from the CURRENCY-item bookkeeping; accurate while
    # _NON_OPEFF_SUB_FUNCTIONS == {LEC_RESP_LOSS_REDUCTION} (true today). If a
    # non-CURRENCY sub-function ever joins that frozenset, extend the producer
    # (build_group_effectiveness_reports) or this under-counts.
    non_opeff_excluded_count: int = 0
    non_opeff_excluded_sub_functions: list[FairCamSubFunction] = field(default_factory=list)
