"""Task 3 (#130): the Layer-2 diagnostic delegates to the shared `compose_groups`.

`compose_group_effectiveness` must be sourced from `compose_groups` so it cannot
drift from the engine path (D2). This test pins the delegation contract: for a
multi-group, multi-control input (including a Detection+Response pair), every
`GroupEffectivenessReport` field the diagnostic emits must equal what the shared
`GroupComposition` carries:

  - `group_effectiveness`           == `comp.group_effectiveness[g]`
  - `sub_function_effectivenesses`  == `comp.sub_function_effectiveness[g]`
  - `contributing_control_ids`      == `comp.contributing_control_ids[g]`
  - `non_opeff_excluded_count` / `non_opeff_excluded_sub_functions`
                                    derived from `comp.currency_excluded[g]`

The pair group (`LEC_DETECTION_RESPONSE_PAIR`) is checked too: its effectiveness
is the shared routine's pair output, and its currency-excluded union is rebuilt
from its children.
"""

from __future__ import annotations

import pytest

from fair_cam.models.composition_topology import (
    PAIR_RECIPES,
    BooleanGroup,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.group_composition import build_group_effectiveness_reports, compose_groups
from fair_cam.tests.risk_engine._helpers import make_control


def _build_controls():
    # Spans LEC Prevention (OR), Detection (AND), Response (WEAK_AND incl.
    # CURRENCY), so the pair group LEC_DETECTION_RESPONSE_PAIR is exercised.
    prevention = make_control(
        control_id="prev1",
        assignments=[("lec_prev_resistance", "probability", 0.7)],
        coverage=0.8,
        reliability=0.8,
    )
    detection = make_control(
        control_id="det1",
        assignments=[
            ("lec_det_visibility", "probability", 0.8),
            ("lec_det_monitoring", "elapsed_time", 120.0),
            ("lec_det_recognition", "probability", 0.6),
        ],
        coverage=0.9,
        reliability=0.9,
    )
    response = make_control(
        control_id="resp1",
        assignments=[
            ("lec_resp_event_termination", "elapsed_time", 300.0),
            ("lec_resp_resilience", "probability", 0.7),
            ("lec_resp_loss_reduction", "currency", 50000.0),
        ],
        coverage=0.9,
        reliability=0.9,
    )
    return [prevention, detection, response]


def test_diagnostic_reports_equal_shared_compose_groups():
    controls = _build_controls()

    reports = build_group_effectiveness_reports(controls)
    comp = compose_groups(controls)

    # Leaf groups: every field traces to the shared GroupComposition.
    for group, rpt in reports.items():
        if group in PAIR_RECIPES:
            continue
        if rpt.group_effectiveness is None:
            assert comp.group_effectiveness[group] is None
        else:
            assert rpt.group_effectiveness == pytest.approx(comp.group_effectiveness[group])
        assert rpt.sub_function_effectivenesses == comp.sub_function_effectiveness[group]
        assert rpt.contributing_control_ids == comp.contributing_control_ids[group]
        excluded = comp.currency_excluded[group]
        assert rpt.non_opeff_excluded_count == len(excluded)
        assert rpt.non_opeff_excluded_sub_functions == sorted(
            {sf for sf, _ in excluded}, key=lambda x: x.value
        )


def test_diagnostic_pair_group_equals_shared_pair_output():
    controls = _build_controls()

    reports = build_group_effectiveness_reports(controls)
    comp = compose_groups(controls)

    pair = reports[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    shared_pair = comp.group_effectiveness[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    if shared_pair is None:
        assert pair.group_effectiveness is None
    else:
        assert pair.group_effectiveness == pytest.approx(shared_pair)

    # Pair currency-excluded fields are the union of its two children's.
    left, right = PAIR_RECIPES[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR]
    expected_sfs = sorted(
        set(reports[left].non_opeff_excluded_sub_functions)
        | set(reports[right].non_opeff_excluded_sub_functions),
        key=lambda x: x.value,
    )
    assert pair.non_opeff_excluded_sub_functions == expected_sfs
    assert pair.non_opeff_excluded_count == (
        reports[left].non_opeff_excluded_count + reports[right].non_opeff_excluded_count
    )
    # Response arm contributes a CURRENCY (Loss Reduction) sub-function.
    assert FairCamSubFunction.LEC_RESP_LOSS_REDUCTION in pair.non_opeff_excluded_sub_functions
