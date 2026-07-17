"""Layer 2 group composition + Scenario F (all-time-unit g_eff = None).

Spec §3.2 + §5.2 + §9.6.
"""

import pytest

from fair_cam.models.composition_topology import BooleanGroup, GroupType
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.group_composition import build_group_effectiveness_reports
from fair_cam.tests.risk_engine._helpers import make_control


def test_single_control_single_assignment_lec_prevention():
    c = make_control(
        control_id="C1",
        assignments=[("lec_prev_resistance", "probability", 0.7)],
        coverage=0.8,
        reliability=0.8,
    )

    reports = build_group_effectiveness_reports([c])

    prev = reports[BooleanGroup.LEC_PREVENTION]
    assert prev.group_type == GroupType.OR
    assert prev.sub_function_effectivenesses == {
        FairCamSubFunction.LEC_PREV_RESISTANCE: pytest.approx(0.448, abs=1e-9),
    }
    # OR with only Resistance present (Avoidance, Deterrence absent -> s_i = 0):
    # 1 - (1-0)(1-0)(1-0.448) = 1 - 0.552 = 0.448
    assert prev.group_effectiveness == pytest.approx(0.448, abs=1e-9)
    assert prev.contributing_control_ids == ["C1"]
    assert prev.non_opeff_excluded_count == 0


def test_two_controls_same_subfunction_or_redundancy():
    c1 = make_control(
        control_id="C1",
        assignments=[("lec_prev_resistance", "probability", 0.7)],
        coverage=0.8,
        reliability=0.8,
    )
    c2 = make_control(
        control_id="C2",
        assignments=[("lec_prev_resistance", "probability", 1.0)],
        coverage=0.5,
        reliability=1.0,
    )

    reports = build_group_effectiveness_reports([c1, c2])

    prev = reports[BooleanGroup.LEC_PREVENTION]
    # within-sub-function OR: 1 - (1-0.448)(1-0.5) = 0.724
    assert prev.sub_function_effectivenesses[
        FairCamSubFunction.LEC_PREV_RESISTANCE
    ] == pytest.approx(0.724, abs=1e-9)
    # across-sub-function OR (others absent -> 0): 1 - 1 * 1 * (1-0.724) = 0.724
    assert prev.group_effectiveness == pytest.approx(0.724, abs=1e-9)
    assert set(prev.contributing_control_ids) == {"C1", "C2"}


def test_detection_and_trio_collapses_with_zero():
    c = make_control(
        control_id="C1",
        assignments=[("lec_det_visibility", "probability", 1.0)],
        coverage=0.5,
        reliability=1.0,
    )

    reports = build_group_effectiveness_reports([c])

    det = reports[BooleanGroup.LEC_DETECTION]
    assert det.group_type == GroupType.AND
    # Only Visibility present, Monitoring + Recognition absent (s_i=0 for absent)
    assert det.group_effectiveness == 0.0


def test_scenario_f_response_group_elapsed_time_now_included():
    """Re-pin of Spec §9.6 Scenario F (originally PR μ.1 Task 6; updated
    issue #131 2026-05-16).

    Pre-PR-μ.1: all three LEC_RESPONSE sub-functions were TIME_UNIT_EXCLUDED,
    so the group returned g_eff=None and non_opeff_excluded_count==3.

    Post-PR-μ.1 / pre-issue-#131: ET + Resilience were both ELAPSED_TIME and
    contributed opeff via two-branch math.

    Post-issue-#131: LEC_RESP_RESILIENCE is now UnitType.PROBABILITY (no
    primary citation supported its old τ=33d "3-week BCM heuristic").
    ET (still ELAPSED_TIME, τ=64 post-recalibration) contributes via the
    ELAPSED_TIME branch; Resilience contributes via the PROBABILITY
    branch. Only CURRENCY (LEC_RESP_LOSS_REDUCTION) is excluded.
    WEAK_AND still has 2 operands → g_eff is a real float.

    Expected values (cov=0.9, rel=0.9):
      ET:        exp(-300/64) * 0.9 * 0.9 ≈ 0.007460  (ELAPSED_TIME branch)
      Resilience: 0.7 * 0.9 * 0.9 = 0.567             (PROBABILITY branch)
      WEAK_AND(equal weights): (0.007460 + 0.567) / 2 ≈ 0.287230
    """
    # Issue #131: Resilience reclassified ELAPSED_TIME → PROBABILITY; cap is
    # now in [0, 1] (was a day-count). Pick 0.7 — a plausible
    # "70% effectiveness" value.
    c = make_control(
        control_id="C1",
        assignments=[
            ("lec_resp_event_termination", "elapsed_time", 300.0),
            ("lec_resp_resilience", "probability", 0.7),
            ("lec_resp_loss_reduction", "currency", 50000.0),
        ],
        coverage=0.9,
        reliability=0.9,
    )

    reports = build_group_effectiveness_reports([c])

    resp = reports[BooleanGroup.LEC_RESPONSE]
    assert resp.group_type == GroupType.WEAK_AND
    # ET (ELAPSED_TIME) + Resilience (PROBABILITY) both contribute; only CURRENCY excluded.
    assert resp.group_effectiveness == pytest.approx(0.287230, abs=1e-5)
    assert resp.non_opeff_excluded_count == 1
    assert resp.non_opeff_excluded_sub_functions == [FairCamSubFunction.LEC_RESP_LOSS_REDUCTION]
    assert set(resp.sub_function_effectivenesses.keys()) == {
        FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        FairCamSubFunction.LEC_RESP_RESILIENCE,
    }


def test_mixed_time_unit_and_probability_in_same_group():
    """PR μ.1 Task 6 re-pin; updated issue #131 (LEC_DET_MONITORING τ 280→194).

    Pre-PR-μ.1: LEC_DET_MONITORING was excluded (non_opeff_excluded_count=1).
    Post-PR-μ.1: Monitoring contributes opeff via two-branch math.
    Post-issue-#131: same branch, new τ (194 instead of 280):
      Monitoring: exp(-120/194) * 0.9 * 0.9 ≈ 0.436364
      Visibility: 0.8 * 0.9 * 0.9 = 0.648
    AND-compose([Visibility, Monitoring, absent Recognition=0.0]) = 0.0 still —
    absent sub-function collapses AND to 0.
    non_opeff_excluded_count changes 1 → 0 (no CURRENCY subs in this group).
    """
    c = make_control(
        control_id="C1",
        assignments=[
            ("lec_det_visibility", "probability", 0.8),
            ("lec_det_monitoring", "elapsed_time", 120.0),
        ],
        coverage=0.9,
        reliability=0.9,
    )

    reports = build_group_effectiveness_reports([c])

    det = reports[BooleanGroup.LEC_DETECTION]
    # AND collapses to 0 because Recognition is absent (gets 0.0 default).
    assert det.group_effectiveness == 0.0
    # No CURRENCY subs in this group → 0 excluded.
    assert det.non_opeff_excluded_count == 0
    assert det.non_opeff_excluded_sub_functions == []
    # Both Visibility and Monitoring are now in sub_function_effectivenesses.
    assert FairCamSubFunction.LEC_DET_VISIBILITY in det.sub_function_effectivenesses
    assert FairCamSubFunction.LEC_DET_MONITORING in det.sub_function_effectivenesses
    assert det.sub_function_effectivenesses[FairCamSubFunction.LEC_DET_MONITORING] == pytest.approx(
        0.436364, abs=1e-5
    )


def test_vmc_identification_low_probability_contributes_near_zero():
    """PR μ.1 Task 6 re-pin of paranoid-review fix N3; updated issue #131
    (VMC_ID_THREAT_INTELLIGENCE + VMC_ID_CONTROL_MONITORING reclassified
    ELAPSED_TIME → PROBABILITY because the previous v3-default τ values
    lacked primary citations). Re-pinned again for Slice 2 (#439) D3:
    VMC_IDENTIFICATION's operator changed AND->OR (v3 arithmetic choice — TI
    and control-monitoring cover DIFFERENT variance sources, §4.2 p.25;
    coverage-union approximated by OR; see
    fair_cam/tests/test_composition_topology.py::test_meta_group_operators_slice2).

    Pre-PR-μ.1: VMC_IDENTIFICATION's two ELAPSED_TIME members were excluded,
    so g_eff=None and non_opeff_excluded_count==2.

    Post-issue-#131 / post-Slice-2: Both subs are PROBABILITY-typed and
    OR-composed. Use deliberately low capability values (0.01) to preserve the
    test's "near zero" intent:
      ti: 0.01 * 0.8 * 0.9 = 0.0072
      cm: 0.01 * 0.8 * 0.9 = 0.0072
      OR-compose([0.0072, 0.0072]) = 1 - (1-0.0072)^2 ≈ 0.01434816 (not None, ≈0).
    """
    c = make_control(
        control_id="C1",
        assignments=[
            ("vmc_id_threat_intelligence", "probability", 0.01),
            ("vmc_id_control_monitoring", "probability", 0.01),
        ],
        coverage=0.8,
        reliability=0.9,
    )

    reports = build_group_effectiveness_reports([c])

    vmc_id = reports[BooleanGroup.VMC_IDENTIFICATION]
    assert vmc_id.group_type == GroupType.OR  # Slice 2 D3
    # Low PROBABILITY → OR-compose gives ≈0 (not None).
    assert vmc_id.group_effectiveness == pytest.approx(0.01434816, abs=1e-7)
    # No CURRENCY subs in this group → 0 excluded.
    assert vmc_id.non_opeff_excluded_count == 0
    assert vmc_id.non_opeff_excluded_sub_functions == []
    # Both subs now contribute (with ≈0 opeff).
    assert set(vmc_id.sub_function_effectivenesses.keys()) == {
        FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
        FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
    }
