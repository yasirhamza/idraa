"""Pydantic boundary tests for ControlForm.annual_cost (issue #66)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from idraa.models.enums import ControlDomain, ControlType, FairCamSubFunction
from idraa.schemas.control import ControlForm


def _base_form_data() -> dict:
    """Minimum-required ControlForm fields excluding annual_cost.

    The point of this helper is that annual_cost behavior is tested in
    isolation from the rest. Adjust if ControlForm gains/loses required
    fields.
    """
    return {
        "name": "Test Control",
        "description": "test",
        "domain": ControlDomain.LOSS_EVENT,
        "type": ControlType.ADMINISTRATIVE,
        "assignments": [
            {
                "sub_function": FairCamSubFunction.LEC_PREV_RESISTANCE,
                "capability_value": 0.8,
                "coverage": 1.0,
                "reliability": 1.0,
            }
        ],
    }


def test_control_form_annual_cost_default_is_zero_decimal() -> None:
    form = ControlForm(**_base_form_data())
    assert form.annual_cost == Decimal("0")
    assert isinstance(form.annual_cost, Decimal)


def test_control_form_annual_cost_coerces_string() -> None:
    form = ControlForm(**_base_form_data(), annual_cost="12000.50")
    assert form.annual_cost == Decimal("12000.50")


def test_control_form_annual_cost_rejects_negative() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ControlForm(**_base_form_data(), annual_cost=Decimal("-1"))
    assert "greater than or equal to 0" in str(exc_info.value).lower()


def test_control_form_annual_cost_rejects_overflow() -> None:
    """Issue #66 + security review: reject values beyond Numeric(18, 2) ceiling
    so Postgres NumericValueOutOfRange never surfaces as a 500."""
    with pytest.raises(ValidationError):
        ControlForm(**_base_form_data(), annual_cost=Decimal("1E20"))


def test_control_form_annual_cost_rejects_excess_precision() -> None:
    """max_digits=18, decimal_places=2 — sub-cent precision is rejected."""
    with pytest.raises(ValidationError):
        ControlForm(**_base_form_data(), annual_cost=Decimal("12000.001"))


def test_control_form_no_legacy_cost_model_field() -> None:
    """Regression: ControlForm must NOT carry the old cost_model dict field."""
    fields = set(ControlForm.model_fields.keys())
    assert "cost_model" not in fields, (
        "ControlForm still has legacy cost_model field — migration incomplete"
    )


def test_control_form_no_legacy_domain_field() -> None:
    """Issue #90: ControlForm must NOT carry the legacy domain field.

    Domain is derived from assignments; no editable user input.
    """
    fields = set(ControlForm.model_fields.keys())
    assert "domain" not in fields, (
        "ControlForm still has legacy domain field — issue #90 migration incomplete"
    )
