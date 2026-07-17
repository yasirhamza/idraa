"""Slice 1 — effect-type-aware recovery gate on _group_comp_to_node_multipliers.

Availability effects self-detect (FAIR-CAM §3.3.2 p.19): the Detection->Response
AND precondition (§3.3 p.18) is intrinsically satisfied, so the raw LEC_RESPONSE
group effectiveness is credited to the magnitude multiplier WITHOUT a co-present
Detection control. Stealth C/I effects stay detection-gated. Standard-consistent
interpretation, scoped to availability only.
"""

from __future__ import annotations

from fair_cam.models.composition_topology import GROUP_NODE_MAPPING, BooleanGroup
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import compose_groups
from fair_cam.tests.risk_engine._helpers import make_control


def _response_only():
    return make_control(
        control_id="resp",
        assignments=[("lec_resp_resilience", "probability", 0.6)],
    )


def test_availability_credits_raw_response_without_detection() -> None:
    """No Detection control, availability effect -> raw LEC_RESPONSE credited."""
    comp = compose_groups([_response_only()])
    raw_resp = comp.group_effectiveness[BooleanGroup.LEC_RESPONSE]
    assert raw_resp is not None and raw_resp > 0.0

    mults = _group_comp_to_node_multipliers(comp, availability_self_detection=True)
    w = GROUP_NODE_MAPPING[BooleanGroup.LEC_RESPONSE].weights
    assert mults["secondary_loss"] == 1.0 - raw_resp * w["secondary_loss"]
    assert mults["primary_loss"] == 1.0 - raw_resp * w["primary_loss"]
    # Availability credit never touches frequency/vuln (magnitude-only re-route).
    assert mults["threat_event_frequency"] == 1.0
    assert mults["vulnerability"] == 1.0


def test_stealth_stays_detection_gated_no_detection_is_identity() -> None:
    """Default (availability_self_detection=False): no Detection -> identity (D8)."""
    comp = compose_groups([_response_only()])
    mults = _group_comp_to_node_multipliers(comp)  # default False
    assert mults["secondary_loss"] == 1.0
    assert mults["primary_loss"] == 1.0


def test_weak_and_single_deficient_response_member_does_not_zero() -> None:
    """§3.3.1 p.19: a single deficient (0.0) Response member must not zero the
    group. Two Response sub-functions, one at 0.0; availability credits a >0
    magnitude reduction (weak-AND averages present members, not a product)."""
    ctrl = make_control(
        control_id="resp2",
        assignments=[
            ("lec_resp_resilience", "probability", 0.0),
            ("lec_resp_event_termination", "elapsed_time", 32.0),
        ],
    )
    comp = compose_groups([ctrl])
    raw_resp = comp.group_effectiveness[BooleanGroup.LEC_RESPONSE]
    assert raw_resp is not None and raw_resp > 0.0  # not zeroed by the 0.0 member
    mults = _group_comp_to_node_multipliers(comp, availability_self_detection=True)
    assert mults["secondary_loss"] < 1.0  # scores despite the deficient member


def test_availability_no_response_control_is_identity() -> None:
    """Availability flag set but no Response control present -> identity (nothing
    to credit; raw LEC_RESPONSE eff is None)."""
    from fair_cam.tests.risk_engine._helpers import make_control as mc

    prevention = mc(
        control_id="prev",
        assignments=[("lec_prev_resistance", "probability", 0.5)],
    )
    comp = compose_groups([prevention])
    assert comp.group_effectiveness[BooleanGroup.LEC_RESPONSE] is None
    mults = _group_comp_to_node_multipliers(comp, availability_self_detection=True)
    assert mults["secondary_loss"] == 1.0
    assert mults["primary_loss"] == 1.0
