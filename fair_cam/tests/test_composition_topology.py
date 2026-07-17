"""#130 Task 4 — pin the declarative GROUP_NODE_MAPPING table.

These tests lock the group -> FAIR-node mapping (targets + weights + citation +
provenance), including the D4 Response -> Loss Magnitude re-route and the D7
secondary/primary weights. They also assert every leaf + pair BooleanGroup has a
mapping entry so a future group addition cannot silently route through nothing.
"""

from fair_cam.models.composition_topology import (
    GROUP_NODE_MAPPING,
    GROUP_TYPE,
    PAIR_GROUPS,
    BooleanGroup,
    GroupType,
    NodeMapping,
)


def test_response_group_targets_loss_magnitude_not_frequency():
    m = GROUP_NODE_MAPPING[BooleanGroup.LEC_RESPONSE]
    assert set(m.targets) == {"secondary_loss", "primary_loss"}  # Loss Magnitude
    assert "threat_event_frequency" not in m.targets
    assert "vulnerability" not in m.targets
    assert m.weights == {"secondary_loss": 0.5, "primary_loss": 0.2}  # D7 default
    assert "§3.3" in m.citation


def test_response_weights_labeled_implementation_calibration():
    # D7: the Standard gives no numeric weight -> not Standard-grounded.
    m = GROUP_NODE_MAPPING[BooleanGroup.LEC_RESPONSE]
    assert m.weights_provenance == "implementation-calibration"


def test_prevention_keeps_frequency_targets_with_current_weights():
    # Audit note: Prevention node targets retained (re-routing is out of scope).
    m = GROUP_NODE_MAPPING[BooleanGroup.LEC_PREVENTION]
    assert set(m.targets) == {"threat_event_frequency", "vulnerability"}
    assert m.weights == {"threat_event_frequency": 0.8, "vulnerability": 0.9}


def test_detection_pair_drives_magnitude_via_response_reroute():
    # The Detection-Response AND pair is the magnitude-effectiveness carrier
    # (D8): it shares Response's Loss-Magnitude targets + D7 weights.
    m = GROUP_NODE_MAPPING[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    assert set(m.targets) == {"secondary_loss", "primary_loss"}
    assert m.weights == {"secondary_loss": 0.5, "primary_loss": 0.2}


# NOTE (Slice 2 #439): the pre-Slice-2
# `test_vmc_vulnerability_target_applied_exactly_once` (VMC_VARIANCE_PREVENTION /
# VMC_IDENTIFICATION_CORRECTION_PAIR targeting vulnerability×0.3) and
# `test_dsc_groups_keep_loss_magnitude_target` (DSC_PREVENTION /
# DSC_IDENTIFICATION_CORRECTION_PAIR targeting loss magnitude) pinned the DIRECT
# meta node-target channels that Slice 2 retires (D1). Both are DELETED here —
# `test_meta_groups_have_no_direct_node_targets` below supersedes them exactly
# (it asserts empty targets/weights on all four groups, a strict superset of what
# the deleted tests checked for VMC_IDENTIFICATION/VMC_CORRECTION, which were
# already empty pre-Slice-2).


def test_every_boolean_group_has_a_node_mapping():
    # No leaf or pair group may route through nothing.
    for g in BooleanGroup:
        assert g in GROUP_NODE_MAPPING, f"missing GROUP_NODE_MAPPING entry for {g}"


def test_node_mapping_targets_and_weight_keys_agree():
    for g, m in GROUP_NODE_MAPPING.items():
        assert isinstance(m, NodeMapping)
        assert set(m.targets) == set(m.weights), f"{g}: targets != weight keys"
        assert m.weights_provenance in (
            "Standard-grounded",
            "implementation-calibration",
        )


def test_pair_groups_have_mappings():
    for g in PAIR_GROUPS:
        assert g in GROUP_NODE_MAPPING


def test_kappa_meta_reliability_pin() -> None:
    """Slice 2 pinning test: kappa is implementation-calibration, immutable in code.

    Mutation guard: changing the value or removing the constant fails this test.
    """
    from fair_cam.models.composition_topology import KAPPA_META_RELIABILITY

    assert KAPPA_META_RELIABILITY == 0.5


def test_meta_groups_have_no_direct_node_targets() -> None:
    """Slice 2 D1: direct VMC->vuln and DSC->magnitude channels are retired.

    Meta value flows ONLY through the reliability coupling."""
    for g in (
        BooleanGroup.VMC_VARIANCE_PREVENTION,
        BooleanGroup.VMC_IDENTIFICATION,
        BooleanGroup.VMC_CORRECTION,
        BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR,
        BooleanGroup.DSC_PREVENTION,
        BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR,
    ):
        m = GROUP_NODE_MAPPING[g]
        assert m.targets == (), f"{g}: direct node target must be retired (Slice 2 D1)"
        assert m.weights == {}, f"{g}: direct node weights must be retired (Slice 2 D1)"


def test_meta_group_operators_slice2() -> None:
    """Slice 2 D3 operator table. Labels per the spec's grounding section:

    - VMC_IDENTIFICATION AND->OR: v3 arithmetic choice (no intra-pair
      operator prescribed; TI and monitoring cover DIFFERENT variance
      sources, coverage-union approximation, section 4.2 p.25).
    - VMC_CORRECTION stays AND: the PRESCRIBED pin (section 4.3.1/4.3.2
      p.28). The implementation-gated absent-member handling is a documented
      DEVIATION implemented in the meta composition (Task 2), not here.
    - DSC_PREVENTION AND->WEAK_AND: documented DEVIATION from the nine
      section 5.1.x Boolean-AND prescriptions (see spec D3 justification).
    - Both id-corr pairings stay AND: Standard-PRESCRIBED (section 4 p.21;
      section 5 p.30 "both must exist").
    """
    assert GROUP_TYPE[BooleanGroup.VMC_IDENTIFICATION] == GroupType.OR
    assert GROUP_TYPE[BooleanGroup.VMC_CORRECTION] == GroupType.AND
    assert GROUP_TYPE[BooleanGroup.DSC_PREVENTION] == GroupType.WEAK_AND
    assert GROUP_TYPE[BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR] == GroupType.AND
    assert GROUP_TYPE[BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR] == GroupType.AND
