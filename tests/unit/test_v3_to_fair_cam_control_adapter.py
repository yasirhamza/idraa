"""v3 Control ORM row → fair_cam Control dataclass adapter (PR iota shape).

The adapter is a private helper inside services/run_executor.py. We test it
via direct import of the function to validate field mapping and safe-default
branches.

PR iota bridge spec (spec §8.3, OQ3+OQ7):
  (a) Empty assignments -> safe default (0.5, 0.5, 0.5)
  (b) ELAPSED_TIME or CURRENCY unit -> safe default
  (c) NULL capability_value -> raises ValueError (T11 paranoid-review fix S3)
  (d) Otherwise: pass capability_value as control_strength, coverage/reliability
      through TRANSITIONALLY during PR iota -> PR kappa window.

The adapter reads from Control.assignments (selectin-loaded relationship) —
not from the dropped flat fields (control_strength / control_reliability /
control_coverage / function).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fair_cam.models.control import ControlDomain as FairCamControlDomain

from idraa.models.control import Control
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
)
from idraa.services.run_executor import _v3_to_fair_cam_control


def _make_v3_control(
    *,
    name: str = "Test Control",
    type_: ControlType = ControlType.TECHNICAL,
) -> Control:
    """Build an in-memory Control with NO assignments (assignments=[]).

    We bypass the SQLAlchemy relationship setter via object.__setattr__ because
    the `assignments` relationship has backref events that reject non-ORM objects.
    The adapter only reads ``ctrl.assignments`` as a list — no session or FK
    machinery needed for unit testing.

    Issue #90 task 2 dropped Control.domain — the representative domain is
    now derived from assignments[0].sub_function downstream. Tests that need
    a specific domain attach an assignment with the appropriate sub_function
    via ``_attach_assignment(sub_function=...)``.
    """
    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name=name,
        type=type_,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    # Bypass the ORM instrumented-list descriptor to avoid backref-event machinery.
    # Writing directly into __dict__ skips the SQLAlchemy attribute interceptor.
    ctrl.__dict__["assignments"] = []
    return ctrl


def _attach_assignment(
    ctrl: Control,
    *,
    sub_function: FairCamSubFunction = FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    capability_value: float | None = 0.7,
    coverage: float = 0.8,
    reliability: float = 0.85,
) -> Any:
    """Attach a mock CFA namespace to ctrl.assignments (no DB session needed).

    Uses object.__setattr__ to bypass the SQLAlchemy relationship setter
    (same trick as _make_v3_control).  The adapter only iterates over the list
    and reads attribute names — it never calls ORM session or backref methods.
    """
    from types import SimpleNamespace

    asgn = SimpleNamespace(
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
    )
    # Write directly into __dict__ to bypass the SQLAlchemy instrumented-list
    # descriptor.  SimpleNamespace has no _sa_instance_state so the normal
    # assignment triggers a backref-event that fails.  The adapter only reads
    # the list via iteration + attribute access — no ORM session needed.
    ctrl.__dict__["assignments"] = [asgn]
    return asgn


# ---------------------------------------------------------------------------
# Branch (a): empty assignments → safe default
# ---------------------------------------------------------------------------


# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_adapter_empty_assignments_returns_safe_default

# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_adapter_preserves_id_as_string

# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_adapter_preserves_name


# ---------------------------------------------------------------------------
# Branch (b): ELAPSED_TIME / CURRENCY unit → safe default
# ---------------------------------------------------------------------------


# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_adapter_elapsed_time_unit_returns_safe_default


# ---------------------------------------------------------------------------
# Branch (c): NULL capability_value → pass-through (issue #209)
# ---------------------------------------------------------------------------


def test_adapter_null_capability_passes_through_without_raising() -> None:
    """Issue #209: capability_value=None is passed straight through, NOT rejected.

    The stale executor NULL-reject gate (paranoid-review fix S3 / T11 PR κ) is
    deleted. fair_cam already handles NULL via its documented
    ``opeff(median)=0.5`` anchor (``_null_safe_default`` = 0.5 * coverage *
    reliability), so the adapter must let NULL through to fair_cam rather than
    hard-failing the run. The clear-capability UI modal promises this graceful
    midpoint fallback.
    """
    v3 = _make_v3_control()
    _attach_assignment(v3, capability_value=None, coverage=0.8, reliability=0.8)
    fc = _v3_to_fair_cam_control(v3)
    assert len(fc.assignments) == 1
    asn = fc.assignments[0]
    assert asn.capability_value is None
    assert asn.coverage == 0.8
    assert asn.reliability == 0.8


# ---------------------------------------------------------------------------
# Branch (d): probability/percent-reduction unit with non-NULL capability
# ---------------------------------------------------------------------------


def test_adapter_passes_through_capability_for_probability_unit() -> None:
    """Branch (d): probability unit with non-NULL → pass-through."""
    v3 = _make_v3_control()
    _attach_assignment(v3, capability_value=0.42, coverage=0.55, reliability=0.91)
    fc = _v3_to_fair_cam_control(v3)
    assert fc.control_strength == 0.42
    assert fc.control_coverage == 0.55
    assert fc.control_reliability == 0.91


# ---------------------------------------------------------------------------
# Domain mapping
# ---------------------------------------------------------------------------


def test_adapter_handles_loss_event_domain() -> None:
    v3 = _make_v3_control()
    _attach_assignment(v3, sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE)
    fc = _v3_to_fair_cam_control(v3)
    assert fc.domain == FairCamControlDomain.LOSS_EVENT


def test_adapter_handles_variance_management_domain() -> None:
    v3 = _make_v3_control()
    _attach_assignment(v3, sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB)
    fc = _v3_to_fair_cam_control(v3)
    assert fc.domain == FairCamControlDomain.VARIANCE_MANAGEMENT


def test_adapter_handles_decision_support_domain() -> None:
    v3 = _make_v3_control()
    _attach_assignment(v3, sub_function=FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS)
    fc = _v3_to_fair_cam_control(v3)
    assert fc.domain == FairCamControlDomain.DECISION_SUPPORT


# ---------------------------------------------------------------------------
# Default timing fields
# ---------------------------------------------------------------------------


# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_adapter_provides_default_response_time

# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_adapter_provides_default_recovery_time

# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_adapter_provides_default_degradation_rate


# ---------------------------------------------------------------------------
# Branch (d) defense-in-depth: out-of-bounds reliability/coverage (F15)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_field,bad_value,err_match",
    [
        ("reliability", 1.5, r"reliability=1\.5"),
        ("coverage", -0.1, r"coverage=-0\.1"),
    ],
)
def test_adapter_branch_d_raises_on_out_of_bounds(
    bad_field: str,
    bad_value: float,
    err_match: str,
) -> None:
    """Branch (d) defense-in-depth: out-of-bounds reliability/coverage raises ValueError.

    DTO already guards via Pydantic ge=0/le=1, but the bridge is the only
    code path producing fair_cam input — guard regardless. (F15 carryover)
    """
    v3 = _make_v3_control()
    if bad_field == "reliability":
        _attach_assignment(v3, capability_value=0.5, coverage=0.5, reliability=bad_value)
    else:  # coverage
        _attach_assignment(v3, capability_value=0.5, coverage=bad_value, reliability=0.5)
    with pytest.raises(ValueError, match=err_match):
        _v3_to_fair_cam_control(v3)


# ---------------------------------------------------------------------------
# Cost bridge: v3 Control.annual_cost (Decimal) → fair_cam CostModel.annual_cost (float)
# Issue #66: column collapse JSON dict → Numeric(18, 2). The bridge coerces
# Decimal → float at the fair_cam boundary; fair_cam itself remains a float DTO.
# ---------------------------------------------------------------------------


def test_adapter_passes_annual_cost_through() -> None:
    """v3's Decimal annual_cost passes through as float on fair_cam.CostModel.

    Per-control ROI on the fair_cam side must see the v3 cost — otherwise
    aggregate ROI silently aggregates $0 (the bug that motivated issue #66).
    """
    v3 = _make_v3_control()
    v3.annual_cost = Decimal("12345.67")
    _attach_assignment(v3, capability_value=0.5, coverage=0.5, reliability=0.5)

    fc_ctrl = _v3_to_fair_cam_control(v3)

    assert fc_ctrl.cost_model.annual_cost == 12345.67
    assert isinstance(fc_ctrl.cost_model.annual_cost, float)


def test_adapter_falls_back_to_zero_when_annual_cost_default() -> None:
    """The column default is Decimal('0'); the bridge coerces to 0.0."""
    v3 = _make_v3_control()  # annual_cost=Decimal("0") from factory
    _attach_assignment(v3, capability_value=0.5, coverage=0.5, reliability=0.5)

    fc_ctrl = _v3_to_fair_cam_control(v3)
    assert fc_ctrl.cost_model.annual_cost == 0.0
