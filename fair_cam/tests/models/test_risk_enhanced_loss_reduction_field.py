"""Smoke test for ControlAdjustment.loss_reduction_per_event field (PR μ.1)."""

from dataclasses import fields

from fair_cam.models.risk_enhanced import ControlAdjustment


def test_loss_reduction_per_event_field_exists() -> None:
    field_names = {f.name for f in fields(ControlAdjustment)}
    assert "loss_reduction_per_event" in field_names


def test_loss_reduction_per_event_default_zero() -> None:
    adj = ControlAdjustment(control_id="c1", control_name="C1")
    assert adj.loss_reduction_per_event == 0.0


def test_loss_reduction_per_event_typed_float() -> None:
    adj = ControlAdjustment(
        control_id="c1",
        control_name="C1",
        loss_reduction_per_event=213750.0,
    )
    assert adj.loss_reduction_per_event == 213750.0


def test_existing_fields_unchanged() -> None:
    adj = ControlAdjustment(control_id="c1", control_name="C1")
    assert adj.threat_event_frequency_multiplier == 1.0
    assert adj.vulnerability_multiplier == 1.0
    assert adj.primary_loss_multiplier == 1.0
    assert adj.secondary_loss_multiplier == 1.0
    assert adj.control_effectiveness == 0.0
    assert adj.confidence_level == 0.95
    assert adj.risk_reduction_value == 0.0
    assert adj.control_cost == 0.0
