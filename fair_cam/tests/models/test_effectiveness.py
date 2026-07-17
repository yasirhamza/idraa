"""T9 PR κ: ControlEffectivenessCalculator.calculate_base_effectiveness deleted."""

from fair_cam.controls.effectiveness import ControlEffectivenessCalculator


def test_calculate_base_effectiveness_no_longer_exists():
    """PR κ deletes calculate_base_effectiveness; calculate_risk_reduction_factor
    on Control replaces it everywhere."""
    calc = ControlEffectivenessCalculator()
    assert not hasattr(calc, "calculate_base_effectiveness")
