"""FairCamControlFunctionAssignment dataclass — per-assignment effectiveness
shape mirroring v3's ControlFunctionAssignment ORM (PR ι spec §7.1.1)."""

from datetime import datetime

import pytest

from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction


def test_minimum_construction():
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.85,
        coverage=0.88,
        reliability=0.92,
    )
    assert a.sub_function == FairCamSubFunction.LEC_PREV_RESISTANCE
    assert a.capability_value == 0.85
    assert a.coverage == 0.88
    assert a.reliability == 0.92
    assert a.measured_at is None
    assert a.confirmed_by_user_at is None
    assert a.degradation_rate == 0.0


def test_full_construction():
    now = datetime(2026, 5, 2)
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_DET_VISIBILITY,
        capability_value=0.75,
        coverage=0.88,
        reliability=0.92,
        measured_at=now,
        confirmed_by_user_at=now,
        degradation_rate=0.005,
    )
    assert a.measured_at == now
    assert a.confirmed_by_user_at == now
    assert a.degradation_rate == 0.005


def test_coverage_endpoints_accepted():
    """coverage=0.0 and coverage=1.0 are valid (closed interval)."""
    for cov in [0.0, 1.0]:
        a = FairCamControlFunctionAssignment(
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=0.85,
            coverage=cov,
            reliability=0.92,
        )
        assert a.coverage == cov


def test_coverage_just_outside_rejected():
    """coverage just over 1 or just under 0 raises."""
    for cov in [1.0001, -0.0001]:
        with pytest.raises(ValueError, match="coverage"):
            FairCamControlFunctionAssignment(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.85,
                coverage=cov,
                reliability=0.92,
            )


def test_reliability_endpoints_accepted():
    """reliability=0.0 and reliability=1.0 are valid (closed interval)."""
    for rel in [0.0, 1.0]:
        a = FairCamControlFunctionAssignment(
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=0.85,
            coverage=0.88,
            reliability=rel,
        )
        assert a.reliability == rel


def test_reliability_just_outside_rejected():
    """reliability just over 1 or just under 0 raises."""
    for rel in [1.0001, -0.0001]:
        with pytest.raises(ValueError, match="reliability"):
            FairCamControlFunctionAssignment(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.85,
                coverage=0.88,
                reliability=rel,
            )


def test_capability_value_unbounded_for_time_unit_subfunctions():
    """ELAPSED_TIME and CURRENCY sub-functions have capability_value in
    natural units (seconds, dollars). PR κ tolerates this; PR μ activates
    time-unit normalization. The constructor does NOT bound capability_value
    to [0, 1] — only coverage and reliability are bounded."""
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        capability_value=300.0,  # 300 seconds — valid for elapsed-time unit
        coverage=0.9,
        reliability=0.9,
    )
    assert a.capability_value == 300.0
