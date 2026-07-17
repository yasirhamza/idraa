"""Tests for the shared `compose_groups` routine (#130 Task 2).

`compose_groups` is the single source of truth for FAIR-CAM Boolean group
composition consumed by BOTH the Layer-2 diagnostic and the engine ALE path
(D2). These tests pin its contract: OR within a sub-function across controls →
the group's Standard rule across sub-functions (CURRENCY excluded as a separate
currency subtractor total), plus the pair-recipe second pass.
"""

from __future__ import annotations

import pytest

from fair_cam.composition import compute_assignment_opeff_two_branch
from fair_cam.models.composition_topology import BooleanGroup
from fair_cam.risk_engine.group_composition import GroupComposition, compose_groups
from fair_cam.tests.risk_engine._helpers import make_control


def test_response_group_weak_ands_opeffs_and_totals_currency():
    """LEC Response: weak-AND of the two opeffs; currency totalled separately.

    Event Termination (ELAPSED_TIME) → a computed opeff via two-branch math;
    Resilience (PROBABILITY) → 0.4 opeff directly; Loss Reduction (CURRENCY) →
    excluded from the mean, accumulated into `currency_subtractor_total`.
    """
    ctrl = make_control(
        assignments=[
            ("lec_resp_event_termination", "elapsed_time", 300.0),
            ("lec_resp_resilience", "probability", 0.4),
            ("lec_resp_loss_reduction", "currency", 5000.0),
        ]
    )
    et_opeff = compute_assignment_opeff_two_branch(ctrl.assignments[0])
    assert et_opeff is not None

    comp = compose_groups([ctrl])
    resp = comp.group_effectiveness[BooleanGroup.LEC_RESPONSE]
    # weak-AND of the TWO opeffs (the computed ET opeff + the 0.4 resilience
    # opeff), NOT the raw capability_value; CURRENCY is excluded from the mean.
    assert resp == pytest.approx((et_opeff + 0.4) / 2)
    assert comp.currency_subtractor_total == pytest.approx(5000.0)


def test_returns_group_composition_dataclass():
    comp = compose_groups([])
    assert isinstance(comp, GroupComposition)
    assert comp.currency_subtractor_total == 0.0
    # No controls: AND/OR leaf groups compose all-absent (0.0) members → 0.0
    # (parity with the diagnostic); LEC_RESPONSE (weak-AND, no present operands)
    # and the pair groups (a None child) → None.
    assert comp.group_effectiveness[BooleanGroup.LEC_RESPONSE] is None
    assert comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR] is None
    assert comp.group_effectiveness[BooleanGroup.LEC_DETECTION] == 0.0


def test_or_within_sub_function_across_controls():
    """Two controls hitting the SAME Prevention sub-function OR-compose."""
    c1 = make_control(
        control_id="c1",
        assignments=[("lec_prev_resistance", "probability", 0.5)],
    )
    c2 = make_control(
        control_id="c2",
        assignments=[("lec_prev_resistance", "probability", 0.5)],
    )
    comp = compose_groups([c1, c2])
    # OR within Resistance: 1 - (1-0.5)(1-0.5) = 0.75; Prevention is OR-group
    # over its members (Avoidance/Deterrence absent → 0.0): OR(0.75, 0, 0)=0.75.
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(0.75)


def test_and_group_absent_member_collapses_to_zero():
    """LEC Detection is AND; one present member + two absent → 0.0 (parity
    with the diagnostic's `.get(sf, 0.0)` semantics)."""
    ctrl = make_control(
        assignments=[("lec_det_visibility", "probability", 0.9)],
    )
    comp = compose_groups([ctrl])
    assert comp.group_effectiveness[BooleanGroup.LEC_DETECTION] == 0.0


def test_pair_recipe_ands_leaf_outputs():
    """Detection-Response pair = AND of the Detection & Response leaf outputs."""
    ctrl = make_control(
        assignments=[
            ("lec_det_visibility", "probability", 0.9),
            ("lec_det_monitoring", "elapsed_time", 60.0),
            ("lec_det_recognition", "probability", 0.9),
            ("lec_resp_event_termination", "elapsed_time", 300.0),
            ("lec_resp_resilience", "probability", 0.4),
        ],
    )
    comp = compose_groups([ctrl])
    det = comp.group_effectiveness[BooleanGroup.LEC_DETECTION]
    resp = comp.group_effectiveness[BooleanGroup.LEC_RESPONSE]
    assert det is not None and resp is not None
    pair = comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    assert pair == pytest.approx(det * resp)


def test_pair_recipe_none_when_response_side_absent():
    """The pair resolves to None when a child leaf eff is None.

    LEC_RESPONSE is a weak-AND group: with NO present opeff operands its eff is
    None (not 0.0). A Detection-only control therefore yields Response eff None
    → pair None (the engine maps that to magnitude multiplier 1.0 per D8).
    Detection itself is an AND group whose absent members pad to 0.0, so it is
    never None — only the weak-AND Response side drives the pair to None here."""
    ctrl = make_control(
        assignments=[
            ("lec_det_visibility", "probability", 0.9),
            ("lec_det_monitoring", "elapsed_time", 60.0),
            ("lec_det_recognition", "probability", 0.9),
        ],
    )
    comp = compose_groups([ctrl])
    assert comp.group_effectiveness[BooleanGroup.LEC_RESPONSE] is None
    assert comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR] is None


def test_currency_excluded_from_opeff_mean_but_control_attributed():
    """A CURRENCY-only Response control contributes to currency_subtractor_total
    and to contributing_control_ids, but yields no opeff (group eff None)."""
    ctrl = make_control(
        assignments=[("lec_resp_loss_reduction", "currency", 7500.0)],
    )
    comp = compose_groups([ctrl])
    assert comp.currency_subtractor_total == pytest.approx(7500.0)
    assert comp.group_effectiveness[BooleanGroup.LEC_RESPONSE] is None
    assert "c1" in comp.contributing_control_ids[BooleanGroup.LEC_RESPONSE]


def test_currency_subtractor_scaled_by_coverage_reliability():
    ctrl = make_control(
        assignments=[("lec_resp_loss_reduction", "currency", 10000.0)],
        coverage=0.8,
        reliability=0.5,
    )
    comp = compose_groups([ctrl])
    assert comp.currency_subtractor_total == pytest.approx(10000.0 * 0.8 * 0.5)
