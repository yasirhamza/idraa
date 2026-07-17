"""#130 Task 5 — engine composes per-group; Response weak-AND -> Loss Magnitude.

Full migration: the engine ALE path no longer accumulates per-control
domain->node multipliers. It consumes the shared `compose_groups` result and
maps each Boolean group's composed effectiveness E to its FAIR node multiplier
`1 - E·w` per `GROUP_NODE_MAPPING`. The LEC Response group is RE-ROUTED from
TEF/Vulnerability (the pre-#130 mis-mapping) to Loss Magnitude (primary +
secondary loss), gated on Detection presence (D8).

#328: re-pointed from the retired ``ControlAwareRiskCalculator
._apply_control_adjustments`` (param-dict application, no production analogue)
to the LIVE shared algebra ``_group_comp_to_node_multipliers`` — the exact
mapping the native engine consumes (``native_control_aware``), so these pins
now guard the production routing directly. The invariants are unchanged:

  * Response-only control reduces magnitude, never frequency/vuln (D4 re-route).
  * No Detection present => Response benefit gated to zero (D8 AND-gate).
  * Prevention OR-composes to TEF/Vuln (full migration; not per-control).
  * No controls => identity multipliers.
"""

from __future__ import annotations

import math

import pytest

from fair_cam.models.composition_topology import (
    GROUP_NODE_MAPPING,
    BooleanGroup,
)
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import compose_groups
from fair_cam.tests.risk_engine._helpers import make_control

_NODES = ("threat_event_frequency", "vulnerability", "primary_loss", "secondary_loss")


def test_no_controls_is_identity() -> None:
    """No controls -> identity multiplier on every node, zero subtractor."""
    comp = compose_groups([])
    m = _group_comp_to_node_multipliers(comp)
    assert all(m[node] == 1.0 for node in _NODES)
    assert comp.currency_subtractor_total == 0.0


def test_response_with_detection_reduces_magnitude_not_frequency() -> None:
    """A Response (+Detection gate) control reduces magnitude, leaves TEF/Vuln.

    The Detection group is a strict-AND across all three sub-functions; an
    absent member pads to 0.0 and collapses the AND, so all three must be
    present. We supply Visibility + Recognition (probability) at 0.9 and
    Monitoring (elapsed_time) at a value yielding a strong opeff. The exact
    pair effectiveness is read from the shared routine and the asserted
    magnitude multipliers are derived from it + GROUP_NODE_MAPPING weights —
    the point of the test is the RE-ROUTE (magnitude, not frequency), not a
    pinned scalar.
    """
    detection = make_control(
        control_id="det",
        assignments=[
            ("lec_det_visibility", "probability", 0.9),
            ("lec_det_monitoring", "elapsed_time", 1.0),
            ("lec_det_recognition", "probability", 0.9),
        ],
    )
    response = make_control(
        control_id="resp",
        assignments=[("lec_resp_resilience", "probability", 0.6)],
    )
    comp = compose_groups([detection, response])
    pair_eff = comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    assert pair_eff is not None and pair_eff > 0.0  # Detection present -> gate open

    m = _group_comp_to_node_multipliers(comp)
    # TEF / Vuln must be UNCHANGED by Response (the re-route fix).
    assert m["threat_event_frequency"] == 1.0
    assert m["vulnerability"] == pytest.approx(1.0)

    weights = GROUP_NODE_MAPPING[BooleanGroup.LEC_RESPONSE].weights
    assert m["secondary_loss"] == pytest.approx(1.0 - pair_eff * weights["secondary_loss"])
    assert m["primary_loss"] == pytest.approx(1.0 - pair_eff * weights["primary_loss"])


def test_response_without_detection_leaves_magnitude_unchanged() -> None:
    """D8 AND-gate: Response benefit requires Detection. No Detection => no benefit."""
    response = make_control(
        control_id="resp",
        assignments=[("lec_resp_resilience", "probability", 0.6)],
    )
    comp = compose_groups([response])
    # No Detection control: the Detection AND-group collapses to 0.0 (absent
    # members pad to 0.0), so the gated pair effectiveness is 0.0 (or None when
    # the Detection group itself has no members at all). Either way the magnitude
    # multiplier is identity (1 - 0·w = 1.0) — no Response benefit (D8).
    pair_eff = comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    assert pair_eff in (None, 0.0)

    m = _group_comp_to_node_multipliers(comp)
    assert all(m[node] == 1.0 for node in _NODES)


def test_prevention_or_composes_to_tef_and_vuln() -> None:
    """Full migration: Prevention OR-composes across sub-functions -> TEF/Vuln.

    Two Prevention sub-functions at probability 0.5 and 0.4 (single control):
      OR within group across sub-functions = 1 - (1-0.5)(1-0.4) = 0.7
      tef multiplier = 1 - 0.7*0.8 = 0.44
      vuln multiplier = 1 - 0.7*0.9 = 0.37
    """
    prevention = make_control(
        control_id="prev",
        assignments=[
            ("lec_prev_resistance", "probability", 0.5),
            ("lec_prev_deterrence", "probability", 0.4),
        ],
    )
    comp = compose_groups([prevention])
    e_prev = comp.group_effectiveness[BooleanGroup.LEC_PREVENTION]
    assert e_prev == pytest.approx(0.7)

    m = _group_comp_to_node_multipliers(comp)
    assert m["threat_event_frequency"] == pytest.approx(1 - 0.7 * 0.8)
    assert m["vulnerability"] == pytest.approx(1 - 0.7 * 0.9)
    # Magnitude untouched by Prevention.
    assert m["primary_loss"] == 1.0
    assert m["secondary_loss"] == 1.0


def test_currency_subtractor_flows_via_group_comp_total_not_multipliers() -> None:
    """Loss-Reduction currency subtractor flows through the group_comp total
    (#258); it is NOT a node multiplier. The sample-level application
    (scale -> subtract -> double-floor) is natively pinned in
    ``test_native_engine_subtractor``."""
    lr = make_control(
        control_id="lr",
        assignments=[("lec_resp_loss_reduction", "currency", 100_000.0)],
    )
    comp = compose_groups([lr])
    assert comp.currency_subtractor_total == pytest.approx(100_000.0)
    # No opeff Response member present -> every node multiplier stays identity.
    m = _group_comp_to_node_multipliers(comp)
    assert all(m[node] == 1.0 for node in _NODES)


def test_multi_sub_function_response_control_weak_ands_then_routes_to_magnitude() -> None:
    """#130 Task 7 audit regression — the case the bug-audit grep targets:
    a SINGLE control carrying MULTIPLE Response sub-functions (Event-Termination
    + Resilience) on the engine ALE path.

    Pre-#130 each Response sub-function fed an independent per-control
    multiplicative TEF/Vuln factor (the mis-route + double-count bug). Post-#130
    the two opeffs first OR-compose within their own sub-functions, then the
    LEC_RESPONSE group weak-ANDs ACROSS the two distinct sub-functions to ONE
    group effectiveness, which the engine routes to Loss Magnitude (gated on a
    Detection presence), never to frequency/vuln.

    Hand-math (cov=rel=1.0):
      Event-Termination (elapsed_time=32, τ=64): exp(-32/64)        = 0.6065307
      Resilience        (probability=0.6):                            0.6
      LEC_RESPONSE weak-AND (equal weights):    (0.6065307+0.6)/2   = 0.6032653
      Detection present -> gate open; pair eff  = AND(det, response)  (read live).
    """
    detection = make_control(
        control_id="det",
        assignments=[
            ("lec_det_visibility", "probability", 0.9),
            ("lec_det_monitoring", "elapsed_time", 1.0),
            ("lec_det_recognition", "probability", 0.9),
        ],
    )
    response_multi = make_control(
        control_id="resp_multi",
        assignments=[
            ("lec_resp_event_termination", "elapsed_time", 32.0),
            ("lec_resp_resilience", "probability", 0.6),
        ],
    )
    comp = compose_groups([detection, response_multi])

    # The two Response sub-functions weak-AND to a SINGLE group effectiveness
    # (not two independent per-control factors — the bug this audit guards).
    assert comp.group_effectiveness[BooleanGroup.LEC_RESPONSE] == pytest.approx(
        (math.exp(-32.0 / 64.0) + 0.6) / 2.0
    )

    pair_eff = comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    assert pair_eff is not None and pair_eff > 0.0  # Detection present -> gate open

    m = _group_comp_to_node_multipliers(comp)
    # Frequency / vuln UNCHANGED — the multi-sub-function Response no longer
    # touches TEF/Vuln (kills the pre-#130 per-assignment frequency reduction).
    assert m["threat_event_frequency"] == 1.0
    assert m["vulnerability"] == pytest.approx(1.0)
    # Magnitude reduced by 1 - pair_eff·w on both loss nodes.
    weights = GROUP_NODE_MAPPING[BooleanGroup.LEC_RESPONSE].weights
    assert m["secondary_loss"] == pytest.approx(1.0 - pair_eff * weights["secondary_loss"])
    assert m["primary_loss"] == pytest.approx(1.0 - pair_eff * weights["primary_loss"])
