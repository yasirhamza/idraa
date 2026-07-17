"""Smoke tests — prove pytest, the package, and fair_cam all load."""

from __future__ import annotations


def test_idraa_package_imports() -> None:
    import idraa

    assert idraa.__name__ == "idraa"


def test_fair_cam_core_imports() -> None:
    """fair_cam must be installed as an editable dep and its key classes importable."""
    # #328: ControlAwareRiskCalculator + RiskParameters retired; the native
    # engine surface is the public API.
    from fair_cam import Control, NativeControlAwareRiskCalculator

    assert Control is not None
    assert NativeControlAwareRiskCalculator is not None
