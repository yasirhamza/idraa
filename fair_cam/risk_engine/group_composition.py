"""Single source of truth for FAIR-CAM Boolean group composition (#130 D2).

Consumed by BOTH the Layer-2 diagnostic (`compose_group_effectiveness`) and the
engine ALE path (`_apply_control_adjustments`) so they cannot drift.

Input contract (NIT NEW-5): `active_controls` is a list of *resolved* `Control`
objects (NOT control-id strings); both engine callers resolve ids to Controls
before calling. Output is a `GroupComposition` describing per-`BooleanGroup`
composed effectiveness, the CURRENCY (Loss-Reduction) subtractor total, the
per-sub-function effectivenesses, and per-group contributing control ids.

Import direction is one-way: `control_aware -> group_composition ->
{composition, models}`. NEVER import `control_aware` from here (the cycle that
plan-gate B-arch-1 broke by relocating `_NON_OPEFF_SUB_FUNCTIONS` and the
pair-group topology into `composition_topology`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from fair_cam.composition import (
    WEAK_AND_OPERATOR_PROVENANCE,
    GroupEffectivenessReport,
    and_compose,
    compute_assignment_part,
    or_compose,
    weak_and_compose,
)
from fair_cam.models.composition_topology import (
    _NON_OPEFF_SUB_FUNCTIONS,
    GROUP_MEMBERSHIP,
    GROUP_NODE_MAPPING,
    GROUP_TYPE,
    KAPPA_META_RELIABILITY,
    PAIR_GROUPS,
    PAIR_RECIPES,
    BooleanGroup,
    GroupType,
    sub_function_to_group,
)
from fair_cam.models.control import Control
from fair_cam.models.sub_function import FairCamSubFunction

_COMPOSE: dict[GroupType, Callable[[Sequence[float]], float | None]] = {
    GroupType.AND: and_compose,
    GroupType.OR: or_compose,
    GroupType.WEAK_AND: weak_and_compose,
}


@dataclass(frozen=True)  # #432: single-construction value object (dict contents
# are still mutable by design — tests seed group_effectiveness in place; frozen
# blocks field REBINDING, which is the aliasing hazard the cache introduced).
class GroupComposition:
    """Per-group composition result shared by diagnostic + engine.

    - `group_effectiveness[g]` is `None` when the group has no opeff operands
      (all members absent/CURRENCY-only), distinguishing "no operands" from
      "0 effectiveness" (spec §3.2.4). For a pair group it is `None` when either
      child leaf eff is `None`.
    - `currency_subtractor_total` accumulates the CURRENCY (Loss-Reduction)
      per-event dollar subtractor across all controls (#130 D3); it is NOT an
      opeff and never enters the weak-AND mean.
    """

    group_effectiveness: dict[BooleanGroup, float | None] = field(default_factory=dict)
    sub_function_effectiveness: dict[BooleanGroup, dict[FairCamSubFunction, float]] = field(
        default_factory=dict
    )
    currency_subtractor_total: float = 0.0
    contributing_control_ids: dict[BooleanGroup, list[str]] = field(default_factory=dict)
    currency_excluded: dict[BooleanGroup, list[tuple[FairCamSubFunction, str]]] = field(
        default_factory=dict
    )
    # Slice 2 (#439): the composed meta strength E_meta that uplifted this
    # result's LEC reliabilities (r_eff = r0 + (1-r0)*kappa*E_meta). Defaulted
    # so existing constructors stay valid; 0.0 == no meta uplift applied.
    meta_strength: float = 0.0


# --------------------------------------------------------------------------- #
# Slice 2 (#439) — two-phase composition: κ-invariant `precompose_parts` +
# cheap per-κ `finalize_composition`, with the meta→reliability coupling.
#
# The VMC/DSC "meta" families no longer carry direct FAIR-node targets (Slice 2
# retired them on §2.2 p.5 "Indirectly Affect Risk" grounds — see
# composition_topology GROUP_NODE_MAPPING). Their composed strength E_meta now
# uplifts the *reliability* of co-present Loss Event Controls:
#
#     r_eff(a) = r0(a) + (1 - r0(a)) * kappa * E_meta
#
# applied to every LEC opeff/currency assignment at finalize time. Because the
# meta composition and the raw LEC opeff parts (X * coverage, reliability-free)
# are κ-invariant, `precompose_parts` computes them ONCE; `finalize_composition`
# applies a specific κ cheaply (the weight-robustness ensemble reuses one
# `ComposedParts` across many κ draws — #419 param key "meta.kappa").
# --------------------------------------------------------------------------- #

_META_GROUPS: frozenset[BooleanGroup] = frozenset(
    {
        BooleanGroup.VMC_VARIANCE_PREVENTION,
        BooleanGroup.VMC_IDENTIFICATION,
        BooleanGroup.VMC_CORRECTION,
        BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR,
        BooleanGroup.DSC_PREVENTION,
        BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR,
    }
)

# The Loss-Event families (everything that is NOT meta): these are the groups
# whose reliabilities the coupling uplifts, composed κ-dependently at finalize.
_LEC_GROUPS: frozenset[BooleanGroup] = frozenset(g for g in BooleanGroup if g not in _META_GROUPS)


@dataclass(frozen=True)
class ComposedParts:
    """κ-invariant intermediate for the two-phase composition (Slice 2 #439).

    Produced once by :func:`precompose_parts`; consumed by
    :func:`finalize_composition` for any number of κ values.

    - ``e_meta`` — the composed meta strength E_meta ∈ [0, 1].
    - ``lec_items`` — one entry per LEC opeff assignment:
      ``(group, sub_function, part, r0)`` where ``part = X * coverage``
      (reliability-free) and ``r0`` is the authored reliability.
    - ``currency_items`` — one entry per LEC CURRENCY assignment:
      ``(group, sub_function, control_id, cap_x_cov, r0)`` where
      ``cap_x_cov = (capability_value or 0.0) * coverage`` (NULL → 0.0 so it
      contributes nothing to the subtractor while still appearing in the
      currency-exclusion bookkeeping).
    - ``meta_group_effectiveness`` / ``meta_sub_function_effectiveness`` — the
      composed meta diagnostics (κ-invariant: meta uses raw r0, no meta-on-meta
      coupling).
    - ``contributing`` — per-group contributing control ids (κ-invariant).

    Caveat (T2-quality-minor): the dataclass is frozen, but
    ``meta_group_effectiveness``, ``meta_sub_function_effectiveness``, and
    ``contributing`` are themselves mutable dict values — freezing the
    dataclass does NOT freeze their contents. Callers that cache a
    ``ComposedParts`` instance (e.g. the eval-time ``_comp_cache``) and call
    :func:`finalize_composition` on it repeatedly across many κ values or
    ensemble draws must never mutate these inner dicts in place; doing so
    would corrupt the cached parts for every subsequent hit.
    """

    e_meta: float
    lec_items: tuple[tuple[BooleanGroup, FairCamSubFunction, float, float], ...]
    currency_items: tuple[tuple[BooleanGroup, FairCamSubFunction, str, float, float], ...]
    meta_group_effectiveness: dict[BooleanGroup, float | None]
    meta_sub_function_effectiveness: dict[BooleanGroup, dict[FairCamSubFunction, float]]
    contributing: dict[BooleanGroup, tuple[str, ...]]


def _none_to_absent(x: float | None) -> list[float]:
    """Treat a ``None`` group effectiveness as an ABSENT OR operand (spec
    §3.2.4): ``or_compose([])`` == 0.0, so absent meta components contribute
    nothing to E_meta rather than being padded with a spurious 0.0."""
    return [] if x is None else [x]


def _best_coherent_subset_mean(values: Sequence[float]) -> float | None:
    """Best-coherent-subset mean: ``max`` over k≥1 of ``mean(top-k of values)``.

    Slice 2 D3-REVISED aggregation for E_dsc_prev ONLY (#453). Sort the present
    member opeffs descending and take the maximum prefix mean. Because a
    descending-sorted sequence's prefix means are non-increasing (each
    newly-added element is ≤ the running mean of the strictly-larger-or-equal
    prefix), this equals ``max(values)`` — but the prefix-mean form is written
    explicitly to document the "strongest coherent subset" semantics the design
    specifies (equivalently: sort descending, take the maximum prefix mean).

    Properties (#453 / spec D3-revised): MONOTONE in adding a member (a new
    member either creates a better prefix or is ignored — E never decreases),
    bounded [0, 1] for inputs in [0, 1], equal to the plain mean for
    homogeneous members (so the existing partial-DSC pin with two equal 0.32
    members is UNCHANGED), and equal to the single member when alone. Empty →
    ``None`` (matches the WEAK_AND empty case, spec §3.2.4).
    """
    ordered = sorted(values, reverse=True)
    if not ordered:
        return None
    best = ordered[0]
    running = 0.0
    for k, v in enumerate(ordered, start=1):
        running += v
        prefix_mean = running / k
        if prefix_mean > best:
            best = prefix_mean
    return best


def _compose_leaf_and_pair_groups(
    by_group: Mapping[BooleanGroup, Mapping[FairCamSubFunction, list[float]]],
    groups: frozenset[BooleanGroup],
) -> tuple[dict[BooleanGroup, float | None], dict[BooleanGroup, dict[FairCamSubFunction, float]]]:
    """Shared pass-1 (leaf) + pass-2 (pair) group composition, restricted to
    ``groups``. Used by BOTH the meta pass (:func:`precompose_parts`) and the
    LEC pass (:func:`finalize_composition`) so operator semantics cannot drift.

    Pass 1 — leaf groups: OR within each sub-function across controls (already
    accumulated in ``by_group``), then the group's ``GROUP_TYPE`` rule across
    its members (CURRENCY excluded). AND/OR groups treat an absent member as 0.0
    (parity with the diagnostic); WEAK_AND averages only the PRESENT opeff
    members (a 0 diminishes but does not inhibit, §3.3). A group with no opeff
    members → ``None`` (spec §3.2.4).

    Pass 2 — pair groups (``PAIR_RECIPES``): AND of two child leaf outputs;
    ``None`` if either child is ``None`` (the D8 gate). NB the VMC id∧corr pair
    is recomputed by the meta caller AFTER its correction-gate override.

    Returns ``(group_effectiveness, sub_function_effectiveness)`` covering every
    group in ``groups`` (full key coverage; ``sub_function_effectiveness`` holds
    leaf groups only, matching the legacy shape).
    """
    group_eff: dict[BooleanGroup, float | None] = {}
    sub_eff: dict[BooleanGroup, dict[FairCamSubFunction, float]] = {}

    # Pass 1 — leaf groups. Iterate BooleanGroup for stable key order; skip pair
    # groups explicitly (they ARE in GROUP_TYPE; do NOT use `not in GROUP_TYPE`
    # to detect them — plan-gate B-M3/spec-B1).
    for group in BooleanGroup:
        if group not in groups or group in PAIR_GROUPS:
            continue
        sub_effs = {sf: or_compose(opeffs) for sf, opeffs in by_group.get(group, {}).items()}
        sub_eff[group] = sub_effs

        members = GROUP_MEMBERSHIP[group]
        gtype = GROUP_TYPE[group]
        opeff_members = [sf for sf in members if sf not in _NON_OPEFF_SUB_FUNCTIONS]

        if not opeff_members:
            group_eff[group] = None
            continue

        if gtype == GroupType.WEAK_AND:
            operands = [sub_effs[sf] for sf in opeff_members if sf in sub_effs]
        else:
            operands = [sub_effs.get(sf, 0.0) for sf in opeff_members]

        group_eff[group] = _COMPOSE[gtype](operands) if operands else None

    # Pass 2 — pair groups.
    for pair, (left, right) in PAIR_RECIPES.items():
        if pair not in groups:
            continue
        left_eff = group_eff.get(left)
        right_eff = group_eff.get(right)
        group_eff[pair] = (
            and_compose([left_eff, right_eff])
            if left_eff is not None and right_eff is not None
            else None
        )

    return group_eff, sub_eff


def precompose_parts(active_controls: list[Control]) -> ComposedParts:
    """Compose the κ-invariant parts of a control set (Slice 2 #439).

    Iterates each control/assignment ONCE, routing by Boolean group:
      - meta groups (``_META_GROUPS``): full opeff = ``part * r0`` (meta uses
        raw authored reliability — no meta-on-meta coupling), bucketed for the
        meta composition. CURRENCY cannot occur here (no meta sub-function is
        CURRENCY) — guarded with an explicit ``RuntimeError`` (fail-loud; never
        a bare ``assert``, which is stripped under ``-O``).
      - LEC CURRENCY (``part is None``): recorded in ``currency_items``.
      - LEC opeff: the reliability-free ``part`` is recorded in ``lec_items``
        for finalize to apply ``r_eff``.

    Then composes the meta side once, applies the VMC_CORRECTION implementation
    gate (spec D3), recomputes the VMC id∧corr pair over the gated value, and
    derives E_meta = OR(E_vmc, E_dsc).
    """
    meta_by_group: dict[BooleanGroup, dict[FairCamSubFunction, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    lec_items: list[tuple[BooleanGroup, FairCamSubFunction, float, float]] = []
    currency_items: list[tuple[BooleanGroup, FairCamSubFunction, str, float, float]] = []
    # Full key coverage (every leaf group + pair) matching the legacy shape.
    contributing: dict[BooleanGroup, set[str]] = {group: set() for group in BooleanGroup}

    for control in active_controls:
        for a in control.assignments:
            group = sub_function_to_group(a.sub_function)
            part = compute_assignment_part(a)
            contributing[group].add(control.control_id)

            if group in _META_GROUPS:
                if part is None:
                    raise RuntimeError(
                        f"CURRENCY sub-function {a.sub_function.value!r} routed to meta group "
                        f"{group.value!r}; no meta sub-function is CURRENCY (Slice 2 #439). "
                        "This indicates a topology/units corruption."
                    )
                # Meta opeff uses the raw authored reliability r0 (no coupling).
                meta_by_group[group][a.sub_function].append(part * a.reliability)
                continue

            if part is None:
                # LEC CURRENCY (Loss Reduction) — subtractor, not an opeff
                # (#130 D3). NULL capability contributes nothing (cap_x_cov=0.0)
                # but is still recorded for currency-exclusion bookkeeping.
                cap_x_cov = (a.capability_value or 0.0) * a.coverage
                currency_items.append(
                    (group, a.sub_function, control.control_id, cap_x_cov, a.reliability)
                )
                continue

            lec_items.append((group, a.sub_function, part, a.reliability))

    # Compose the meta side ONCE (κ-invariant).
    meta_group_eff, meta_sub_eff = _compose_leaf_and_pair_groups(meta_by_group, _META_GROUPS)

    # Spec D3 deviation (plan-gate Meth-B2): implementation is the load-bearing
    # correction operand (§4.3.2 "the final step"). Absent implementation ->
    # no correction, regardless of treatment-selection presence; present ->
    # AND over the PRESENT members (impl alone = its opeff; impl+selection =
    # the §4.3 prescribed product). Replaces only the 0.0-PADDING of the
    # absent-selection case; GROUP_TYPE stays the prescribed AND. The else 0.0
    # also covers the no-members-at-all case — matching the legacy padded-AND
    # value for LEC-only sets, keeping the LEC-only bit-identity pin exact.
    corr_effs = meta_sub_eff.get(BooleanGroup.VMC_CORRECTION, {})
    meta_group_eff[BooleanGroup.VMC_CORRECTION] = (
        and_compose(list(corr_effs.values()))
        if FairCamSubFunction.VMC_CORR_IMPLEMENTATION in corr_effs
        else 0.0
    )
    # Recompute the id∧corr pair AFTER the gate so the AND consumes the gated
    # correction value (spec D3).
    id_eff = meta_group_eff.get(BooleanGroup.VMC_IDENTIFICATION)
    corr_eff = meta_group_eff.get(BooleanGroup.VMC_CORRECTION)
    meta_group_eff[BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR] = (
        and_compose([id_eff, corr_eff]) if id_eff is not None and corr_eff is not None else None
    )

    # Slice 2 D3-REVISED deviation (#453): DSC_PREVENTION aggregates its present
    # member opeffs with the BEST-COHERENT-SUBSET MEAN, NOT the plain WEAK_AND
    # mean-of-present that the generic leaf pass produced above.
    #
    # This is a labeled documented DEVIATION from BOTH (a) the nine FAIR-CAM
    # §5.1.x pp.36-45 Boolean-AND prescriptions (the original Slice 2 D3
    # deviation) AND (b) plain mean-of-present. Rationale: mean-of-present is
    # NON-MONOTONE in coalition membership — a control contributing
    # below-average DSC members LOWERS the mean, so v(S∪{c}) < v(S) and exact
    # Shapley goes NEGATIVE (observed on prod run ce3d0294: THM −$1,611.67,
    # SAT −$0.57 — #453). The best-coherent-subset mean (max over k of the
    # top-k mean) is the MONOTONE ENVELOPE of mean-of-present: adding a member
    # can only raise the max or leave it unchanged. Semantics: decision-support
    # quality = the strongest coherent subset of present functions; a weaker
    # extra function neither helps nor dilutes. It still OVERSTATES E_dsc for
    # sparse authoring (accepted, bounded by kappa*(1−r0)).
    #
    # GROUP_TYPE[DSC_PREVENTION] stays WEAK_AND (the operator family label);
    # THIS override IS the D3-revised aggregation. Mirrors the VMC_CORRECTION
    # implementation-gate override pattern above — an explicit override written
    # into meta_group_eff after the generic leaf composition (engine ≡
    # diagnostic parity). LEC_RESPONSE's weak-AND is deliberately NOT touched:
    # its diminution is Standard-prescribed (§3.3.1) and a deficient response
    # member SHOULD diminish — that non-monotonicity is FAIR-CAM-faithful and
    # out of scope. The empty case (no present DSC members) stays None (the
    # helper returns None on empty), preserving the generic WEAK_AND empty case.
    dsc_prev_effs = meta_sub_eff.get(BooleanGroup.DSC_PREVENTION, {})
    meta_group_eff[BooleanGroup.DSC_PREVENTION] = _best_coherent_subset_mean(
        list(dsc_prev_effs.values())
    )

    # E_meta = OR(E_vmc, E_dsc); each family ORs its Variance-Prevention /
    # Prevention leaf with its Identification∧Correction pair (§2.2 p.5, §4 p.21;
    # DSC contribution is a labeled v3 proxy — §2.2 p.5 / §5 p.30).
    e_vmc = or_compose(
        _none_to_absent(meta_group_eff.get(BooleanGroup.VMC_VARIANCE_PREVENTION))
        + _none_to_absent(meta_group_eff.get(BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR))
    )
    e_dsc = or_compose(
        _none_to_absent(meta_group_eff.get(BooleanGroup.DSC_PREVENTION))
        + _none_to_absent(meta_group_eff.get(BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR))
    )
    # Guard each component BEFORE the OR-fusion (T2-Meth-N3): OR-fusion can
    # saturate to 1.0 and mask an out-of-range component (e.g. e_vmc=1.2,
    # e_dsc=1.0 -> or_compose([1.2, 1.0]) == 1.0 — saturation masking requires
    # the OTHER operand to be exactly 1.0, hiding the corrupt operand behind a
    # superficially in-range result). Checking e_meta alone is not sufficient.
    if not (0.0 <= e_vmc <= 1.0):
        raise ValueError(f"e_vmc out of [0,1]: {e_vmc!r} — an opeff operand exceeded 1")
    if not (0.0 <= e_dsc <= 1.0):
        raise ValueError(f"e_dsc out of [0,1]: {e_dsc!r} — an opeff operand exceeded 1")
    e_meta = or_compose([e_vmc, e_dsc])
    if not (0.0 <= e_meta <= 1.0):
        raise ValueError(f"E_meta out of [0,1]: {e_meta!r} — an opeff operand exceeded 1")

    # Pair contributing = union of children (κ-invariant), matching legacy.
    for pair, (left, right) in PAIR_RECIPES.items():
        contributing[pair] = contributing[left] | contributing[right]

    contributing_out = {group: tuple(sorted(ids)) for group, ids in contributing.items()}

    return ComposedParts(
        e_meta=e_meta,
        lec_items=tuple(lec_items),
        currency_items=tuple(currency_items),
        meta_group_effectiveness=meta_group_eff,
        meta_sub_function_effectiveness=meta_sub_eff,
        contributing=contributing_out,
    )


def finalize_composition(
    parts: ComposedParts, kappa: float = KAPPA_META_RELIABILITY
) -> GroupComposition:
    """Apply a specific κ to κ-invariant ``parts`` (Slice 2 #439).

    ``r_eff = r0 + (1 - r0) * kappa * parts.e_meta`` uplifts every LEC opeff /
    currency assignment's reliability, then the SAME pass-1/pass-2 group
    composition runs over the LEC groups. With ``kappa == 0.0`` or
    ``e_meta == 0.0`` the uplift returns ``r0`` EXACTLY (``r0 + (1-r0)*k*0.0``
    is exact in IEEE-754), so LEC group values / currency subtractor are
    bit-identical to the legacy left-associative arithmetic.

    Copy semantics: FRESH dicts are built every call and the cached ``parts``
    meta dicts are SHALLOW-COPIED into the result, so no per-draw
    ``GroupComposition`` aliases the cached parts' inner dicts — a mutating
    consumer cannot contaminate another κ draw. Full key coverage (every leaf
    group + pair) is preserved, matching the legacy diagnostic shape.
    """
    if not (0.0 <= kappa <= 1.0):
        raise ValueError(f"kappa out of [0,1]: {kappa!r}")

    e_meta = parts.e_meta

    def uplift(r0: float) -> float:
        return r0 + (1.0 - r0) * kappa * e_meta

    # Rebuild the LEC opeff buckets with the κ-uplifted reliability. Preserve
    # the legacy left-associative grouping: `part` is `X * coverage`, then
    # `part * uplift(r0)` — bit-identical to `(X*coverage)*r0` when uplift==r0.
    lec_by_group: dict[BooleanGroup, dict[FairCamSubFunction, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for group, sf, part, r0 in parts.lec_items:
        lec_by_group[group][sf].append(part * uplift(r0))

    lec_group_eff, lec_sub_eff = _compose_leaf_and_pair_groups(lec_by_group, _LEC_GROUPS)

    # start=0.0 (T2-Meth-N2): sum() over an empty generator defaults to int 0,
    # so an empty currency_items would otherwise leave currency_total as int 0
    # rather than the declared float 0.0.
    currency_total = sum(
        (cap_x_cov * uplift(r0) for _, _, _, cap_x_cov, r0 in parts.currency_items), 0.0
    )

    # Merge LEC (κ-dependent) + meta (κ-invariant) into fresh dicts.
    group_effectiveness: dict[BooleanGroup, float | None] = dict(lec_group_eff)
    group_effectiveness.update(parts.meta_group_effectiveness)

    sub_function_effectiveness: dict[BooleanGroup, dict[FairCamSubFunction, float]] = dict(
        lec_sub_eff
    )
    for group, sub_effs in parts.meta_sub_function_effectiveness.items():
        # Shallow-copy the cached inner dict so no draw aliases the parts cache.
        sub_function_effectiveness[group] = dict(sub_effs)

    contributing_control_ids = {group: list(ids) for group, ids in parts.contributing.items()}

    # Rebuild currency_excluded from currency_items with full leaf-group key
    # coverage (matches the legacy shape: every non-pair group present).
    currency_excluded: dict[BooleanGroup, list[tuple[FairCamSubFunction, str]]] = {
        group: [] for group in BooleanGroup if group not in PAIR_GROUPS
    }
    for group, sf, control_id, _cap_x_cov, _r0 in parts.currency_items:
        currency_excluded[group].append((sf, control_id))

    return GroupComposition(
        group_effectiveness=group_effectiveness,
        sub_function_effectiveness=sub_function_effectiveness,
        currency_subtractor_total=currency_total,
        contributing_control_ids=contributing_control_ids,
        currency_excluded=currency_excluded,
        meta_strength=e_meta,
    )


def compose_groups(
    active_controls: list[Control], *, kappa: float = KAPPA_META_RELIABILITY
) -> GroupComposition:
    """Compose all active controls into per-`BooleanGroup` effectiveness.

    Two-phase (Slice 2 #439): ``finalize_composition(precompose_parts(...),
    kappa)``. The meta (VMC/DSC) families no longer carry direct FAIR-node
    targets (retired on §2.2 p.5 "Indirectly Affect Risk" grounds); their
    composed strength E_meta instead uplifts co-present Loss Event Control
    reliabilities via ``r_eff = r0 + (1-r0)*kappa*E_meta`` (§2.2 p.5, §2.3
    pp.5-6, §4 p.21; the DSC contribution is a labeled v3 proxy — §2.2 p.5 /
    §5 p.30). ``kappa`` defaults to the canonical
    :data:`KAPPA_META_RELIABILITY`.

    Composition passes (see :func:`_compose_leaf_and_pair_groups`):
      1. Leaf groups: OR within each sub-function across controls, then the
         group's ``GROUP_TYPE`` rule across its members (CURRENCY excluded).
         AND/OR groups treat an absent member as 0.0; WEAK_AND averages only
         the present opeff members (a 0 diminishes but does not inhibit, §3.3).
      2. Pair groups: AND of two child leaf-group outputs per ``PAIR_RECIPES``;
         ``None`` if either child is ``None``.
    """
    return finalize_composition(precompose_parts(active_controls), kappa)


# --------------------------------------------------------------------------- #
# Composition provenance (#130 Task 8 / D6 / spec §9)
# --------------------------------------------------------------------------- #
#
# Structured, CODE-CONSTANT §/page provenance for each composition rule, emitted
# into the run results payload so the separate "FAIR-grounding UX" spec renders
# citations WITHOUT re-deriving them. SAFE-RENDERING INVARIANT (plan-gate
# I-sec-4, spec §9; extended Slice 2 Task 5, #439): every field below is
# sourced ONLY from `GROUP_TYPE`, `GROUP_NODE_MAPPING`,
# `WEAK_AND_OPERATOR_PROVENANCE`, `KAPPA_META_RELIABILITY`,
# `_DSC_WEAK_AND_DEVIATION_NOTE`, `_RELIABILITY_COUPLING_PROVENANCE`, and
# `_VMC_IDENTIFICATION_RULE_PROVENANCE` (all module-level code constants
# below) — NEVER from control/scenario/org free-text. These are stable
# identifiers, not user display labels. If a
# future field is ever sourced from user data it MUST be XML-escaped before
# reaching reportlab `Paragraph` (the reportlab path is not autoescaped,
# unlike the Jinja web path — a documented repo XSS channel).
#
# ALLOWLIST (plan-gate N-sec; N-M8 RESOLVED — Slice 2 Task 5, #439): provenance
# is emitted for the LEC + VMC + DSC families the engine actually exercises.
# The DSC groups were previously excluded pending an unverified §5.1 DSC
# citation spot-check (N-M8). Slice 2 retired DSC's direct Loss-Magnitude
# target and routes DSC through the SAME kappa reliability coupling as VMC
# (see `GROUP_NODE_MAPPING[DSC_*].citation`, Task 1's NodeMapping literals),
# explicitly labeled a v3 PROXY rather than the retracted §5.1 claim. N-M8 is
# resolved by removing the overclaim (honest PROXY label), not by verifying
# the original §5.1 citation — the citation strings emitted below are the
# Task-1 NodeMapping literals, unchanged here.
_PROVENANCE_GROUPS: tuple[BooleanGroup, ...] = (
    BooleanGroup.LEC_PREVENTION,
    BooleanGroup.LEC_DETECTION,
    BooleanGroup.LEC_RESPONSE,
    BooleanGroup.LEC_DETECTION_RESPONSE_PAIR,
    BooleanGroup.VMC_VARIANCE_PREVENTION,
    BooleanGroup.VMC_IDENTIFICATION,
    BooleanGroup.VMC_CORRECTION,
    BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR,
    BooleanGroup.DSC_PREVENTION,
    BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR,
)

# DSC_PREVENTION is GROUP_TYPE.WEAK_AND (Slice 2 D3-revised deviation from the
# nine §5.1.x pp.36-45 Boolean-AND prescriptions). Its actual aggregation is the
# best-coherent-subset mean (max_k mean(top-k present member opeffs) — #453), a
# MONOTONE envelope of mean-of-present that avoids the negative-Shapley bug the
# plain mean caused; bounded by kappa*(1-r0). It shares the WEAK_AND operator
# FAMILY label with `WEAK_AND_OPERATOR_PROVENANCE` but NOT the equal-weighted
# mean `formula` (overridden per-entry below), and the shared constant's
# `rule_citation` (§3.3.1-3.3.3 pp.18-20) is LEC_RESPONSE's rule citation, not
# DSC's — mutating the shared constant would mislabel LEC_RESPONSE. These
# DSC-only fields document the deviation instead.
_DSC_WEAK_AND_DEVIATION_NOTE = (
    "DSC_PREVENTION's aggregation is a Slice 2 D3-REVISED (#453) documented "
    "DEVIATION from BOTH the nine §5.1.x pp.36-45 Boolean-AND prescriptions "
    "AND plain mean-of-present: E_dsc_prev = max_k mean(top-k present member "
    "opeffs), the best-coherent-subset mean (the MONOTONE ENVELOPE of "
    "mean-of-present). Plain mean-of-present was NON-MONOTONE in coalition "
    "membership — a below-average member LOWERED the mean, driving v(S∪{c}) < "
    "v(S) and NEGATIVE meta Shapley for weak contributors (#453); the "
    "best-coherent-subset mean makes adding a member never decrease E. The "
    "whole DSC channel is a labeled v3 proxy; the shared "
    "WEAK_AND_OPERATOR_PROVENANCE.rule_citation (§3.3.1-3.3.3 pp.18-20) "
    "describes LEC_RESPONSE's weak-AND, not this one. "
    "mean-of-present overstates E_dsc for sparse authoring (the "
    "best-coherent-subset mean, being its upper envelope, overstates at least "
    "as much — accepted, bounded by kappa*(1-r0)). "
    "F453-Meth-1: equivalently, the maximum present member opeff — breadth of "
    "present functions does not increase E."
)

# Slice 2 (#439) meta -> reliability coupling provenance entry. Structure-vs-
# routing distinction (plan-gate Meth-I3): the pair ANDs (VMC id/corr, DSC
# id/corr) are Standard-prescribed Boolean structures; DSC_PREVENTION's
# WEAK_AND and VMC_CORRECTION's implementation-gate handling are documented
# v3 deviations from the prescribed structure; E_meta's OR-fusion across
# families is v3 arithmetic with no Standard-prescribed cross-family operator.
# Separately, DSC's ROUTING into r_eff (as opposed to its group STRUCTURE) is
# a v3 PROXY — the Standard frames DSC as affecting decisions (§2.2 p.5, §5
# p.30), not control reliability directly. `dsc_note` documents that split so
# the emitted provenance never conflates "structure" with "routing" honesty.
_RELIABILITY_COUPLING_PROVENANCE: dict[str, str] = {
    "group": "reliability_coupling",
    "rule": "interpolation",
    "formula": "r_eff = r0 + (1 - r0) * kappa * E_meta",
    "kappa": str(KAPPA_META_RELIABILITY),
    "citation": (
        "FAIR-CAM §2.1 p.4 (controls improving other controls' performance), "
        "§2.2 p.5 (reliability), §2.3 pp.5-6, §4 p.21 "
        '("Operational Performance of other controls")'
    ),
    "structure_provenance": (
        "pair ANDs Standard-prescribed (§4 p.21; §5 p.30); DSC_PREVENTION "
        "WEAK_AND and VMC_CORRECTION implementation-gate are documented "
        "deviations; the per-family fusions E_vmc = OR(VMC_VARIANCE_PREVENTION, "
        "VMC_IDENTIFICATION_CORRECTION_PAIR) and E_dsc = OR(DSC_PREVENTION, "
        "DSC_IDENTIFICATION_CORRECTION_PAIR), and the top-level E_meta = "
        "OR(E_vmc, E_dsc) fusion, are ALL v3 arithmetic with no "
        "Standard-prescribed cross-family operator"
    ),
    "dsc_note": (
        "DSC ROUTING into r_eff is a v3 PROXY (Standard: DSC affects "
        "decisions, §2.2 p.5, §5 p.30); the DSC pair AND and functional "
        "decomposition are Standard-prescribed (§5 p.30), while "
        "DSC_PREVENTION's weak-AND operator is a documented deviation (see "
        "structure_provenance)"
    ),
    "weights_provenance": "implementation-calibration",
}

# Final-Meth-1 (#439/#451 final-gate): VMC_IDENTIFICATION's emitted entry pairs
# `rule="or"` with `citation` = GROUP_NODE_MAPPING[VMC_IDENTIFICATION].citation,
# which is a §4 p.21 NODE-TARGET citation ("no standalone node") — it does NOT
# trace the OR *operator choice* itself to the Standard. The OR is a Slice 2 D3
# v3 arithmetic choice: §4.2 p.25 has Threat Intelligence and Controls
# Monitoring identifying DIFFERENT variance sources (threat-landscape vs the
# controls themselves), so their coverage is approximated as a union (OR) —
# no intra-pair operator is Standard-prescribed. Mirrors the
# `_DSC_WEAK_AND_DEVIATION_NOTE` per-entry-override pattern above (a dedicated
# field rather than mutating any shared constant).
_VMC_IDENTIFICATION_RULE_PROVENANCE = (
    "v3 arithmetic choice — coverage-union over different variance sources "
    "(§4.2 p.25); no intra-pair operator prescribed"
)


def build_composition_provenance() -> list[dict[str, str]]:
    """Return the code-constant composition provenance for LEC + VMC + DSC.

    Each per-group entry: ``{group, rule, citation, weights_provenance}`` (+ the
    weak-AND operator ``formula`` / ``formula_provenance`` for ``weak_and``
    groups — with ``DSC_PREVENTION``'s ``formula`` overridden to the
    best-coherent-subset mean (#453), + a DSC-specific deviation note for
    ``DSC_PREVENTION``, + a
    ``rule_provenance`` override for ``VMC_IDENTIFICATION`` clarifying its OR
    is a v3 arithmetic choice rather than Standard-traced — Final-Meth-1,
    #439). The result also appends one ``reliability_coupling`` entry
    describing the kappa meta->reliability interpolation (Slice 2, #439). The
    whole result is invariant — it depends only on the static topology tables
    and `KAPPA_META_RELIABILITY`, never on any control/scenario/org input
    (spec §9 safe-render). DSC groups were previously excluded (N-M8); see
    `_PROVENANCE_GROUPS` for the resolution.
    """
    entries: list[dict[str, str]] = []
    for group in _PROVENANCE_GROUPS:
        mapping = GROUP_NODE_MAPPING[group]
        entry: dict[str, str] = {
            "group": group.value,
            "rule": GROUP_TYPE[group].value,
            "citation": mapping.citation,
            "weights_provenance": mapping.weights_provenance,
        }
        if GROUP_TYPE[group] == GroupType.WEAK_AND:
            # Honest-labeling (D5): the rule is Standard-cited; the operator
            # FORMULA (equal-weighted arithmetic mean) is implementation-defined.
            entry["rule_citation"] = WEAK_AND_OPERATOR_PROVENANCE["rule_citation"]
            entry["formula"] = WEAK_AND_OPERATOR_PROVENANCE["formula"]
            entry["formula_provenance"] = WEAK_AND_OPERATOR_PROVENANCE["formula_provenance"]
            if group == BooleanGroup.DSC_PREVENTION:
                # T5-Meth-I1: the shared WEAK_AND_OPERATOR_PROVENANCE
                # rule_citation (§3.3.1-3.3.3 pp.18-20) is LEC_RESPONSE's
                # citation, not DSC's — override the per-entry copy here
                # rather than mutating the shared constant.
                entry["rule_citation"] = (
                    "documented deviation from the nine FAIR-CAM §5.1.x "
                    "pp.36-45 Boolean-AND prescriptions; see "
                    "weak_and_deviation_note"
                )
                # #453: DSC no longer uses the shared equal-weighted-mean
                # formula — override it with the best-coherent-subset mean the
                # engine actually applies (monotone envelope of mean-of-present).
                # F453-Meth-1: disclose the max() equivalence inline so a reader
                # of the emitted provenance sees it without following the
                # deviation note or the helper's docstring.
                entry["formula"] = (
                    "E = max_k mean(top-k present member opeffs) — "
                    "best-coherent-subset mean (monotone envelope of "
                    "mean-of-present; #453); equivalently: the maximum present "
                    "member opeff — breadth of present functions does not "
                    "increase E"
                )
                entry["weak_and_deviation_note"] = _DSC_WEAK_AND_DEVIATION_NOTE
                # F453-Meth-2: the shared WEAK_AND_OPERATOR_PROVENANCE
                # formula_provenance cites the equal-weighted-mean property
                # proofs in tests/test_composition_operators.py, which do NOT
                # test this operator — override with the actual pin location.
                entry["formula_provenance"] = (
                    "implementation-defined (#453); properties (monotone in "
                    "membership, bounded [0,1], idempotent on homogeneous "
                    "inputs, empty -> None) pinned in "
                    "fair_cam/tests/risk_engine/test_meta_reliability_coupling.py"
                )
        if group == BooleanGroup.VMC_IDENTIFICATION:
            # Final-Meth-1: the `citation` above is the NodeMapping's
            # node-target citation, not an operator-choice citation — without
            # this override, `rule="or"` beside a §4 p.21 citation implies the
            # TI∨monitoring OR is Standard-traced. It is a v3 arithmetic
            # choice (see `_VMC_IDENTIFICATION_RULE_PROVENANCE` above).
            entry["rule_provenance"] = _VMC_IDENTIFICATION_RULE_PROVENANCE
        entries.append(entry)
    entries.append(dict(_RELIABILITY_COUPLING_PROVENANCE))
    return entries


def build_group_effectiveness_reports(
    active_controls: Sequence[Control],
) -> dict[BooleanGroup, GroupEffectivenessReport]:
    """Layer-2 Boolean-group composition diagnostic (spec §5.2), as per-group
    ``GroupEffectivenessReport``s.

    #328: extracted verbatim from the retired
    ``ControlAwareRiskCalculator.compose_group_effectiveness`` (whose only
    remaining callers were tests). The method was already purely
    *presentational* post-#130-Task-3: it delegates composition to the single
    shared ``compose_groups`` (the same routine the engine ALE path consumes,
    so diagnostic and engine cannot drift — D2) and re-shapes the resulting
    ``GroupComposition`` into the diagnostic's stable per-group report
    contract. Leaf groups re-derive the currency-exclusion count + sub-function
    list from the routine's ``currency_excluded`` tuples; pair groups report
    the AND-pair effectiveness from Pass-2 plus the UNION of their children's
    currency-exclusion bookkeeping (pair groups have no sub-functions of their
    own -> empty ``sub_function_effectivenesses``).
    """
    comp = compose_groups(list(active_controls))

    reports: dict[BooleanGroup, GroupEffectivenessReport] = {}

    for group in BooleanGroup:
        if group in PAIR_RECIPES:
            continue
        excluded = comp.currency_excluded.get(group, [])
        excluded_sfs = sorted({sf for sf, _ in excluded}, key=lambda x: x.value)
        reports[group] = GroupEffectivenessReport(
            group_id=group,
            group_type=GROUP_TYPE[group],
            sub_function_effectivenesses=comp.sub_function_effectiveness.get(group, {}),
            group_effectiveness=comp.group_effectiveness.get(group),
            contributing_control_ids=comp.contributing_control_ids.get(group, []),
            non_opeff_excluded_count=len(excluded),
            non_opeff_excluded_sub_functions=excluded_sfs,
        )

    for pair_group, (left, right) in PAIR_RECIPES.items():
        excluded_count = (
            reports[left].non_opeff_excluded_count + reports[right].non_opeff_excluded_count
        )
        excluded_sfs_pair = sorted(
            set(reports[left].non_opeff_excluded_sub_functions)
            | set(reports[right].non_opeff_excluded_sub_functions),
            key=lambda x: x.value,
        )
        reports[pair_group] = GroupEffectivenessReport(
            group_id=pair_group,
            group_type=GROUP_TYPE[pair_group],
            sub_function_effectivenesses={},
            group_effectiveness=comp.group_effectiveness.get(pair_group),
            contributing_control_ids=comp.contributing_control_ids.get(pair_group, []),
            non_opeff_excluded_count=excluded_count,
            non_opeff_excluded_sub_functions=excluded_sfs_pair,
        )

    return reports
