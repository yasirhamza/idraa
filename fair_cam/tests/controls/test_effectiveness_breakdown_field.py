"""Issue #129 §6 - ControlAdjustment.breakdown per-unit-type shape.

Verifies that the breakdown list contains one dict per assignment with
unit-conditional field population:
- ELAPSED_TIME: tau_canonical, t_used, capability_was_null, opeff populated
- CURRENCY: loss_reduction_per_event populated
- PROBABILITY / PERCENT_REDUCTION: capability_value_in only

Writer-side adapter iteration contract: N=4 assignments -> 4 breakdown entries.
"""

from __future__ import annotations

import pytest

from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.models.control import Control, FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction


def _make_assignment(sf, capability_value):
    return FairCamControlFunctionAssignment(
        sub_function=sf,
        capability_value=capability_value,
        coverage=0.8,
        reliability=0.8,
    )


def test_breakdown_per_unit_type_shape():
    """One breakdown dict per assignment; fields populated per unit type."""
    control = Control(
        control_id="c1",
        name="Test",
        assignments=[
            _make_assignment(FairCamSubFunction.LEC_DET_MONITORING, 7.0),
            _make_assignment(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 5000),
            _make_assignment(FairCamSubFunction.LEC_PREV_AVOIDANCE, 0.7),
            _make_assignment(FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ, 0.5),
        ],
    )
    calc = ControlEffectivenessCalculator()
    adjustment = calc.calculate_control_risk_adjustment(
        control=control,
        base_threat_frequency=1.0,
        base_vulnerability=0.5,
        base_primary_loss=10000,
        base_secondary_loss=5000,
    )

    assert len(adjustment.breakdown) == 4, "one breakdown dict per assignment"

    by_sf = {b["sub_function"]: b for b in adjustment.breakdown}

    et = by_sf["lec_det_monitoring"]
    assert et["unit"] == "elapsed_time"
    assert et["capability_value_in"] == 7.0
    assert et["tau_canonical"] is not None
    assert et["t_used"] == 7.0
    assert et["capability_was_null"] is False
    assert et["opeff"] is not None
    assert et["loss_reduction_per_event"] is None

    cur = by_sf["lec_resp_loss_reduction"]
    assert cur["unit"] == "currency"
    assert cur["loss_reduction_per_event"] == 5000 * 0.8 * 0.8
    assert cur["tau_canonical"] is None
    assert cur["t_used"] is None
    assert cur["opeff"] is None

    prob = by_sf["lec_prev_avoidance"]
    assert prob["unit"] == "probability"
    assert prob["capability_value_in"] == 0.7
    assert prob["tau_canonical"] is None
    assert prob["opeff"] is None
    assert prob["loss_reduction_per_event"] is None

    pct = by_sf["vmc_prev_reduce_change_freq"]
    assert pct["unit"] == "percent_reduction"
    assert pct["capability_value_in"] == 0.5


def test_breakdown_null_capability_records_was_null():
    """NULL capability on ELAPSED_TIME records capability_was_null=True.

    For NULL capability on ELAPSED_TIME, compute_assignment_opeff_two_branch
    returns the 0.5 NULL-anchor: 0.5 * coverage * reliability (issue #131
    Arch3-N1; the standalone _null_safe_default helper was folded into
    compute_assignment_part by Slice 2 #439). With coverage=reliability=0.8,
    opeff = 0.32.
    """
    control = Control(
        control_id="c1",
        name="Test",
        assignments=[_make_assignment(FairCamSubFunction.LEC_DET_MONITORING, None)],
    )
    calc = ControlEffectivenessCalculator()
    adj = calc.calculate_control_risk_adjustment(
        control=control,
        base_threat_frequency=1.0,
        base_vulnerability=0.5,
        base_primary_loss=10000,
        base_secondary_loss=5000,
    )

    assert adj.breakdown[0]["capability_value_in"] is None
    assert adj.breakdown[0]["capability_was_null"] is True
    assert adj.breakdown[0]["t_used"] is None
    assert adj.breakdown[0]["opeff"] == pytest.approx(0.5 * 0.8 * 0.8)
