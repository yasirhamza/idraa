"""Contract tests for ControlFunctionAssignmentDTO.

Covers:
  - Virtual-function guard (reject_virtual_unless_derived, spec §4.3)
  - M1 unit-type validator (validate_capability_value_unit, spec §7.1)
  - B-NEW3: DTO permits non-NULL derived_from (enforcement at service layer only)
  - Coverage/reliability Pydantic ge/le bounds

Test count: 14 tests (8 module-level DTO + 4 ControlForm cap/relaxation + 2 F9 cross-validator gaps).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from idraa.models.enums import FairCamSubFunction
from idraa.schemas.control import ControlFunctionAssignmentDTO

# Virtual-function guard (spec §4.3 / Decision 3)


def test_dto_rejects_virtual_without_derived() -> None:
    """DSC_CORR_MISALIGNED without derived_from_assignment_id → ValidationError."""
    with pytest.raises(ValidationError, match="virtual"):
        ControlFunctionAssignmentDTO(
            sub_function=FairCamSubFunction.DSC_CORR_MISALIGNED,
            coverage=0.8,
            reliability=0.9,
        )


def test_dto_permits_virtual_when_derived_from_provided() -> None:
    """DSC_CORR_MISALIGNED WITH derived_from_assignment_id → DTO succeeds."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.DSC_CORR_MISALIGNED,
        derived_from_assignment_id=uuid.uuid4(),
        coverage=0.8,
        reliability=0.9,
    )
    assert dto.sub_function == FairCamSubFunction.DSC_CORR_MISALIGNED


def test_dto_permits_non_null_derived_from_on_non_virtual_subfn() -> None:
    """Non-virtual sub_function + non-NULL derived_from → DTO succeeds (B-NEW3)."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.85,
        derived_from_assignment_id=uuid.uuid4(),
        coverage=0.8,
        reliability=0.9,
    )
    assert dto.derived_from_assignment_id is not None


# M1 unit-type validator (spec §7.1)


def test_dto_probability_unit_rejects_out_of_range_capability() -> None:
    """Probability-unit sub_function: capability_value=1.1 → ValidationError (M1)."""
    with pytest.raises(ValidationError, match=r"\[0, 1\]"):
        ControlFunctionAssignmentDTO(
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=1.1,
            coverage=0.8,
            reliability=0.9,
        )


def test_dto_probability_unit_accepts_valid_capability() -> None:
    """Probability-unit sub_function: capability_value=0.85 → succeeds."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.85,
        coverage=0.8,
        reliability=0.9,
    )
    assert dto.capability_value == pytest.approx(0.85)


def test_dto_null_capability_always_permitted() -> None:
    """capability_value=None is valid for any sub_function (OQ1 sentinel)."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.LEC_DET_VISIBILITY,
        capability_value=None,
        coverage=0.5,
        reliability=0.7,
    )
    assert dto.capability_value is None


def test_dto_elapsed_time_unit_accepts_large_capability_value() -> None:
    """ELAPSED_TIME sub_function: capability_value=3600.0 → succeeds (no upper bound)."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.LEC_DET_MONITORING,
        capability_value=3600.0,
        coverage=0.8,
        reliability=0.9,
    )
    assert dto.capability_value == pytest.approx(3600.0)


def test_dto_elapsed_time_unit_rejects_negative_capability() -> None:
    """ELAPSED_TIME sub_function: capability_value=-1.0 → ValidationError (M1)."""
    with pytest.raises(ValidationError, match="non-negative"):
        ControlFunctionAssignmentDTO(
            sub_function=FairCamSubFunction.LEC_DET_MONITORING,
            capability_value=-1.0,
            coverage=0.8,
            reliability=0.9,
        )


# F9 gap test 1: CURRENCY/TIME unit accepts non-NULL capability_value


def test_dto_currency_unit_accepts_non_null_capability() -> None:
    """CURRENCY-unit sub_function: capability_value=10000.0 → succeeds (no upper bound)."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
        capability_value=10000.0,
        coverage=0.8,
        reliability=0.9,
    )
    assert dto.capability_value == pytest.approx(10000.0)


@pytest.mark.parametrize("blank_value", ["", "   ", "  \t "])
def test_dto_blank_capability_string_coerces_to_none(blank_value: str) -> None:
    """UAT 2026-05-21: HTML forms submit empty inputs as the empty string,
    not as a missing key. Without coercion, Pydantic raises 'unable to
    parse string as number' on Save for any control whose currency
    capability is NULL — including the no-changes Save round-trip on the
    edit page. Empty / whitespace-only strings must coerce to None so
    the field's default kicks in."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
        capability_value=blank_value,  # type: ignore[arg-type]
        coverage=0.8,
        reliability=0.9,
    )
    assert dto.capability_value is None


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("5000", 5000.0),
        ("5,000", 5000.0),  # rendered with thousands separators; round-trip
        ("1,234,567", 1234567.0),
        ("  500 ", 500.0),  # stray whitespace
    ],
)
def test_dto_currency_capability_strips_separators(input_str: str, expected: float) -> None:
    """Pre-existing currency values render as '5,000' for readability;
    a user who re-types the same value verbatim must Save cleanly."""
    dto = ControlFunctionAssignmentDTO(
        sub_function=FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
        capability_value=input_str,  # type: ignore[arg-type]
        coverage=0.8,
        reliability=0.9,
    )
    assert dto.capability_value == pytest.approx(expected)


# F9 gap test 2: virtual + derived + negative-cap cross-validator interaction


def test_dto_virtual_derived_with_negative_capability_rejected() -> None:
    """DSC_CORR_MISALIGNED + derived_from_assignment_id + negative capability_value
    → still rejected by the unit-bounded validator (M1 runs after virtual guard).

    Cross-validator interaction: derived_from_assignment_id satisfies the virtual
    guard, but the M1 unit check still applies. Issue #131 reclassified
    DSC_CORR_MISALIGNED from ELAPSED_TIME to PROBABILITY, so the M1 message
    is now "must be in [0, 1] for probability unit" (was: "non-negative for
    elapsed_time"). The test's purpose — verify cross-validator interaction
    blocks negative caps even when derived_from_assignment_id is set — is
    unchanged.
    """
    with pytest.raises(ValidationError, match=r"must be in \[0, 1\] for probability unit"):
        ControlFunctionAssignmentDTO(
            sub_function=FairCamSubFunction.DSC_CORR_MISALIGNED,
            derived_from_assignment_id=uuid.uuid4(),
            capability_value=-1.0,
            coverage=0.8,
            reliability=0.9,
        )


# ---------------------------------------------------------------------------
# Issue #131 T1+T2 — locked PROBABILITY-unit rejection on reclassified slugs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sub_function",
    [
        # Five of the six issue-#131 reclassified slugs. DSC_CORR_MISALIGNED
        # is the sixth and is virtual — its own ``derived_from_assignment_id``
        # gate fires first under the model_validator ordering; the cross-
        # validator interaction is exercised by
        # ``test_dto_virtual_derived_with_negative_capability_rejected``
        # above (also touches the PROBABILITY-unit rejection path).
        FairCamSubFunction.LEC_RESP_RESILIENCE,
        FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
        FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
        FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION,
        FairCamSubFunction.DSC_ID_MISALIGNED,
    ],
)
def test_dto_reclassified_sub_function_rejects_above_one_capability(
    sub_function: FairCamSubFunction,
) -> None:
    """Issue #131 T1+T2: the six ELAPSED_TIME → PROBABILITY reclassifications
    must surface in the M1 validator. capability_value=2.0 on any of the
    reclassified slugs is now an OUT-OF-RANGE probability and is rejected
    with the canonical PROBABILITY-unit error message.

    Locks the unit-table flip in lockstep — a future regression that
    reverted any slug back to ELAPSED_TIME would silently accept 2.0
    here (valid as 2 days under the old semantics) and this test would
    catch it.
    """
    with pytest.raises(ValidationError, match=r"must be in \[0, 1\] for probability unit"):
        ControlFunctionAssignmentDTO(
            sub_function=sub_function,
            capability_value=2.0,
            coverage=0.5,
            reliability=0.5,
        )


# ---------------------------------------------------------------------------
# ControlForm cap tests (spec §7.2, §4.4)
# ---------------------------------------------------------------------------


def _make_valid_assignment(**kwargs: object) -> ControlFunctionAssignmentDTO:
    """Factory: a valid PROBABILITY-unit assignment for ControlForm fixtures."""
    defaults: dict[str, object] = {
        "sub_function": FairCamSubFunction.LEC_PREV_RESISTANCE,
        "capability_value": 0.75,
        "coverage": 0.8,
        "reliability": 0.9,
    }
    defaults.update(kwargs)
    return ControlFunctionAssignmentDTO(**defaults)  # type: ignore[arg-type]


def test_control_form_accepts_single_assignment() -> None:
    """ControlForm with exactly one assignment passes validation (min_length=1)."""
    from idraa.models.enums import ControlType
    from idraa.schemas.control import ControlForm

    form = ControlForm(
        name="Test Control",
        type=ControlType.TECHNICAL,
        assignments=[_make_valid_assignment()],
    )
    assert len(form.assignments) == 1


def test_control_form_rejects_empty_assignments() -> None:
    """ControlForm with zero assignments → ValidationError (min_length=1)."""
    from idraa.models.enums import ControlType
    from idraa.schemas.control import ControlForm

    with pytest.raises(ValidationError, match="at least 1"):
        ControlForm(
            name="Test Control",
            type=ControlType.TECHNICAL,
            assignments=[],
        )


def test_control_form_accepts_two_distinct_assignments() -> None:
    """ControlForm with 2 distinct sub_functions → accepted (PR kappa cap relaxation, spec §6.1).

    PR iota had max_length=1 (OQ3 hard cap). PR kappa removes it; multiple
    assignments with distinct sub_functions are now valid.
    """
    from idraa.models.enums import ControlType
    from idraa.schemas.control import ControlForm

    form = ControlForm(
        name="Test Control",
        type=ControlType.TECHNICAL,
        assignments=[
            _make_valid_assignment(sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE),
            _make_valid_assignment(sub_function=FairCamSubFunction.LEC_DET_VISIBILITY),
        ],
    )
    assert len(form.assignments) == 2


def test_control_form_rejects_duplicate_sub_function() -> None:
    """ControlForm with 2 assignments having the same sub_function → ValidationError.

    The model_validator defends against duplicate sub_functions (spec §6.1);
    the DB UNIQUE constraint uq_cfa_control_sub_function enforces it at persistence.
    """
    from idraa.models.enums import ControlType
    from idraa.schemas.control import ControlForm

    with pytest.raises(ValidationError, match=r"duplicate sub_function|unique"):
        ControlForm(
            name="Test Control",
            type=ControlType.TECHNICAL,
            assignments=[_make_valid_assignment(), _make_valid_assignment()],
        )
