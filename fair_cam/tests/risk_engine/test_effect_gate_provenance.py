"""Slice 1 methodology guardrails — the availability recovery gate stays cited,
availability-scoped, and free of any new calibration weight."""

from __future__ import annotations

from fair_cam.models.composition_topology import GROUP_NODE_MAPPING, BooleanGroup
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import compose_groups
from fair_cam.tests.risk_engine._helpers import make_control


def test_gate_docstring_cites_availability_self_detection() -> None:
    doc = _group_comp_to_node_multipliers.__doc__ or ""
    assert "3.3.2 p.19" in doc  # primary self-detection citation
    assert "3.3 p.18" in doc  # the D8 detection-AND it interprets
    assert "AVAILABILITY EFFECTS ONLY" in doc  # scoping label, verbatim
    assert "NEVER generalized to stealth" in doc


def test_availability_branch_only_touches_magnitude_nodes() -> None:
    """The availability credit must never reduce frequency/vuln — magnitude-only,
    the same nodes as the detection-gated path (no scope creep to other nodes)."""
    comp = compose_groups(
        [make_control(control_id="r", assignments=[("lec_resp_resilience", "probability", 0.6)])]
    )
    mults = _group_comp_to_node_multipliers(comp, availability_self_detection=True)
    assert mults["threat_event_frequency"] == 1.0
    assert mults["vulnerability"] == 1.0
    assert mults["secondary_loss"] < 1.0


def test_no_new_calibration_weight_reuses_lec_response_weights() -> None:
    """Slice 1 introduces no new numeric weight: the availability path reuses the
    existing LEC_RESPONSE magnitude weights + their implementation-calibration
    provenance (identifiability discipline: nothing new to calibrate)."""
    nm = GROUP_NODE_MAPPING[BooleanGroup.LEC_RESPONSE]
    assert nm.weights == {"secondary_loss": 0.5, "primary_loss": 0.2}
    assert nm.weights_provenance == "implementation-calibration"


def test_stealth_regression_no_detection_still_zero() -> None:
    """Standard-faithfulness regression: default path (stealth) with no detection
    control keeps the identity multiplier (D8, §3.3 p.18) — the correct behaviour
    Slice 1 must not regress."""
    comp = compose_groups(
        [make_control(control_id="r", assignments=[("lec_resp_resilience", "probability", 0.6)])]
    )
    mults = _group_comp_to_node_multipliers(comp)  # default False
    assert mults["secondary_loss"] == 1.0
    assert mults["primary_loss"] == 1.0
