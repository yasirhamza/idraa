"""Task 0 (#130): smoke test for the shared test `_helpers` factories.

`make_control` / `make_risk_parameters` wrap the real fair_cam constructors so
Tasks 2/5/6 build inputs uniformly (plan-gate B-spec-2). Assignment tuples are
`(sub_function_str, unit_str, capability_value)`.
"""

import pytest

from fair_cam.models.control import Control, FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.tests.risk_engine._helpers import make_control


def test_make_control_builds_real_control_with_assignments():
    ctrl = make_control(
        assignments=[
            ("lec_resp_event_termination", "elapsed_time", 300.0),
            ("lec_resp_resilience", "probability", 0.4),
            ("lec_resp_loss_reduction", "currency", 5000.0),
        ]
    )
    assert isinstance(ctrl, Control)
    assert ctrl.control_id == "c1"
    assert len(ctrl.assignments) == 3
    assert all(isinstance(a, FairCamControlFunctionAssignment) for a in ctrl.assignments)
    assert ctrl.assignments[0].sub_function == FairCamSubFunction.LEC_RESP_EVENT_TERMINATION
    assert ctrl.assignments[0].capability_value == pytest.approx(300.0)
    # default coverage/reliability are 1.0 so opeff reflects raw capability
    assert ctrl.assignments[1].coverage == pytest.approx(1.0)
    assert ctrl.assignments[1].reliability == pytest.approx(1.0)


def test_make_control_custom_id_coverage_reliability():
    ctrl = make_control(
        control_id="resp_only",
        assignments=[("lec_resp_resilience", "probability", 0.7)],
        coverage=0.9,
        reliability=0.9,
    )
    assert ctrl.control_id == "resp_only"
    assert ctrl.assignments[0].coverage == pytest.approx(0.9)
    assert ctrl.assignments[0].reliability == pytest.approx(0.9)
