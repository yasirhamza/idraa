"""Slice 2 (#439) — meta -> reliability coupling on Boolean-group composition.

The VMC/DSC "meta" families no longer carry direct FAIR-node targets (Slice 2
retired them, §2.2 p.5 "Indirectly Affect Risk"); their composed strength E_meta
now uplifts the reliability of co-present Loss Event Controls via

    r_eff = r0 + (1 - r0) * kappa * E_meta

applied to every LEC opeff/currency assignment inside ``finalize_composition``.
``precompose_parts`` computes the kappa-invariant parts (E_meta, the raw LEC
opeff parts, the meta diagnostics); ``finalize_composition`` applies a specific
kappa; ``compose_groups`` = the two composed.

Helper construction mirrors ``test_effect_type_aware_gate.py`` style but builds
``Control`` directly so a test can set per-assignment (capability, coverage,
reliability) tuples (the coupling operates on r0, which ``make_control`` fixes
to a single value).
"""

from __future__ import annotations

import math

import pytest

from fair_cam.composition import WEAK_AND_OPERATOR_PROVENANCE
from fair_cam.models.composition_topology import BooleanGroup
from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.control_aware import (
    _group_comp_to_node_multipliers,
)
from fair_cam.risk_engine.group_composition import (
    _best_coherent_subset_mean,
    build_composition_provenance,
    build_group_effectiveness_reports,
    compose_groups,
    finalize_composition,
    precompose_parts,
)


def _ctrl(cid: str, assigns: list[tuple[str, float | None, float, float]]) -> Control:
    """(sub_function_value, capability, coverage, reliability) -> Control."""
    return Control(
        control_id=cid,
        name=cid,
        # Domain is irrelevant to composition (routing is by sub-function via
        # sub_function_to_group, not by ControlDomain); LOSS_EVENT is a valid
        # placeholder. (The brief drafted ``ControlDomain.TECHNICAL``, but
        # TECHNICAL is a ControlType, not a ControlDomain.)
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


def test_lec_only_composition_bit_identical_any_kappa() -> None:
    """No meta assignments -> E_meta = 0 -> r_eff = r0 EXACTLY, kappa inert.

    EXACT equality on purpose (==, never approx): this is the byte-identity
    pin. Scope (spec drift log / plan-gate Spec-I1): LEC group values, node
    multipliers, and currency subtractor are bit-identical to the legacy
    left-associative arithmetic ((cap*cov)*rel); the META diagnostic entries
    intentionally change under D3 (LEC-only DSC_PREVENTION was 0.0 via
    AND-padding, is now None via the WEAK_AND empty case) and are pinned to
    the NEW values here.
    """
    ctrl = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    a = compose_groups([ctrl], kappa=0.0)
    b = compose_groups([ctrl], kappa=1.0)
    assert a.group_effectiveness == b.group_effectiveness
    assert a.meta_strength == 0.0
    # bit-exact legacy value: (0.9 * 0.8) * 0.7 — same grouping as the old
    # cap*cov*rel left-associative product.
    assert a.group_effectiveness[BooleanGroup.LEC_PREVENTION] == (0.9 * 0.8) * 0.7
    # D3 diagnostic deltas for LEC-only sets, pinned to the NEW semantics:
    assert a.group_effectiveness[BooleanGroup.DSC_PREVENTION] is None  # was 0.0
    assert a.group_effectiveness[BooleanGroup.VMC_CORRECTION] == 0.0  # unchanged
    assert a.group_effectiveness[BooleanGroup.VMC_IDENTIFICATION] == 0.0  # OR over padded zeros


def test_meta_uplifts_co_present_lec_effectiveness() -> None:
    """dsc_prev_communication (NULL cap: opeff 0.5*0.8*0.8=0.32) uplifts a
    co-present LEC control: r_eff = 0.7 + 0.3*0.5*0.32 = 0.748."""
    meta = _ctrl("sat", [("dsc_prev_communication", None, 0.8, 0.8)])
    lec = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    comp = compose_groups([meta, lec], kappa=0.5)
    assert comp.meta_strength == pytest.approx(0.32)
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(
        0.9 * 0.8 * (0.7 + 0.3 * 0.5 * 0.32)
    )


def test_meta_alone_produces_no_node_effect() -> None:
    """Meta-only subset: LEC groups keep their EXISTING no-present-operand
    semantics (AND/OR pad absent members with 0.0 -> 0.0; do NOT change the
    None-vs-0.0 diagnostic distinction, spec §3.2.4 / plan-gate Arch-B2);
    direct meta targets retired -> node multipliers identity -> v(S)=0."""
    meta = _ctrl("sat", [("dsc_prev_communication", None, 0.8, 0.8)])
    comp = compose_groups([meta], kappa=0.5)
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == 0.0
    mults = _group_comp_to_node_multipliers(comp)
    assert all(v == 1.0 for v in mults.values())


def test_partial_dsc_group_no_longer_zeroes() -> None:
    """Two of nine dsc_prev_* members present -> DSC aggregation of the two.

    Both members are HOMOGENEOUS (0.32 each), so the #453 best-coherent-subset
    mean equals the plain mean-of-present here (max prefix mean of {0.32, 0.32}
    is 0.32) — this pin is UNCHANGED by #453 (the homogeneous-equals-plain-mean
    property is exactly why the fix is backward-compatible on equal members).
    """
    c = _ctrl(
        "crqm",
        [
            ("dsc_prev_sa_reporting", None, 0.8, 0.8),  # 0.32
            ("dsc_prev_sa_analysis", None, 0.8, 0.8),  # 0.32
        ],
    )
    comp = compose_groups([c])
    assert comp.group_effectiveness[BooleanGroup.DSC_PREVENTION] == pytest.approx(0.32)
    assert comp.meta_strength == pytest.approx(0.32)


# --------------------------------------------------------------------------- #
# #453 — E_dsc_prev best-coherent-subset mean (monotone envelope of
# mean-of-present). Kills the negative-meta-Shapley bug on prod run ce3d0294.
# --------------------------------------------------------------------------- #


def _dsc(cid: str, sf: str, cov: float, rel: float) -> Control:
    """A control carrying ONE dsc_prev_* member with NULL capability, so its
    opeff is exactly 0.5*cov*rel (the same NULL-cap convention the existing
    dsc_prev_communication pins rely on)."""
    return _ctrl(cid, [(sf, None, cov, rel)])


def test_e_dsc_prev_best_coherent_subset_mean_ignores_weaker_added_member() -> None:
    """#453 monotonicity pin: adding a BELOW-average DSC member must NOT lower
    E_dsc_prev (the exact non-monotonicity that produced negative Shapley).

    Control A -> dsc member {0.32} (0.5*0.8*0.8). Adding control B -> member
    {0.245} (0.5*0.7*0.7). Plain mean-of-present would give (0.32+0.245)/2 =
    0.2825 < 0.32 (the bug). Best-coherent-subset mean = max prefix mean of
    {0.32, 0.245} = 0.32 — the weaker member is ignored, E does NOT decrease.
    """
    a = _dsc("thm", "dsc_prev_sa_reporting", 0.8, 0.8)  # 0.32
    b = _dsc("sat", "dsc_prev_sa_analysis", 0.7, 0.7)  # 0.245
    e_a = compose_groups([a]).group_effectiveness[BooleanGroup.DSC_PREVENTION]
    e_ab = compose_groups([a, b]).group_effectiveness[BooleanGroup.DSC_PREVENTION]
    assert e_a == pytest.approx(0.32)
    assert e_ab == pytest.approx(0.32)  # NOT 0.2825 — the #453 fix
    assert e_ab >= e_a


def test_e_dsc_prev_best_coherent_subset_mean_takes_stronger_added_member() -> None:
    """Adding a STRONGER member raises E to that member's value (its top-1
    prefix mean beats the two-member average).

    A -> {0.32}; adding C -> member {0.5} (0.5*1.0*1.0). Best prefix mean of
    {0.5, 0.32} = max(0.5, 0.41) = 0.5 (NOT the plain mean 0.41)."""
    a = _dsc("thm", "dsc_prev_sa_reporting", 0.8, 0.8)  # 0.32
    c = _dsc("iam", "dsc_prev_incentives", 1.0, 1.0)  # 0.5
    e_ac = compose_groups([a, c]).group_effectiveness[BooleanGroup.DSC_PREVENTION]
    assert e_ac == pytest.approx(0.5)


def test_best_coherent_subset_mean_unit_properties() -> None:
    """Direct unit pins on the pure helper: single member, homogeneous, empty,
    and the two worked examples from the design brief.

    F453-Meth-2: this test + ``test_best_coherent_subset_mean_is_monotone_exhaustive``
    below are the pin location cited by ``build_composition_provenance``'s
    DSC_PREVENTION ``formula_provenance`` override for ALL four named
    properties: monotone in membership (exhaustive test below), bounded [0,1]
    (asserted below and in the exhaustive test), idempotent on homogeneous
    inputs (the ``[0.32, 0.32]`` pin below), and empty -> None (the ``[]`` pin
    below).
    """
    assert _best_coherent_subset_mean([]) is None  # empty -> None
    result_alone = _best_coherent_subset_mean([0.32])
    assert result_alone == pytest.approx(0.32)  # alone
    assert result_alone is not None and 0.0 <= result_alone <= 1.0  # bounded [0,1]
    result_homogeneous = _best_coherent_subset_mean([0.32, 0.32])
    assert result_homogeneous == pytest.approx(0.32)  # idempotent on homogeneous inputs
    assert result_homogeneous is not None and 0.0 <= result_homogeneous <= 1.0
    assert _best_coherent_subset_mean([0.32, 0.245]) == pytest.approx(0.32)  # weaker ignored
    assert _best_coherent_subset_mean([0.5, 0.32]) == pytest.approx(0.5)  # stronger wins


def test_best_coherent_subset_mean_is_monotone_exhaustive() -> None:
    """Exhaustive small-case property: for a few fixed member-sets S and every
    candidate x, E(S ∪ {x}) >= E(S) — the monotonicity #453 requires. Also pins
    the bounded-[0,1] property (F453-Meth-2) across every S and S∪{x} composed
    here, since all inputs/candidates are themselves in [0,1].

    (Membership is by VALUE here — the helper operates on the composed present
    member opeffs, so a repeated value models a same-strength extra member.)
    """
    base_sets = [
        [],
        [0.32],
        [0.5, 0.32],
        [0.245, 0.6, 0.1],
        [0.9, 0.9, 0.9],
    ]
    candidates = [0.0, 0.1, 0.245, 0.32, 0.5, 0.6, 0.9, 1.0]
    for s in base_sets:
        e_s = _best_coherent_subset_mean(s)
        e_s_val = 0.0 if e_s is None else e_s
        assert e_s is None or 0.0 <= e_s <= 1.0  # bounded [0,1]
        for x in candidates:
            e_sx = _best_coherent_subset_mean([*s, x])
            assert e_sx is not None
            assert 0.0 <= e_sx <= 1.0  # bounded [0,1]
            assert e_sx + 1e-12 >= e_s_val, f"monotonicity broken: S={s} x={x}"


def test_monitoring_only_vmc_contributes_zero() -> None:
    """vmc_id_control_monitoring without any correction partner: the
    PRESCRIBED find-AND-fix pair (§4 p.21) -> E_vmc = 0."""
    c = _ctrl("siem", [("vmc_id_control_monitoring", None, 0.8, 0.8)])
    comp = compose_groups([c])
    assert comp.meta_strength == pytest.approx(0.0)


def test_vmc_pair_completes_across_controls() -> None:
    """Monitoring on one control + implementation on another completes the
    id-AND-corr pair: E_pair = 0.32 * 0.32 (implementation-alone passes the
    correction gate)."""
    mon = _ctrl("siem", [("vmc_id_control_monitoring", None, 0.8, 0.8)])
    impl = _ctrl("pmgt", [("vmc_corr_implementation", None, 0.8, 0.8)])
    comp = compose_groups([mon, impl])
    assert comp.meta_strength == pytest.approx(0.32 * 0.32)


def test_correction_gate_selection_alone_is_zero() -> None:
    """Treatment-selection WITHOUT implementation corrects nothing (spec D3
    deviation design: implementation is the load-bearing operand, §4.3.2
    "the final step"). Selecting fixes without implementing them must not
    count as correction — the plan-gate Meth-B2 case."""
    mon = _ctrl("siem", [("vmc_id_control_monitoring", None, 0.8, 0.8)])
    sel = _ctrl("crqm", [("vmc_corr_treatment_selection", None, 0.8, 0.8)])
    comp = compose_groups([mon, sel])
    assert comp.group_effectiveness[BooleanGroup.VMC_CORRECTION] == 0.0
    assert comp.meta_strength == pytest.approx(0.0)


def test_correction_gate_both_members_use_prescribed_product() -> None:
    """Implementation + selection both present -> the Standard's AND product
    (§4.3.1/§4.3.2 p.28): E_corr = 0.32 * 0.32."""
    impl = _ctrl("pmgt", [("vmc_corr_implementation", None, 0.8, 0.8)])
    sel = _ctrl("crqm", [("vmc_corr_treatment_selection", None, 0.8, 0.8)])
    comp = compose_groups([impl, sel])
    assert comp.group_effectiveness[BooleanGroup.VMC_CORRECTION] == pytest.approx(0.32 * 0.32)


def test_elapsed_time_lec_assignment_gets_uplift() -> None:
    """D2: the coupling applies to ELAPSED_TIME units too — opeff(tau) * cov
    * r_eff. lec_det_monitoring carries tau=194 (elapsed_time_taus.py); with
    capability_value=194.0, opeff = exp(-1). E_meta=1 fixture -> r_eff =
    0.8 + 0.2*0.5 = 0.9."""
    meta = _ctrl("sat", [("dsc_prev_communication", 1.0, 1.0, 1.0)])  # E_meta = 1.0
    det = _ctrl("siem2", [("lec_det_monitoring", 194.0, 1.0, 0.8)])
    comp = compose_groups([meta, det], kappa=0.5)
    expected = (math.exp(-1.0) * 1.0) * 0.9
    assert comp.sub_function_effectiveness[BooleanGroup.LEC_DETECTION][
        FairCamSubFunction.LEC_DET_MONITORING
    ] == pytest.approx(expected)


def test_r_eff_bounds_and_monotonicity() -> None:
    """r_eff in [r0, 1]; monotone in kappa."""
    meta = _ctrl("sat", [("dsc_prev_communication", 1.0, 1.0, 1.0)])  # E_meta = 1.0
    lec = _ctrl("mfa", [("lec_prev_resistance", 1.0, 1.0, 0.5)])
    low = compose_groups([meta, lec], kappa=0.0)
    mid = compose_groups([meta, lec], kappa=0.5)
    hi = compose_groups([meta, lec], kappa=1.0)
    e = lambda c: c.group_effectiveness[BooleanGroup.LEC_PREVENTION]  # noqa: E731
    assert e(low) == pytest.approx(0.5)  # r_eff = r0
    assert e(mid) == pytest.approx(0.75)  # r_eff = 0.5 + 0.5*0.5*1.0
    assert e(hi) == pytest.approx(1.0)  # r_eff capped by construction
    assert e(low) < e(mid) < e(hi)


def test_currency_subtractor_uses_r_eff() -> None:
    """CURRENCY subtractor scales with the uplifted reliability."""
    meta = _ctrl("sat", [("dsc_prev_communication", 1.0, 1.0, 1.0)])  # E_meta = 1
    dre = _ctrl("dre", [("lec_resp_loss_reduction", 100_000.0, 1.0, 0.8)])
    comp = compose_groups([meta, dre], kappa=0.5)
    # r_eff = 0.8 + 0.2*0.5*1.0 = 0.9
    assert comp.currency_subtractor_total == pytest.approx(90_000.0)


def test_diagnostic_matches_engine_composition_meta_plus_lec() -> None:
    """Spec D5 (seventh site) — Layer-2 diagnostic ≡ engine composition.

    ``build_group_effectiveness_reports`` (the Layer-2
    diagnostic) must report the SAME per-group effectiveness as the shared
    ``compose_groups`` at default κ, INCLUDING the meta→reliability coupling:
    the diagnostic delegates to the same routine, so a meta+LEC control set
    (where E_meta uplifts the co-present LEC reliability) must yield identical
    group values in both surfaces (engine≡diagnostic charter, D2/D5).
    """
    meta = _ctrl("sat", [("dsc_prev_communication", None, 0.8, 0.8)])
    lec = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    controls = [meta, lec]

    comp = compose_groups(controls)  # default κ
    reports = build_group_effectiveness_reports(controls)

    for group in BooleanGroup:
        assert reports[group].group_effectiveness == comp.group_effectiveness[group], group
    # The coupling actually fired (E_meta > 0) so this is a live parity check.
    assert comp.meta_strength == pytest.approx(0.32)
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(
        0.9 * 0.8 * (0.7 + 0.3 * 0.5 * 0.32)
    )


def test_finalize_matches_compose_and_parts_are_kappa_invariant() -> None:
    """precompose_parts + finalize == compose_groups; parts reusable across kappas."""
    meta = _ctrl("sat", [("dsc_prev_communication", None, 0.8, 0.8)])
    lec = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    parts = precompose_parts([meta, lec])
    for k in (0.0, 0.3, 0.5, 1.0):
        via_parts = finalize_composition(parts, kappa=k)
        direct = compose_groups([meta, lec], kappa=k)
        assert via_parts.group_effectiveness == direct.group_effectiveness
        assert via_parts.currency_subtractor_total == direct.currency_subtractor_total


def test_finalize_composition_rejects_kappa_out_of_range() -> None:
    """T2-Meth-I1: kappa must be in [0,1] — r_eff = r0 + (1-r0)*kappa*E_meta
    only stays bounded to [r0, 1] for kappa in [0,1]; kappa=2.0 would push
    effectiveness above 1 (physically meaningless opeff)."""
    ctrl = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    parts = precompose_parts([ctrl])
    with pytest.raises(ValueError, match=r"kappa out of \[0,1\]"):
        finalize_composition(parts, kappa=2.0)


def test_compose_groups_rejects_kappa_out_of_range() -> None:
    """T2-Meth-I1: the compose_groups(kappa=...) convenience entry point must
    reject out-of-range kappa too (negative side)."""
    ctrl = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    with pytest.raises(ValueError, match=r"kappa out of \[0,1\]"):
        compose_groups([ctrl], kappa=-0.1)


def test_null_capability_lec_assignment_gets_uplift() -> None:
    """T2-Meth-N1: NULL-capability LEC assignment under ACTIVE meta uplift.

    meta control (dsc_prev_communication, cap 1.0, cov 1.0, rel 1.0) ->
    E_meta = 1.0 (opeff = 1.0*1.0*1.0 = 1.0, OR-composed alone -> 1.0).
    LEC control (lec_prev_resistance, cap None, cov 0.8, rel 0.7): part =
    0.5 * 0.8 = 0.4 (NULL-capability fallback, compute_assignment_part).
    kappa=0.5 -> r_eff = 0.7 + 0.3*0.5*1.0 = 0.85.
    Expected LEC_PREVENTION sub-function opeff = (0.5*0.8)*0.85 = 0.34.
    """
    meta = _ctrl("sat", [("dsc_prev_communication", 1.0, 1.0, 1.0)])
    lec = _ctrl("mfa", [("lec_prev_resistance", None, 0.8, 0.7)])
    comp = compose_groups([meta, lec], kappa=0.5)
    assert comp.meta_strength == pytest.approx(1.0)
    assert comp.sub_function_effectiveness[BooleanGroup.LEC_PREVENTION][
        FairCamSubFunction.LEC_PREV_RESISTANCE
    ] == pytest.approx(0.34)


def test_currency_subtractor_total_is_float_zero_when_no_currency_items() -> None:
    """T2-Meth-N2: currency_subtractor_total must be a float 0.0 (not int 0)
    when there are no CURRENCY assignments — a LEC-opeff-only control set."""
    ctrl = _ctrl("mfa", [("lec_prev_resistance", 0.9, 0.8, 0.7)])
    comp = compose_groups([ctrl])
    assert comp.currency_subtractor_total == 0.0
    assert isinstance(comp.currency_subtractor_total, float)


# --------------------------------------------------------------------------- #
# Task 5 — provenance: reliability-coupling entry + DSC groups un-excluded
# --------------------------------------------------------------------------- #


def test_provenance_emits_coupling_and_dsc_groups() -> None:
    """N-M8 resolution: DSC groups are no longer excluded from provenance
    (they ship the Task-1 PROXY-labeled citation strings instead of the
    retracted unverified §5.1 claim), and a `reliability_coupling` entry
    documents the kappa meta->reliability interpolation with the
    structure-vs-routing distinction (plan-gate Meth-I3).

    T5 methodology re-review (#439) tightened three string fields — the
    assertions below pin the POST-fix strings intentionally, per that
    review's rationale:

    - T5-Meth-I1: `dsc_prevention["rule_citation"]` no longer borrows the
      shared LEC_RESPONSE-specific §3.3.1-3.3.3 citation; it is overridden
      per-entry to point at the DSC deviation note instead.
    - T5-Meth-I2: `reliability_coupling["dsc_note"]` no longer claims the
      DSC group STRUCTURES are wholesale Standard-prescribed (overclaim —
      DSC_PREVENTION's weak-AND is a documented deviation); it now
      distinguishes the Standard-prescribed pair-AND/decomposition from the
      deviating weak-AND operator.
    """
    entries = build_composition_provenance()
    by_group = {e["group"]: e for e in entries}

    assert "dsc_prevention" in by_group
    assert "dsc_identification_correction_pair" in by_group
    assert "PROXY" in by_group["dsc_prevention"]["citation"] or (
        "proxy" in by_group["dsc_prevention"]["citation"].lower()
    )
    assert "PROXY" in by_group["dsc_identification_correction_pair"]["citation"] or (
        "proxy" in by_group["dsc_identification_correction_pair"]["citation"].lower()
    )

    # DSC_PREVENTION keeps the weak_and FAMILY label (rule) but its `formula`
    # is overridden (#453) to the best-coherent-subset mean the engine actually
    # applies — NOT the shared equal-weighted mean (which stays LEC_RESPONSE's).
    # It also carries a DSC-specific deviation note (the shared rule_citation is
    # LEC_RESPONSE-specific and must not be read as DSC's).
    dsc_prev = by_group["dsc_prevention"]
    assert dsc_prev["rule"] == "weak_and"
    # #453: DSC-specific formula override (monotone best-coherent-subset mean),
    # replacing the shared "weighted arithmetic mean (equal weights)".
    # F453-Meth-1 (methodology re-review): the emitted formula must disclose
    # the max() equivalence explicitly — "best-coherent-subset mean" alone
    # invites a reader to assume breadth of present functions helps E, when in
    # fact the composed value equals the single strongest present member.
    assert dsc_prev["formula"] == (
        "E = max_k mean(top-k present member opeffs) — best-coherent-subset "
        "mean (monotone envelope of mean-of-present; #453); equivalently: the "
        "maximum present member opeff — breadth of present functions does "
        "not increase E"
    )
    assert dsc_prev["formula"] != WEAK_AND_OPERATOR_PROVENANCE["formula"]
    assert "weak_and_deviation_note" in dsc_prev
    assert "DSC_PREVENTION" in dsc_prev["weak_and_deviation_note"]
    # T5-Meth-N2: the deviation note also owns its bias, not just its benefit.
    assert (
        "mean-of-present overstates E_dsc for sparse authoring"
        in (dsc_prev["weak_and_deviation_note"])
    )
    # F453-Meth-1: the deviation note also discloses the max() equivalence.
    assert (
        "the maximum present member opeff — breadth of present functions "
        "does not increase E" in dsc_prev["weak_and_deviation_note"]
    )
    # F453-Meth-2: formula_provenance is overridden away from the shared
    # WEAK_AND_OPERATOR_PROVENANCE (which cites property proofs in
    # tests/test_composition_operators.py that do NOT test this operator) to
    # point at the actual pin location for this operator's properties.
    assert dsc_prev["formula_provenance"] != WEAK_AND_OPERATOR_PROVENANCE["formula_provenance"]
    assert dsc_prev["formula_provenance"] == (
        "implementation-defined (#453); properties (monotone in membership, "
        "bounded [0,1], idempotent on homogeneous inputs, empty -> None) "
        "pinned in fair_cam/tests/risk_engine/test_meta_reliability_coupling.py"
    )

    # T5-Meth-I1: rule_citation is DSC-specific (overridden per-entry), never
    # the shared LEC_RESPONSE §3.3.1-3.3.3 citation string.
    assert dsc_prev["rule_citation"] == (
        "documented deviation from the nine FAIR-CAM §5.1.x pp.36-45 "
        "Boolean-AND prescriptions; see weak_and_deviation_note"
    )
    assert "§3.3" not in dsc_prev["rule_citation"]
    # The shared constant itself must be untouched by the per-entry override.
    assert WEAK_AND_OPERATOR_PROVENANCE["rule_citation"] != dsc_prev["rule_citation"]
    assert "§3.3.1-3.3.3" in WEAK_AND_OPERATOR_PROVENANCE["rule_citation"]

    # DSC_IDENTIFICATION_CORRECTION_PAIR is a Standard-prescribed AND (§5 p.30)
    # — no weak-AND fields, no deviation note.
    dsc_pair = by_group["dsc_identification_correction_pair"]
    assert dsc_pair["rule"] == "and"
    assert "weak_and_deviation_note" not in dsc_pair

    rc = by_group["reliability_coupling"]
    assert rc["formula"] == "r_eff = r0 + (1 - r0) * kappa * E_meta"
    assert rc["kappa"] == "0.5"
    assert rc["weights_provenance"] == "implementation-calibration"
    assert "structure_provenance" in rc
    assert "dsc_note" in rc
    assert "PROXY" in rc["dsc_note"]
    # T5-Meth-I2: dsc_note distinguishes Standard-prescribed structure from
    # the deviating weak-AND operator instead of overclaiming both.
    assert "documented deviation" in rc["dsc_note"]
    assert "structure_provenance" in rc["dsc_note"]
    assert "DSC pair AND and functional decomposition are Standard-prescribed" in rc["dsc_note"]
    # T5-Meth-N1: the §4 p.21 gloss quotes the Standard's actual wording
    # ("Operational Performance of other controls"), not a paraphrase
    # ("reliability") that belongs to §2.2 p.5 instead.
    assert '"Operational Performance of other controls"' in rc["citation"]
    assert "§2.2 p.5 (reliability)" in rc["citation"]
    assert all(isinstance(v, str) for v in rc.values())

    # Every entry stays list[dict[str, str]] end to end.
    assert all(isinstance(v, str) for e in entries for v in e.values())


def test_provenance_or_fusion_and_vmc_identification_labeled_v3_arithmetic() -> None:
    """Final-Meth-1/Final-Meth-2 (#439/#451 final-gate) honest-labeling pins.

    (a) vmc_identification's entry pairs rule="or" with a §4 p.21 NODE-TARGET
    citation; without a per-entry override that implies the TI∨monitoring OR is
    Standard-traced. It is a v3 coverage-union choice (Slice 2 D3) — the entry
    must carry a `rule_provenance` field saying so (mirrors the dsc_prevention
    rule_citation override pattern; pins the POST-fix string intentionally).

    (b) reliability_coupling's structure_provenance previously named only the
    top-level E_meta OR-fusion as v3 arithmetic; the per-family E_vmc/E_dsc
    OR-fusions are equally v3 arithmetic and must be named explicitly so the
    provenance doesn't imply they are Standard-prescribed.
    """
    entries = build_composition_provenance()
    by_group = {e["group"]: e for e in entries}

    vmc_id = by_group["vmc_identification"]
    assert vmc_id["rule"] == "or"
    assert "rule_provenance" in vmc_id
    assert "v3 arithmetic choice" in vmc_id["rule_provenance"]
    assert "§4.2 p.25" in vmc_id["rule_provenance"]
    assert "no intra-pair operator prescribed" in vmc_id["rule_provenance"]
    # No OTHER per-group entry carries rule_provenance (the override is
    # deliberately vmc_identification-only; the coupling entry is separate).
    for g, e in by_group.items():
        if g not in ("vmc_identification", "reliability_coupling"):
            assert "rule_provenance" not in e, g

    sp = by_group["reliability_coupling"]["structure_provenance"]
    assert "E_vmc = OR(VMC_VARIANCE_PREVENTION" in sp
    assert "E_dsc = OR(DSC_PREVENTION" in sp
    assert "E_meta = OR(E_vmc, E_dsc)" in sp
    assert "v3 arithmetic" in sp
    assert "no Standard-prescribed cross-family operator" in sp
