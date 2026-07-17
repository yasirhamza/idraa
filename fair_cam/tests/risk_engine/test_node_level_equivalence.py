"""#130 Task 6 deliverable (pulled forward to gate the Task-5 fix).

Node-level engine equivalence / anti-double-count test (plan-gate I-M7 / arch-2;
spec §13: "equivalence test asserts node-level outcomes (table-driven over all
groups), not just shared group-eff").

WHY THIS EXISTS HERE NOW (finding #2): the existing equivalence guarantee
(`test_diagnostic_consumes_compose_groups`) compares engine and diagnostic at the
`GroupEffectivenessReport` level, where they share `compose_groups` BY
CONSTRUCTION — so it is structurally blind to errors in the engine-only collapse
step `_group_comp_to_node_multipliers` (the single place group effectiveness
becomes FAIR-node multipliers). The VMC triple-count reviewer-BLOCKER lived
entirely in that collapse step and went undetected precisely because no
node-level test existed. This test pins that EACH composed group's effectiveness
reaches each FAIR node EXACTLY ONCE, equal to the single-application closed form
`1 - E_group·w` from `GROUP_NODE_MAPPING`.

The Layer-group→node single-application contract:
  * LEC Prevention   → TEF (0.8) + Vuln (0.9), eff = LEC_PREVENTION group eff.
  * LEC Detection    → NO standalone node (gates Response only).
  * LEC Response     → Magnitude (sec 0.5 / prim 0.2), eff = the
                       DETECTION_RESPONSE pair eff (Detection-gated, D8).
  * VMC variance-prev → NO standalone node (Slice 2 #439 D1 retirement, below).
  * VMC Id / Corr    → NO standalone node each (operands of the AND-pair).
  * VMC Id∧Corr pair → NO standalone node (Slice 2 #439 D1 retirement, below).
  * DSC Prevention   → NO standalone node (Slice 2 #439 D1 retirement, below).
  * DSC Id∧Corr pair → NO standalone node (Slice 2 #439 D1 retirement, below).

Slice 2 (#439) UPDATE: the pre-Slice-2 single-application contract routed
VMC_VARIANCE_PREVENTION / VMC_IDENTIFICATION_CORRECTION_PAIR to Vuln (0.3) and
DSC_PREVENTION / DSC_IDENTIFICATION_CORRECTION_PAIR to Magnitude (sec 0.5 /
prim 0.2) — the direct meta node-target channel this module's anti-double/
triple-count tests were written to pin. D1 retires those direct targets
entirely: `GROUP_NODE_MAPPING` now carries empty targets/weights for all four
meta groups (`fair_cam/tests/test_composition_topology.py::
test_meta_groups_have_no_direct_node_targets`), so at the node-multiplier
level a fully-exercised VMC/DSC control now produces IDENTITY on every node
(mirroring the pre-existing `lec_detection_no_node` case) — value flows
instead through the kappa reliability coupling (Task 2+), which is NOT yet
wired into `_group_comp_to_node_multipliers` as of this task.
"""

from __future__ import annotations

import pytest

from fair_cam.composition import and_compose
from fair_cam.models.composition_topology import (
    GROUP_NODE_MAPPING,
    PAIR_RECIPES,
    BooleanGroup,
)
from fair_cam.risk_engine.control_aware import _NODE_KEYS, _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import compose_groups
from fair_cam.tests.risk_engine._helpers import make_control

# Each table row: (label, control assignment specs, effectiveness-source group,
# expected node-targets). The control fully exercises exactly one composed group.
# The effectiveness-source group is the group whose composed effectiveness the
# engine applies to the node(s) — for the gated/paired routes it is the PAIR, not
# the raw leaf.
_FULL_PREVENTION = [
    ("lec_prev_avoidance", "probability", 0.9),
    ("lec_prev_deterrence", "probability", 0.9),
    ("lec_prev_resistance", "probability", 0.9),
]
_FULL_DETECTION = [
    ("lec_det_visibility", "probability", 0.9),
    ("lec_det_monitoring", "elapsed_time", 5.0),
    ("lec_det_recognition", "probability", 0.9),
]
_FULL_RESPONSE = [
    ("lec_resp_event_termination", "elapsed_time", 5.0),
    ("lec_resp_resilience", "probability", 0.8),
]
_FULL_VMC_VARIANCE = [
    ("vmc_prev_reduce_change_freq", "percent_reduction", 0.6),
    ("vmc_prev_reduce_variance_prob", "percent_reduction", 0.6),
]
_FULL_VMC_ID = [
    ("vmc_id_threat_intelligence", "probability", 0.9),
    ("vmc_id_control_monitoring", "probability", 0.9),
]
_FULL_VMC_CORR = [
    ("vmc_corr_treatment_selection", "probability", 0.9),
    ("vmc_corr_implementation", "elapsed_time", 5.0),
]
_FULL_DSC_PREVENTION = [
    ("dsc_prev_defined_expectations", "probability", 0.9),
    ("dsc_prev_communication", "probability", 0.9),
    ("dsc_prev_sa_data_asset", "probability", 0.9),
    ("dsc_prev_sa_data_threat", "probability", 0.9),
    ("dsc_prev_sa_data_controls", "probability", 0.9),
    ("dsc_prev_sa_analysis", "probability", 0.9),
    ("dsc_prev_sa_reporting", "probability", 0.9),
    ("dsc_prev_ensure_capability", "probability", 0.9),
    ("dsc_prev_incentives", "probability", 0.9),
]
_FULL_DSC_PAIR = [
    ("dsc_id_misaligned", "probability", 0.9),
    ("dsc_corr_misaligned", "probability", 0.9),
]

# (label, assignment specs, source-group whose eff is applied, expected nodes).
_CASES = [
    (
        "lec_prevention",
        _FULL_PREVENTION,
        BooleanGroup.LEC_PREVENTION,
        ("threat_event_frequency", "vulnerability"),
    ),
    # Detection alone -> no standalone node multiplier (all identity).
    ("lec_detection_no_node", _FULL_DETECTION, None, ()),
    # Response gated by Detection -> magnitude, eff = the AND-pair.
    (
        "lec_response_gated",
        _FULL_DETECTION + _FULL_RESPONSE,
        BooleanGroup.LEC_DETECTION_RESPONSE_PAIR,
        ("secondary_loss", "primary_loss"),
    ),
    # Slice 2 (#439) D1: VMC_VARIANCE_PREVENTION no longer has a direct node
    # target (retired; value flows through the kappa reliability coupling
    # instead, not yet wired into the engine as of this task) -> identity.
    ("vmc_variance_prevention", _FULL_VMC_VARIANCE, None, ()),
    # Slice 2 (#439) D1: VMC_IDENTIFICATION_CORRECTION_PAIR no longer has a
    # direct vuln target (the former reviewer triple-count guard target) ->
    # identity. See test_vmc_id_correction_vuln_not_tripled_node_level below
    # for the direct retirement pin.
    ("vmc_id_correction_pair", _FULL_VMC_ID + _FULL_VMC_CORR, None, ()),
    # Slice 2 (#439) D1: DSC_PREVENTION no longer has a direct magnitude
    # target -> identity.
    ("dsc_prevention", _FULL_DSC_PREVENTION, None, ()),
    # Slice 2 (#439) D1: DSC_IDENTIFICATION_CORRECTION_PAIR no longer has a
    # direct magnitude target -> identity.
    ("dsc_id_correction_pair", _FULL_DSC_PAIR, None, ()),
]


def _expected_source_effectiveness(comp, source_group):
    """Independent re-derivation of the effectiveness the engine SHOULD apply.

    For pair groups we AND-compose the two child leaf effectivenesses by hand
    (independent of `_group_comp_to_node_multipliers`); for leaf groups we read
    the composed group eff directly. Returns None when the group has no operands.
    """
    if source_group in PAIR_RECIPES:
        left, right = PAIR_RECIPES[source_group]
        lhs = comp.group_effectiveness.get(left)
        rhs = comp.group_effectiveness.get(right)
        # DSC pair has its OWN members (no PAIR_RECIPES child-leaf split), but it
        # is NOT in PAIR_RECIPES, so this branch is only the LEC/VMC composed pairs.
        if lhs is None or rhs is None:
            return None
        return and_compose([lhs, rhs])
    return comp.group_effectiveness.get(source_group)


@pytest.mark.parametrize("label,specs,source_group,expected_targets", _CASES, ids=lambda c: c)
def test_each_group_reaches_each_node_exactly_once(
    label, specs, source_group, expected_targets
) -> None:
    """Table-driven node-level single-application contract over ALL composed
    groups: a control fully exercising one group yields, on each target node, the
    closed form `1 - E_source·w` — applied exactly once. Non-target nodes stay at
    identity (1.0), proving the group's effectiveness did not leak onto any other
    node (anti-double-count / anti-mis-route)."""
    ctrl = make_control(control_id=label, assignments=specs)
    comp = compose_groups([ctrl])
    multipliers = _group_comp_to_node_multipliers(comp)

    if source_group is None:
        # No standalone node: every node identity.
        for node in _NODE_KEYS:
            assert multipliers[node] == pytest.approx(1.0), (
                f"{label}: {node} should be identity (no standalone node)"
            )
        return

    eff = _expected_source_effectiveness(comp, source_group)
    assert eff is not None, f"{label}: source group {source_group} produced no effectiveness"

    mapping = GROUP_NODE_MAPPING[source_group]
    for node in _NODE_KEYS:
        if node in expected_targets:
            w = mapping.weights[node]
            expected = 1.0 - eff * w
            assert multipliers[node] == pytest.approx(expected), (
                f"{label}: {node} expected single-application {expected!r} "
                f"(1 - {eff!r}*{w!r}), got {multipliers[node]!r}"
            )
        else:
            assert multipliers[node] == pytest.approx(1.0), (
                f"{label}: {node} should be identity — group eff leaked onto a "
                f"non-target node (double-count / mis-route)"
            )


def test_vmc_id_correction_vuln_not_tripled_node_level() -> None:
    """Slice 2 (#439) D1 retirement pin — supersedes the pre-Slice-2 #130
    anti-triple-count pin (which asserted the single pair-application
    `1 - E_pair·0.3` beat the buggy triple product `(1-E_id·0.3)*(1-E_corr·0.3)*
    (1-E_pair·0.3)`). D1 retires the VMC Id∧Corr pair's direct vulnerability
    target entirely (`GROUP_NODE_MAPPING[VMC_IDENTIFICATION_CORRECTION_PAIR]`
    now has empty targets/weights), so a fully-exercised VMC Id∧Corr control
    now produces IDENTITY on vulnerability regardless of the (still non-zero,
    still AND-composed) pair effectiveness — value flows through the kappa
    reliability coupling instead (Task 2+), not yet wired into the engine as of
    this task."""
    ctrl = make_control(control_id="vmc", assignments=_FULL_VMC_ID + _FULL_VMC_CORR)
    comp = compose_groups([ctrl])
    id_eff = comp.group_effectiveness[BooleanGroup.VMC_IDENTIFICATION]
    corr_eff = comp.group_effectiveness[BooleanGroup.VMC_CORRECTION]
    pair_eff = and_compose([id_eff, corr_eff])
    assert pair_eff is not None and pair_eff > 0.0  # precondition: pair IS non-zero

    got = _group_comp_to_node_multipliers(comp)["vulnerability"]
    assert got == pytest.approx(1.0)  # retired direct channel -> identity (D1)
