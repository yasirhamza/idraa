"""Contract tests for weight-robustness co-variation, slot-pinning, and field-sync.

Covers (issue #419):
  - Arch-I5 slot-pinning: the (group,node) slots the ensemble perturbs are exactly
    the multiplier keys that _group_comp_to_node_multipliers moves under a perturbed
    mapping; LEC_RESPONSE weights move loss nodes; D/R-pair weights are inert.
  - Spec-N1 / SpecCompl-KeyShape1: persisted weight_robustness keys ==
    WEIGHT_ROBUSTNESS_KEYS (present-but-None counts as present).
  - Meth-I5 co-variation: shared-constant slots move together; within-function
    correlation verified via identical logit shift (Slice 2 #439 D1 retired the
    prior N>=3 magnitude / N=2 vmc.vuln multi-slot examples -- see
    test_shared_constant_moves_all_slots_together for the updated slot table).
"""

from __future__ import annotations

import copy

import numpy as np
import pytest
from fair_cam.models.composition_topology import (
    GROUP_NODE_MAPPING,
    KAPPA_META_RELIABILITY,
    BooleanGroup,
    NodeMapping,
)
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import GroupComposition, compose_groups
from fair_cam.tests.risk_engine._helpers import make_control

from idraa.services.weight_robustness import (
    CANONICAL_PARAM_SLOTS,
    CORRELATION_GROUPS,
    KAPPA_PARAM_KEY,
    WEIGHT_ROBUSTNESS_KEYS,
    _logit,
    canonical_param_values,
    run_weight_ensemble,
    sample_ensemble_draw,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_all_groups_comp() -> GroupComposition:
    """Return a GroupComposition with ALL relevant groups having non-zero effectiveness.

    Controls:
      - LEC_PREVENTION (lec_prev_resistance) — activates LEC_PREVENTION
      - Full detection+response trio — activates LEC_DETECTION, LEC_RESPONSE,
        LEC_DETECTION_RESPONSE_PAIR (all three detection sub-functions required for
        the AND-composition to be non-zero; both response sub-functions for coverage)
      - VMC_VARIANCE_PREVENTION (vmc_prev_reduce_change_freq) — activates VMC_VP
      - VMC_IDENTIFICATION + VMC_CORRECTION (with elapsed_time implementation) —
        activates VMC_IDENTIFICATION_CORRECTION_PAIR
      - DSC_IDENTIFICATION_CORRECTION_PAIR (dsc_id_misaligned + dsc_corr_misaligned)

    Rationale: _group_comp_to_node_multipliers only moves a node when the group's
    effectiveness is non-zero. Building a control set where all engine-applied groups
    contribute ensures the "perturbed slots == moved nodes" set equality holds exactly.
    """
    c_prev = make_control(
        control_id="prev",
        assignments=[("lec_prev_resistance", "probability", 0.5)],
    )
    c_det_resp = make_control(
        control_id="det_resp",
        assignments=[
            # Detection AND-trio: all three required (one missing -> AND collapses to 0)
            ("lec_det_visibility", "probability", 0.7),
            ("lec_det_monitoring", "elapsed_time", 24.0),
            ("lec_det_recognition", "probability", 0.8),
            # Response weak-AND-trio (two arms; currency arm has no opeff weight)
            ("lec_resp_event_termination", "elapsed_time", 10.0),
            ("lec_resp_resilience", "probability", 0.6),
        ],
    )
    c_vmc_var = make_control(
        control_id="vmc_var",
        assignments=[("vmc_prev_reduce_change_freq", "percent_reduction", 0.3)],
    )
    c_vmc_id_corr = make_control(
        control_id="vmc_id_corr",
        assignments=[
            # VMC_IDENTIFICATION (AND-compose; both subs needed for non-zero)
            ("vmc_id_threat_intelligence", "probability", 0.5),
            ("vmc_id_control_monitoring", "probability", 0.4),
            # VMC_CORRECTION (AND-compose; elapsed_time arm needed alongside probability)
            ("vmc_corr_treatment_selection", "probability", 0.5),
            ("vmc_corr_implementation", "elapsed_time", 30.0),
        ],
    )
    c_dsc = make_control(
        control_id="dsc",
        assignments=[
            ("dsc_id_misaligned", "probability", 0.4),
            ("dsc_corr_misaligned", "probability", 0.4),
        ],
    )
    return compose_groups([c_prev, c_det_resp, c_vmc_var, c_vmc_id_corr, c_dsc])


# ---------------------------------------------------------------------------
# Arch-I5: slot-pinning
# ---------------------------------------------------------------------------


def test_perturbed_slots_equal_moved_nodes() -> None:
    """Arch-I5: the set of (group,node) slots the ensemble perturbs is exactly the set
    of FAIR nodes that _group_comp_to_node_multipliers moves under a perturbed mapping.

    With all engine-applied groups having non-zero effectiveness, any weight perturbation
    (sigma > 0) propagates to the nodes those groups target. The ensemble's CANONICAL_PARAM_SLOTS
    drive exactly the four FAIR nodes: threat_event_frequency, vulnerability,
    secondary_loss, primary_loss.
    """
    comp = _make_all_groups_comp()
    canon_mults = _group_comp_to_node_multipliers(comp)

    rng = np.random.default_rng(42)
    perturbed_nm = sample_ensemble_draw(rng, sigma=0.6)[0]
    perturbed_mults = _group_comp_to_node_multipliers(comp, perturbed_nm)

    moved = {k for k in canon_mults if abs(canon_mults[k] - perturbed_mults[k]) > 1e-12}

    # Union of target nodes from all CANONICAL_PARAM_SLOTS entries.
    expected = {node for slots in CANONICAL_PARAM_SLOTS.values() for _g, node in slots}

    assert moved == expected, (
        f"Moved nodes {moved!r} != expected target nodes {expected!r}; "
        "ensemble slots and engine targets are out of sync"
    )


def test_lec_response_weights_move_loss_nodes() -> None:
    """Arch-I5: perturbing LEC_RESPONSE weights changes secondary_loss and primary_loss.

    The engine reads LEC_RESPONSE's weights (secondary_loss, primary_loss) from the
    node_mapping when applying the D/R-pair gated magnitude reduction. With a non-zero
    D/R-pair effectiveness (requires non-zero LEC_DETECTION AND LEC_RESPONSE), changing
    those weights changes the magnitude multipliers.
    """
    # Build a control with a non-zero D/R pair (full detection AND-trio + response)
    c_det_resp = make_control(
        control_id="det_resp",
        assignments=[
            ("lec_det_visibility", "probability", 0.7),
            ("lec_det_monitoring", "elapsed_time", 24.0),
            ("lec_det_recognition", "probability", 0.8),
            ("lec_resp_event_termination", "elapsed_time", 10.0),
            ("lec_resp_resilience", "probability", 0.6),
        ],
    )
    comp = compose_groups([c_det_resp])

    # Verify D/R pair has non-zero effectiveness (test precondition)
    pair_eff = comp.group_effectiveness.get(BooleanGroup.LEC_DETECTION_RESPONSE_PAIR)
    assert pair_eff is not None and pair_eff > 0, (
        f"precondition: D/R-pair must have non-zero eff for the weight to matter; got {pair_eff!r}"
    )

    canon_mults = _group_comp_to_node_multipliers(comp)

    # Perturb LEC_RESPONSE weights (smaller -> weaker magnitude reduction)
    perturbed = copy.deepcopy(GROUP_NODE_MAPPING)
    nm_resp = perturbed[BooleanGroup.LEC_RESPONSE]
    perturbed[BooleanGroup.LEC_RESPONSE] = NodeMapping(
        nm_resp.targets,
        {"secondary_loss": 0.1, "primary_loss": 0.05},  # much smaller than canonical 0.5/0.2
        nm_resp.citation,
        nm_resp.weights_provenance,
    )
    perturbed_mults = _group_comp_to_node_multipliers(comp, perturbed)

    assert perturbed_mults["secondary_loss"] != canon_mults["secondary_loss"], (
        "secondary_loss multiplier must change when LEC_RESPONSE weight is perturbed"
    )
    assert perturbed_mults["primary_loss"] != canon_mults["primary_loss"], (
        "primary_loss multiplier must change when LEC_RESPONSE weight is perturbed"
    )
    # Likelihood nodes must be unaffected (LEC_RESPONSE targets magnitude only)
    assert perturbed_mults["threat_event_frequency"] == canon_mults["threat_event_frequency"]
    assert perturbed_mults["vulnerability"] == canon_mults["vulnerability"]


def test_drpair_weights_are_inert() -> None:
    """Arch-I5: changing LEC_DETECTION_RESPONSE_PAIR weights in the node_mapping has no
    effect on _group_comp_to_node_multipliers output.

    The engine skips LEC_DETECTION_RESPONSE_PAIR as a standalone group (it is only
    used to supply the effectiveness value for the LEC_RESPONSE gating, not its own
    weights). The ensemble therefore correctly excludes it from _SKIP and never perturbs it.
    """
    c_det_resp = make_control(
        control_id="det_resp",
        assignments=[
            ("lec_det_visibility", "probability", 0.7),
            ("lec_det_monitoring", "elapsed_time", 24.0),
            ("lec_det_recognition", "probability", 0.8),
            ("lec_resp_event_termination", "elapsed_time", 10.0),
            ("lec_resp_resilience", "probability", 0.6),
        ],
    )
    comp = compose_groups([c_det_resp])
    canon_mults = _group_comp_to_node_multipliers(comp)

    # Drastically change D/R-pair weights — should have zero effect on multipliers
    perturbed = copy.deepcopy(GROUP_NODE_MAPPING)
    nm_drp = perturbed[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    perturbed[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR] = NodeMapping(
        nm_drp.targets,
        {"secondary_loss": 0.001, "primary_loss": 0.001},  # extreme change vs canonical 0.5/0.2
        nm_drp.citation,
        nm_drp.weights_provenance,
    )
    drp_mults = _group_comp_to_node_multipliers(comp, perturbed)

    assert drp_mults == canon_mults, (
        "LEC_DETECTION_RESPONSE_PAIR weights must be inert in node_multipliers; "
        "the engine skips this group (it gates LEC_RESPONSE effectiveness, not its weights). "
        f"Unexpected diff: {[(k, canon_mults[k], drp_mults[k]) for k in canon_mults if canon_mults[k] != drp_mults[k]]!r}"
    )


# ---------------------------------------------------------------------------
# Spec-N1 / SpecCompl-KeyShape1: field-sync
# ---------------------------------------------------------------------------


def _make_persisted_blob(ensemble_result: dict[str, object], /) -> dict[str, object]:
    """Simulate the executor's persisted blob by adding band and canonical_value.

    run_weight_ensemble returns the statistical output (10 keys). The executor
    (services/run_executor.py) enriches it with `band` (snapshotted band config) and
    `canonical_value` (per-control reference values) before storing in the DB column.
    This helper mirrors that wrapping so the field-sync test validates the FULL
    persisted shape, not just the ensemble output.
    """
    return {
        **ensemble_result,
        "band": {"logit_sigma": 0.6, "distribution": "logit_normal", "seed": 42, "draws": 64},
        "canonical_value": {"ctrl_a": 100.0},
        # 2026-07-04 side-by-side augmentation (mirrors _build_weight_robustness;
        # the writer now ALSO enforces WEIGHT_ROBUSTNESS_KEYS at runtime, so this
        # replica drifting again fails loud in production code, not just here).
        "canonical_value_typical": {"ctrl_a": 60.0},
        "basis": "mean",
    }


def test_field_sync_weight_robustness_keys() -> None:
    """Spec-N1 / SpecCompl-KeyShape1: persisted weight_robustness top-level keys ==
    WEIGHT_ROBUSTNESS_KEYS (imported from weight_robustness).

    run_weight_ensemble returns 10 keys; the executor adds `band` + `canonical_value`
    + `canonical_value_typical` + `basis` before persisting (14 keys total). The field-sync test validates the FULL
    persisted blob shape on all three paths (AGGREGATE, SINGLE, insufficient-budget
    fallback). present-but-None counts as present (Spec-Compl-1).
    """
    cids = ["a", "b", "c"]

    def value_fn(nm):
        return {c: (len(cids) - i) * 100.0 for i, c in enumerate(cids)}

    # Full path: compute_rank_stability=True (AGGREGATE), N>=3 controls.
    # BATCHED contract (#419/#439): the runner hands the value fn ALL draws at
    # once and expects one dict per draw, in order.
    result_agg = _make_persisted_blob(
        run_weight_ensemble(
            per_control_value_fn=lambda draws: [value_fn(d) for d in draws],
            control_ids=cids,
            rng=np.random.default_rng(99),
            draws=64,
            eval_cost_per_draw=8,
            # sampler=lambda r: (None, KAPPA_META_RELIABILITY) -> passes None as the
            # node_mapping half of the EnsembleDraw to value_fn; value_fn above
            # ignores nm entirely, so None is sufficient for key-shape validation.
            # If None-handling in run_weight_ensemble or value_fn ever changes, this
            # test will fail loudly rather than silently passing.
            sampler=lambda r: (None, KAPPA_META_RELIABILITY),
        )
    )
    assert set(result_agg.keys()) == WEIGHT_ROBUSTNESS_KEYS, (
        f"AGGREGATE path keys {set(result_agg.keys())!r} != WEIGHT_ROBUSTNESS_KEYS {WEIGHT_ROBUSTNESS_KEYS!r}"
    )

    # SINGLE path: compute_rank_stability=False
    result_single = _make_persisted_blob(
        run_weight_ensemble(
            per_control_value_fn=lambda draws: [value_fn(d) for d in draws],
            control_ids=cids,
            rng=np.random.default_rng(99),
            draws=64,
            eval_cost_per_draw=8,
            sampler=lambda r: (
                None,
                KAPPA_META_RELIABILITY,
            ),  # None -> uses canonical node_mapping for all draws; sufficient for key-shape validation
            compute_rank_stability=False,
        )
    )
    assert set(result_single.keys()) == WEIGHT_ROBUSTNESS_KEYS, (
        f"SINGLE path keys {set(result_single.keys())!r} != WEIGHT_ROBUSTNESS_KEYS {WEIGHT_ROBUSTNESS_KEYS!r}"
    )

    # Insufficient-budget fallback path
    result_fallback = _make_persisted_blob(
        run_weight_ensemble(
            per_control_value_fn=lambda draws: [{"a": 1.0} for _ in draws],
            control_ids=["a"],
            rng=np.random.default_rng(1),
            draws=1000,
            eval_cost_per_draw=1000,
            eval_budget=5000,
            min_draws=32,
        )
    )
    assert set(result_fallback.keys()) == WEIGHT_ROBUSTNESS_KEYS, (
        f"Fallback path keys {set(result_fallback.keys())!r} != WEIGHT_ROBUSTNESS_KEYS {WEIGHT_ROBUSTNESS_KEYS!r}"
    )


# ---------------------------------------------------------------------------
# Meth-I5: co-variation — shared-constant slots move together (N>=3)
# ---------------------------------------------------------------------------


def test_shared_constant_moves_all_slots_together() -> None:
    """Meth-I5, updated for Slice 2 (#439) D1: the pre-Slice-2 multi-slot
    shared-constant examples no longer exist. D1 retired DSC_PREVENTION's and
    DSC_IDENTIFICATION_CORRECTION_PAIR's direct secondary_loss/primary_loss
    targets, and VMC_VARIANCE_PREVENTION's / VMC_IDENTIFICATION_CORRECTION_PAIR's
    direct vulnerability target -- all four now route through the kappa
    reliability coupling instead of GROUP_NODE_MAPPING. Post-D1:

    magnitude.secondary -> (LEC_RESPONSE, secondary_loss) only          -- N=1
                            (LEC_DETECTION_RESPONSE_PAIR is in _SKIP and
                            contributes zero engine slots, per _engine_slots())
    magnitude.primary   -> (LEC_RESPONSE, primary_loss) only            -- N=1
    vmc.vuln            -> GONE (0 slots; not a CANONICAL_PARAM_SLOTS key at all)
    meta.kappa          -> present in canonical_param_values() and
                            CORRELATION_GROUPS (replaces the retired "vmc" group)
                            but ABSENT from CANONICAL_PARAM_SLOTS -- it is not a
                            (group,node) slot; sampling it is Task 4's job.

    The N>=1 "moves together" property is now vacuous for magnitude (a single
    slot trivially "agrees with itself"); the shared-logit-Z MECHANISM is still
    covered for a real multi-slot case by `prevention.tef`/`prevention.vuln`
    (see test_within_function_prevention_tef_and_vuln_share_logit_z below).
    """
    rng = np.random.default_rng(11)
    m = sample_ensemble_draw(rng)[0]

    # magnitude.secondary: exactly 1 slot post-D1 (LEC_RESPONSE only)
    secondary_slots = CANONICAL_PARAM_SLOTS["magnitude.secondary"]
    assert secondary_slots == [(BooleanGroup.LEC_RESPONSE, "secondary_loss")]
    secondary_vals = {m[g].weights[node] for g, node in secondary_slots}
    assert len(secondary_vals) == 1

    # magnitude.primary: same invariant
    primary_slots = CANONICAL_PARAM_SLOTS["magnitude.primary"]
    assert primary_slots == [(BooleanGroup.LEC_RESPONSE, "primary_loss")]
    primary_vals = {m[g].weights[node] for g, node in primary_slots}
    assert len(primary_vals) == 1

    # vmc.vuln: retired entirely (D1) -- not a CANONICAL_PARAM_SLOTS key.
    assert "vmc.vuln" not in CANONICAL_PARAM_SLOTS

    # meta.kappa: canonical-values/correlation-group member, NOT a (group,node) slot.
    canon = canonical_param_values()
    assert canon[KAPPA_PARAM_KEY] == pytest.approx(0.5)
    assert CORRELATION_GROUPS["meta"] == [KAPPA_PARAM_KEY]
    assert KAPPA_PARAM_KEY not in CANONICAL_PARAM_SLOTS
    with pytest.raises(KeyError):
        _ = CANONICAL_PARAM_SLOTS[KAPPA_PARAM_KEY]


def test_within_function_prevention_tef_and_vuln_share_logit_z() -> None:
    """Meth-I5: prevention.tef and prevention.vuln are in the same CORRELATION_GROUP
    ('prevention') and share ONE logit-space Z, so their logit shifts are identical.

    This is a regression guard: independent sampling would give distinct shifts (~31%
    band-narrowing for prevention-dominated controls via partial cancellation).
    """
    from idraa.services.weight_robustness import canonical_param_values

    canon = canonical_param_values()
    rng = np.random.default_rng(5)
    m = sample_ensemble_draw(rng, sigma=0.6)[0]

    w_tef = m[BooleanGroup.LEC_PREVENTION].weights["threat_event_frequency"]
    w_vuln = m[BooleanGroup.LEC_PREVENTION].weights["vulnerability"]

    shift_tef = _logit(w_tef) - _logit(canon["prevention.tef"])
    shift_vuln = _logit(w_vuln) - _logit(canon["prevention.vuln"])

    assert shift_tef == pytest.approx(shift_vuln), (
        "prevention.tef and prevention.vuln must share one logit-Z (Meth-I5); "
        f"got shifts {shift_tef:.6f} vs {shift_vuln:.6f}"
    )
