"""Adapter contract tests for PR μ.1 two-branch math (CR-N4 + Arch-I2).

Pure in-memory tests — no DB fixture required. Controls are built the
same way as test_list_iteration.py: construct V3 Control + assignments
directly, set .assignments attribute, call _v3_to_fair_cam_control.

Three tests:
  1. N=4 mixed-unit (PROBABILITY / ELAPSED_TIME / CURRENCY /
     PERCENT_REDUCTION) full-tuple round-trip through
     _v3_to_fair_cam_control (CR-N4 strengthened).
  2. TIME_UNIT_EXCLUDED capability_value > 1 pass-through (Arch-I2 —
     must not clamp or skip; the new exponential math depends on the
     full numeric value reaching fair_cam).
  3. loss_reduction_per_event round-trip through
     _control_adjustment_to_dict (the per-run output channel; NOT the
     snapshot per plan-gate Arch-B1).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from idraa.models.control import Control as V3Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction
from idraa.services.run_executor import (
    _control_adjustment_to_dict,
    _v3_to_fair_cam_control,
)


def _make_control_with_assignments(
    assignments_spec: list[tuple[FairCamSubFunction, float, float, float]],
) -> V3Control:
    """Build an in-memory V3Control with the given assignments.

    assignments_spec: list of (sub_function, capability_value, coverage, reliability).
    Does NOT flush to DB — mirrors the pattern in test_list_iteration.py.
    """
    org_id = uuid.uuid4()
    ctrl_id = uuid.uuid4()
    ctrl = V3Control(
        id=ctrl_id,
        organization_id=org_id,
        name="Mixed-unit",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    ctrl.assignments = [
        ControlFunctionAssignment(
            id=uuid.uuid4(),
            organization_id=org_id,
            control_id=ctrl_id,
            sub_function=sub_fn,
            capability_value=cap,
            coverage=cov,
            reliability=rel,
        )
        for sub_fn, cap, cov, rel in assignments_spec
    ]
    return ctrl


def test_adapter_preserves_n4_mixed_unit_tuple_round_trip() -> None:
    """Each of 4 assignments preserves full (sub_function, cap, cov, rel) tuple
    through _v3_to_fair_cam_control (CR-N4 strengthened).

    Uses N=4 assignments spanning all four unit types present in FAIR-CAM:
      - PROBABILITY (LEC_PREV_RESISTANCE, cap=0.85)
      - ELAPSED_TIME (VMC_ID_CONTROL_MONITORING, cap=7.0 days — > 1, not clamped)
      - CURRENCY (LEC_RESP_LOSS_REDUCTION, cap=100_000.0 — > 1, not clamped)
      - PERCENT_REDUCTION (VMC_PREV_REDUCE_CHANGE_FREQ, cap=0.5)
    """
    specs: list[tuple[FairCamSubFunction, float, float, float]] = [
        (FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85, 0.85, 0.9),
        (FairCamSubFunction.VMC_ID_CONTROL_MONITORING, 7.0, 0.8, 0.8),
        (FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 100_000.0, 0.9, 0.95),
        (FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ, 0.5, 0.85, 0.85),
    ]
    ctrl = _make_control_with_assignments(specs)
    fair_cam_ctrl = _v3_to_fair_cam_control(ctrl)

    # Sort by sub_function value for deterministic comparison.
    expected = sorted(specs, key=lambda t: t[0].value)
    got = sorted(
        [
            (a.sub_function, a.capability_value, a.coverage, a.reliability)
            for a in fair_cam_ctrl.assignments
        ],
        key=lambda t: t[0].value,
    )
    # Use parallel enum comparison: fair_cam FairCamSubFunction and v3
    # FairCamSubFunction share the same string values — compare .value strings.
    for (exp_fn, exp_cap, exp_cov, exp_rel), (got_fn, got_cap, got_cov, got_rel) in zip(
        expected, got, strict=True
    ):
        assert got_fn.value == exp_fn.value, f"sub_function mismatch: {got_fn!r} != {exp_fn!r}"
        assert got_cap == pytest.approx(exp_cap), (
            f"capability_value mismatch for {exp_fn.value}: {got_cap} != {exp_cap}"
        )
        assert got_cov == pytest.approx(exp_cov), (
            f"coverage mismatch for {exp_fn.value}: {got_cov} != {exp_cov}"
        )
        assert got_rel == pytest.approx(exp_rel), (
            f"reliability mismatch for {exp_fn.value}: {got_rel} != {exp_rel}"
        )
    assert len(got) == 4, f"Expected 4 assignments; adapter returned {len(got)}"


def test_adapter_passes_time_unit_excluded_capability_value_gt_one_unchanged() -> None:
    """Arch-I2: TIME_UNIT_EXCLUDED capability_value (days, dollars) > 1 must
    NOT be clamped or skipped by _v3_to_fair_cam_control.

    The exponential elapsed-time math introduced in PR μ.1 receives the raw
    day/dollar value from the assignment. If the adapter clamps to [0, 1] or
    silently drops TIME_UNIT_EXCLUDED assignments, the τ exponent degrades
    to the safe-default path (0.5), hiding the bug.

    Checks two TIME_UNIT_EXCLUDED sub-functions:
      - VMC_ID_CONTROL_MONITORING (ELAPSED_TIME unit, cap=7.0 days)
      - LEC_RESP_LOSS_REDUCTION   (CURRENCY unit,       cap=100_000.0)
    """
    specs: list[tuple[FairCamSubFunction, float, float, float]] = [
        (FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85, 0.85, 0.9),  # anchor PROBABILITY
        (FairCamSubFunction.VMC_ID_CONTROL_MONITORING, 7.0, 0.8, 0.8),
        (FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 100_000.0, 0.9, 0.95),
        (FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ, 0.5, 0.85, 0.85),
    ]
    ctrl = _make_control_with_assignments(specs)
    fair_cam_ctrl = _v3_to_fair_cam_control(ctrl)

    asgn_by_sub_fn = {a.sub_function.value: a for a in fair_cam_ctrl.assignments}

    elapsed_asg = asgn_by_sub_fn.get(FairCamSubFunction.VMC_ID_CONTROL_MONITORING.value)
    assert elapsed_asg is not None, "VMC_ID_CONTROL_MONITORING assignment missing after adapter"
    assert elapsed_asg.capability_value == pytest.approx(7.0), (
        f"ELAPSED_TIME cap was clamped/changed: expected 7.0, got {elapsed_asg.capability_value}"
    )

    currency_asg = asgn_by_sub_fn.get(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION.value)
    assert currency_asg is not None, "LEC_RESP_LOSS_REDUCTION assignment missing after adapter"
    assert currency_asg.capability_value == pytest.approx(100_000.0), (
        f"CURRENCY cap was clamped/changed: expected 100_000.0, got {currency_asg.capability_value}"
    )


def test_loss_reduction_per_event_round_trips_through_dict_serializer() -> None:
    """ControlAdjustment.loss_reduction_per_event survives JSON serialization
    via _control_adjustment_to_dict (the per-run output channel; NOT the
    snapshot per plan-gate Arch-B1).

    Uses a non-trivial value (213_750.0) that would be silently zeroed if
    the serializer fell back to getattr default 0.0.
    """
    from fair_cam.models.risk_enhanced import ControlAdjustment

    adj = ControlAdjustment(
        control_id="c1",
        control_name="C1",
        loss_reduction_per_event=213_750.0,
    )
    d = _control_adjustment_to_dict(adj)
    assert d["loss_reduction_per_event"] == pytest.approx(213_750.0), (
        f"loss_reduction_per_event not preserved: got {d['loss_reduction_per_event']}"
    )
    # Ensure the key exists and is not None (regression guard: key was absent pre-PR-μ.1)
    assert "loss_reduction_per_event" in d
    assert d["loss_reduction_per_event"] is not None
