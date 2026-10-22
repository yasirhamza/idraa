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
from idraa.models.control_function_assignment import ControlFunctionAssignment
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
        id=uuid.uuid4(),
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
        derived_from_assignment_id=None,
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


# ---------------------------------------------------------------------------
# Issue #268: a derived dsc_corr_misaligned row is scored as the derivation of
# its same-control source (fair_cam.resolve_derived_assignments)
# ---------------------------------------------------------------------------

_SRC_ID = uuid.UUID(int=2)
_DERIVED_ID = uuid.UUID(int=1)  # sorts BEFORE the source under (created_at, id)


def _orm_row(
    ctrl: Control,
    sub_function: FairCamSubFunction,
    *,
    row_id: uuid.UUID,
    capability_value: float | None = 0.7,
    coverage: float = 0.8,
    reliability: float = 0.9,
    derived_from: uuid.UUID | None = None,
) -> ControlFunctionAssignment:
    return ControlFunctionAssignment(
        id=row_id,
        organization_id=ctrl.organization_id,
        control_id=ctrl.id,
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
        derived_from_assignment_id=derived_from,
    )


def _linked_control(
    source_sf: FairCamSubFunction,
    *,
    source_cap: float | None,
    derived_first: bool = False,
) -> tuple[Control, ControlFunctionAssignment, ControlFunctionAssignment]:
    """A control with a source row and a derived dsc_corr_misaligned row linked to it,
    stored the way the write path stores it (NULL capability, 0.0 coverage/reliability)."""
    ctrl = _make_control_with_orm_assignments([])
    src = _orm_row(
        ctrl, source_sf, row_id=_SRC_ID, capability_value=source_cap, coverage=0.8, reliability=0.9
    )
    derived = _orm_row(
        ctrl,
        FairCamSubFunction.DSC_CORR_MISALIGNED,
        row_id=_DERIVED_ID,
        capability_value=None,
        coverage=0.0,
        reliability=0.0,
        derived_from=_SRC_ID,
    )
    ctrl.assignments = [derived, src] if derived_first else [src, derived]
    return ctrl, src, derived


def _make_control_with_orm_assignments(rows: list[ControlFunctionAssignment]) -> Control:
    """A control whose ``assignments`` is the real ORM relationship (rows set through it),
    unlike ``_make_v3_control`` which bypasses it for SimpleNamespace rows."""
    ctrl = _make_v3_control()
    ctrl.__dict__.pop("assignments")
    ctrl.assignments = rows
    return ctrl


_SOURCES_AND_CAPS = [
    (FairCamSubFunction.VMC_CORR_IMPLEMENTATION, 30.0),  # ELAPSED_TIME
    (FairCamSubFunction.LEC_RESP_EVENT_TERMINATION, 44.4),  # ELAPSED_TIME
    (FairCamSubFunction.LEC_RESP_RESILIENCE, 0.6),  # PROBABILITY
    (FairCamSubFunction.VMC_CORR_IMPLEMENTATION, None),  # NULL -> 0.5 anchor
]


@pytest.mark.parametrize(("source_sf", "source_cap"), _SOURCES_AND_CAPS)
def test_adapter_scores_a_linked_row_as_the_derivation_of_its_source(
    source_sf: FairCamSubFunction, source_cap: float | None
) -> None:
    """The fc assignments carry a PROBABILITY dsc_corr_misaligned row whose opeff equals
    the source's (exact ``==``: it IS the source's opeff, register C12)."""
    from fair_cam.composition import compute_assignment_opeff_two_branch
    from fair_cam.models.sub_function import SUB_FUNCTION_UNITS as FC_UNITS
    from fair_cam.models.sub_function import FairCamSubFunction as FcSub
    from fair_cam.models.sub_function import UnitType as FcUnit

    ctrl, _src, _derived = _linked_control(source_sf, source_cap=source_cap)
    fc = _v3_to_fair_cam_control(ctrl)

    assert [a.sub_function.value for a in fc.assignments] == [
        source_sf.value,
        "dsc_corr_misaligned",
    ]
    fc_src, fc_derived = fc.assignments
    assert fc_derived.sub_function is FcSub.DSC_CORR_MISALIGNED
    assert FC_UNITS[fc_derived.sub_function] is FcUnit.PROBABILITY
    assert compute_assignment_opeff_two_branch(fc_derived) == compute_assignment_opeff_two_branch(
        fc_src
    )
    assert fc_derived.coverage == 0.8  # the source's, not the stored 0.0 sentinel
    assert fc_derived.reliability == 0.9  # the AUTHORED r0 of the source


def test_adapter_derived_row_hand_math_event_termination() -> None:
    """Hand math: tau(lec_resp_event_termination) = 64.0 d; t = 44.4 d, coverage 0.8,
    reliability 0.9 -> exp(-44.4/64) * 0.8 * 0.9 = 0.3598..."""
    import math

    from fair_cam.composition import compute_assignment_opeff_two_branch

    ctrl, _s, _d = _linked_control(FairCamSubFunction.LEC_RESP_EVENT_TERMINATION, source_cap=44.4)
    fc = _v3_to_fair_cam_control(ctrl)
    expected = math.exp(-44.4 / 64.0) * 0.8 * 0.9
    assert compute_assignment_opeff_two_branch(fc.assignments[1]) == pytest.approx(
        expected, rel=1e-12
    )


def test_adapter_dangling_derived_link_raises_naming_the_control() -> None:
    ctrl, _src, derived = _linked_control(
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION, source_cap=30.0
    )
    derived.derived_from_assignment_id = uuid.UUID(int=99)  # no such row anywhere
    with pytest.raises(ValueError, match=str(ctrl.id)):
        _v3_to_fair_cam_control(ctrl)


def test_adapter_link_to_another_controls_row_raises() -> None:
    """The source is resolved among THIS control's rows only: an id that belongs to a
    row of a different control is dangling here."""
    ctrl, _src, derived = _linked_control(
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION, source_cap=30.0
    )
    other = _make_control_with_orm_assignments([])
    foreign = _orm_row(
        other,
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
        row_id=uuid.UUID(int=77),
        capability_value=30.0,
    )
    other.assignments = [foreign]
    derived.derived_from_assignment_id = foreign.id
    with pytest.raises(ValueError, match=str(ctrl.id)):
        _v3_to_fair_cam_control(ctrl)


@pytest.mark.parametrize("derived_first", [False, True])
def test_adapter_representative_domain_skips_the_derived_row(derived_first: bool) -> None:
    """The derived row can sort first (created together, equal created_at, smaller id).
    The representative domain/response time must still come from a non-derived row."""
    ctrl, _src, _derived = _linked_control(
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION, source_cap=30.0, derived_first=derived_first
    )
    fc = _v3_to_fair_cam_control(ctrl)
    assert fc.domain == FairCamControlDomain.VARIANCE_MANAGEMENT
    assert fc.response_time_seconds == 60.0  # VARIANCE_MANAGEMENT, not DECISION_SUPPORT's 3600.0
    assert len(fc.assignments) == 2


def test_adapter_without_a_derived_row_is_unchanged() -> None:
    """No link anywhere: assignments map one-to-one, in order, as before."""
    ctrl = _make_control_with_orm_assignments([])
    rows = [
        _orm_row(ctrl, FairCamSubFunction.LEC_PREV_RESISTANCE, row_id=uuid.UUID(int=11)),
        _orm_row(ctrl, FairCamSubFunction.LEC_DET_VISIBILITY, row_id=uuid.UUID(int=12)),
        _orm_row(ctrl, FairCamSubFunction.VMC_CORR_IMPLEMENTATION, row_id=uuid.UUID(int=13)),
    ]
    ctrl.assignments = rows
    fc = _v3_to_fair_cam_control(ctrl)
    assert [a.sub_function.value for a in fc.assignments] == [r.sub_function.value for r in rows]
