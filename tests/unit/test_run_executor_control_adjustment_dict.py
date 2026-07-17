"""Test _control_adjustment_to_dict surfaces PR μ.1 fields + fixes pre-existing bug."""

from __future__ import annotations

from fair_cam.models.risk_enhanced import ControlAdjustment

from idraa.services.run_executor import _control_adjustment_to_dict


def test_dict_includes_loss_reduction_per_event() -> None:
    """PR μ.1: loss_reduction_per_event surfaces in serialized dict."""
    adj = ControlAdjustment(
        control_id="c1",
        control_name="C1",
        loss_reduction_per_event=213_750.0,
    )
    d = _control_adjustment_to_dict(adj)
    assert d["loss_reduction_per_event"] == 213_750.0


def test_dict_loss_reduction_defaults_to_zero() -> None:
    """Default value of 0.0 propagates through serialization."""
    adj = ControlAdjustment(control_id="c1", control_name="C1")
    d = _control_adjustment_to_dict(adj)
    assert d["loss_reduction_per_event"] == 0.0


def test_dict_surfaces_control_effectiveness_correctly() -> None:
    """Arch-I4 pre-existing bug fix: dataclass field is `control_effectiveness`,
    NOT `effectiveness`. Pre-Task-7 the serializer read `getattr(adj, "effectiveness", 0.0)`
    which silently returned 0.0 for every control."""
    adj = ControlAdjustment(
        control_id="c1",
        control_name="C1",
        control_effectiveness=0.85,
    )
    d = _control_adjustment_to_dict(adj)
    # Output key in the JSON dict is 'effectiveness' (consumer-facing); reads from
    # ControlAdjustment.control_effectiveness, not the non-existent .effectiveness.
    assert d["effectiveness"] == 0.85
