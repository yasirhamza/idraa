"""FairCamControlFunctionAssignment.capability_value accepts None (CR-B7, PR μ.1).

v3 ORM has been nullable since issue #90. fair_cam DTO must match the
nullability so the adapter doesn't lose or coerce NULL values.
"""

from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction


def test_null_capability_value_accepted() -> None:
    asg = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
        capability_value=None,
        coverage=0.8,
        reliability=0.8,
        degradation_rate=0.0,
    )
    assert asg.capability_value is None


def test_float_capability_value_still_accepted() -> None:
    asg = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.85,
        coverage=0.8,
        reliability=0.8,
        degradation_rate=0.0,
    )
    assert asg.capability_value == 0.85
