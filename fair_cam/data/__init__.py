"""Calibration-grade reference data for fair_cam.

Only IRIS is imported by calibration code. Other published cyber-loss
datasets (IC3, DBIR, etc.) live as analyst-facing reference material under
``docs/reference/calibration-sources/`` and are not imported here.
"""

from typing import Final

from . import iris_2025

LATEST_IRIS_YEAR: Final[int] = 2025

__all__ = ["LATEST_IRIS_YEAR", "iris_2025"]
