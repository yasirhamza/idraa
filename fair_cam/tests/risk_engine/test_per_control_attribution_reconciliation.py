"""#130 Task 5b — per-control ControlAdjustment attribution reconciliation (D9).

Each per-control `ControlAdjustment` the engine produces is its STANDALONE
ISOLATED effect, derived closed-form from the SAME shared `compose_groups`
routine the engine ALE path consumes (`compose_groups([that_control])`) — NOT
from the retired `effectiveness.py` per-control domain->node multiplier branch.
This keeps the #203 attribution matrix / reports populated without
re-introducing engine<->view-model drift, and without a per-control FairModel
re-run (NEW-3).

Contract assertions:
  * A Response-only control WITH a Detection control present yields a NON-ZERO
    matrix-relevant attribution post-reroute (the gate is open). Standalone (no
    Detection) it is $0 — Detection-gated (R3 N-arch-B; documented in the matrix
    caption). [N.B. attribution is per-control STANDALONE: compose_groups([resp])
    has no Detection, so a lone Response control's standalone $ is 0 by design —
    the engine ALE still benefits when the Detection control is co-present.]
  * Per-control multipliers equal the group-composed node multipliers (derive
    from compose_groups([control]), not stale effectiveness.py multipliers).
  * A combined Event-Termination + Loss-Reduction control keeps the CURRENCY
    subtractor OUT of risk_reduction_value (it stays in loss_reduction_per_event)
    so cell = risk_reduction_value + loss_reduction_per_event×LEF never
    double-counts (R3 N-arch-A; test_risk_reduction_value_excludes_subtractor
    stays green).
"""

from __future__ import annotations

import pytest

from fair_cam.composition import and_compose
from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.models.composition_topology import BooleanGroup
from fair_cam.risk_engine.control_attribution import build_control_adjustment
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import compose_groups
from fair_cam.tests.risk_engine._helpers import make_control

BASE_TEF = 100.0
BASE_VULN = 0.5
BASE_PRIMARY = 1_000_000.0
BASE_SECONDARY = 500_000.0


def _adj(control):
    # #328: re-pointed from the retired calculator's verbatim-delegating
    # _build_control_adjustment to the live closed-form free function.
    return build_control_adjustment(
        control, ControlEffectivenessCalculator(), BASE_TEF, BASE_VULN, BASE_PRIMARY, BASE_SECONDARY
    )


def test_prevention_control_attribution_matches_group_composition() -> None:
    """A Prevention control's per-control multipliers == compose_groups([it])
    node multipliers (NOT stale effectiveness.py per-control math)."""
    ctrl = make_control(
        control_id="prev",
        assignments=[("lec_prev_resistance", "probability", 0.7)],
    )
    comp = compose_groups([ctrl])
    expected = _group_comp_to_node_multipliers(comp)
    adj = _adj(ctrl)
    assert adj.threat_event_frequency_multiplier == pytest.approx(
        expected["threat_event_frequency"]
    )
    assert adj.vulnerability_multiplier == pytest.approx(expected["vulnerability"])
    assert adj.primary_loss_multiplier == pytest.approx(expected["primary_loss"])
    assert adj.secondary_loss_multiplier == pytest.approx(expected["secondary_loss"])
    # Non-zero standalone attribution (Prevention has a standalone node).
    assert adj.risk_reduction_value > 0.0


def test_response_only_standalone_attribution_is_detection_gated_zero() -> None:
    """R3 N-arch-B: a lone Response control's STANDALONE attribution is $0 —
    Detection-gated. compose_groups([resp]) has no Detection -> pair eff
    None/0 -> magnitude multiplier identity -> risk_reduction_value 0."""
    resp = make_control(
        control_id="resp",
        assignments=[("lec_resp_resilience", "probability", 0.8)],
    )
    adj = _adj(resp)
    assert adj.secondary_loss_multiplier == pytest.approx(1.0)
    assert adj.primary_loss_multiplier == pytest.approx(1.0)
    assert adj.risk_reduction_value == pytest.approx(0.0)


def test_response_attribution_nonzero_when_detection_co_present() -> None:
    """A control carrying BOTH Detection and Response sub-functions has the gate
    open on its own standalone composition -> non-zero magnitude attribution."""
    combo = make_control(
        control_id="combo",
        assignments=[
            ("lec_det_visibility", "probability", 0.9),
            ("lec_det_monitoring", "elapsed_time", 1.0),
            ("lec_det_recognition", "probability", 0.9),
            ("lec_resp_resilience", "probability", 0.8),
        ],
    )
    adj = _adj(combo)
    assert adj.secondary_loss_multiplier < 1.0  # magnitude reduced
    assert adj.primary_loss_multiplier < 1.0
    assert adj.threat_event_frequency_multiplier == pytest.approx(1.0)  # not frequency
    assert adj.risk_reduction_value > 0.0


def test_vmc_id_correction_vulnerability_applied_once_not_tripled() -> None:
    """Slice 2 (#439) D1 retirement pin — supersedes the pre-Slice-2 #130
    reviewer-BLOCKER pin (which asserted the §4 VMC effect reached vulnerability
    exactly once via the AND-pair, `1 - E_pair·0.3`, rather than tripled).

    D1 retires `VMC_IDENTIFICATION_CORRECTION_PAIR`'s direct vulnerability
    target entirely (`GROUP_NODE_MAPPING` now carries empty targets/weights for
    it), so a fully-exercised VMC Id∧Corr control now produces IDENTITY on
    vulnerability regardless of the (still non-zero, still AND-composed) pair
    effectiveness — VMC value flows through the kappa reliability coupling
    instead (Task 2+), not yet wired into the engine as of this task.
    """
    vmc = make_control(
        control_id="vmc_id_corr",
        assignments=[
            ("vmc_id_threat_intelligence", "probability", 1.0),
            ("vmc_id_control_monitoring", "probability", 1.0),
            ("vmc_corr_treatment_selection", "probability", 1.0),
            ("vmc_corr_implementation", "elapsed_time", 5.0),
        ],
    )
    comp = compose_groups([vmc])
    id_eff = comp.group_effectiveness[BooleanGroup.VMC_IDENTIFICATION]
    corr_eff = comp.group_effectiveness[BooleanGroup.VMC_CORRECTION]
    assert id_eff is not None and corr_eff is not None
    # Precondition: the pair IS non-zero (independent hand-derivation).
    expected_pair_eff = and_compose([id_eff, corr_eff])
    assert expected_pair_eff > 0.0

    adj = _adj(vmc)
    # Retired direct channel -> identity (D1), regardless of pair effectiveness.
    assert adj.vulnerability_multiplier == pytest.approx(1.0)
    # VMC does not touch frequency / magnitude nodes (unchanged by Slice 2).
    assert adj.threat_event_frequency_multiplier == pytest.approx(1.0)
    assert adj.primary_loss_multiplier == pytest.approx(1.0)
    assert adj.secondary_loss_multiplier == pytest.approx(1.0)


def test_vmc_single_leaf_yields_no_vulnerability_benefit() -> None:
    """A VMC control with ONLY Identification (no Correction) reduces nothing:
    the §4 Identification∧Correction AND collapses (pair eff None → identity),
    mirroring Detection-without-Response. This pins the leaf-empty-targets fix —
    under the buggy code the lone ID leaf still applied ×0.3."""
    vmc = make_control(
        control_id="vmc_id_only",
        assignments=[
            ("vmc_id_threat_intelligence", "probability", 1.0),
            ("vmc_id_control_monitoring", "probability", 1.0),
        ],
    )
    comp = compose_groups([vmc])
    assert comp.group_effectiveness[BooleanGroup.VMC_IDENTIFICATION] == pytest.approx(1.0)
    # Correction absent -> AND group collapses to 0.0 -> pair eff 0.0 (not None,
    # since both child groups DO have opeff members; the absent Correction member
    # pads to 0.0 per the AND-parity rule). Either way the pair contributes no
    # vulnerability benefit.
    assert comp.group_effectiveness[BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR] == (
        pytest.approx(0.0)
    )
    adj = _adj(vmc)
    assert adj.vulnerability_multiplier == pytest.approx(1.0)
    assert adj.risk_reduction_value == pytest.approx(0.0)


def test_event_termination_plus_loss_reduction_excludes_subtractor() -> None:
    """R3 N-arch-A: a combined Event-Termination + Loss-Reduction control keeps
    the currency subtractor OUT of risk_reduction_value (it lives in
    loss_reduction_per_event) so the matrix cell never double-counts.

    No Detection present -> the ET weak-AND magnitude benefit is gated to zero,
    so risk_reduction_value (multipliers-only) is 0; the $250k subtractor shows
    up ONLY in loss_reduction_per_event."""
    ctrl = make_control(
        control_id="et_lr",
        assignments=[
            ("lec_resp_event_termination", "elapsed_time", 5.0),
            ("lec_resp_loss_reduction", "currency", 250_000.0),
        ],
    )
    adj = _adj(ctrl)
    assert adj.loss_reduction_per_event == pytest.approx(250_000.0)
    # Subtractor NOT in risk_reduction_value (gate closed -> multipliers-only 0).
    assert adj.risk_reduction_value == pytest.approx(0.0)
