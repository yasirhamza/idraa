"""Elapsed-time → operational-effectiveness normalization (PR μ.1).

Per FAIR-CAM Standard §2.3 the composition formula is implementation-
defined. The audit doc at docs/reference/fair-cam-standard-alignment.md
§8.3 enumerates exp(-λ * elapsed_time) as one example normalization;
the §8 preamble explicitly states no recommendation is made there. PR μ.1
SELECTS this shape as the v3 implementation choice with τ = 1/λ values
in fair_cam.calibration.elapsed_time_taus (see methodology doc at
docs/reference/elapsed-time-tau-calibration.md, added later in PR μ.1).

This module is pure math — no FAIR domain knowledge.
"""

from __future__ import annotations

import math


def elapsed_time_to_opeff(elapsed_time: float, tau: float) -> float:
    """Exponential decay: opeff = exp(-elapsed_time / tau).

    Args:
        elapsed_time: elapsed time in the natural unit of the sub-function
            (typically days). Must be non-negative.
        tau: half-life-equivalent constant for the sub-function (same unit
            as elapsed_time). Must be positive. By construction,
            elapsed_time = tau · ln(2) yields opeff = 0.5.

    Returns:
        opeff in [0, 1]. Saturated at 1.0 for elapsed_time = 0.
    """
    if elapsed_time < 0:
        raise ValueError(f"elapsed_time must be non-negative, got {elapsed_time}")
    if tau <= 0:
        raise ValueError(f"tau must be positive, got {tau}")
    return math.exp(-elapsed_time / tau)
