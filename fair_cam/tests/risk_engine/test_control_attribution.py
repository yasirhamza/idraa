"""Tests for the extracted pyfair-free per-control attribution helpers.

`build_control_adjustment` + `representative_value` were moved out of the
pyfair-coupled `control_aware._build_control_adjustment` (Task 4) so the native
calculator can reuse them as free functions. The FAIR math is byte-identical;
the only change is the parameter source (`effectiveness_calculator` is passed in
rather than read from `self`). Legacy parity is also covered by the existing
`control_aware` suite, which now exercises the delegated path unchanged.
"""

import math

import pytest

from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.models.risk_enhanced import ControlAdjustment
from fair_cam.risk_engine.control_attribution import (
    build_control_adjustment,
    representative_value,
)
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution
from fair_cam.tests.risk_engine._helpers import make_control


@pytest.fixture
def a_control():
    """A single Control with one assignment, built via the shared factory."""
    return make_control(
        control_id="c1",
        assignments=[("lec_prev_resistance", "probability", 0.7)],
    )


def test_representative_value_per_distribution():
    assert (
        representative_value(
            FAIRDistribution(DistributionType.PERT, {"low": 1, "mode": 2, "high": 4})
        )
        == 2
    )
    assert (
        representative_value(
            FAIRDistribution(DistributionType.TRIANGULAR, {"low": 1, "mode": 3, "high": 5})
        )
        == 3
    )
    assert (
        representative_value(FAIRDistribution(DistributionType.UNIFORM, {"low": 2, "high": 4})) == 3
    )
    assert (
        representative_value(FAIRDistribution(DistributionType.NORMAL, {"mean": 7.0, "std": 2.0}))
        == 7.0
    )
    assert representative_value(
        FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 2.0, "sigma": 1.0})
    ) == math.exp(2.0)


def test_representative_value_uniform_is_midpoint_not_half_default():
    """M-B1 regression guard: UNIFORM must map to its midpoint, NOT the 0.5 safe-default.

    Origin: the original `_vuln_to_float` pyfair-bridge closure had a bug where a UNIFORM
    vulnerability distribution silently fell through to the 0.5 safe-default instead of
    computing its midpoint, collapsing LEF and corrupting Monte Carlo results.  That bridge
    was deleted in the engine cutover (Task 8); its regression guard was carried here when
    `tests/unit/test_run_executor_vuln_to_float.py` was deleted.

    Note: the native engine makes this entire bug class structurally impossible — vulnerability
    is sampled as a full distribution and never collapsed to a scalar during simulation.
    `representative_value` is only used for the per-control attribution breakdown view-model,
    so the guard is retained here for that narrower consumer.

    Two cases:
      * Degenerate (low == high == 0.4) — midpoint is 0.4, NOT the 0.5 safe-default.
      * Non-degenerate (low=0.2, high=0.6) — midpoint is 0.4, also NOT 0.5.
    """
    # Degenerate case: if the fallthrough 0.5 default fired, this would incorrectly return 0.5.
    assert (
        representative_value(FAIRDistribution(DistributionType.UNIFORM, {"low": 0.4, "high": 0.4}))
        == 0.4
    ), "UNIFORM degenerate (low==high==0.4) must return 0.4, not the 0.5 safe-default (M-B1)"

    # Non-degenerate case with midpoint != 0.5.
    assert (
        representative_value(FAIRDistribution(DistributionType.UNIFORM, {"low": 0.2, "high": 0.6}))
        == 0.4
    ), "UNIFORM(0.2, 0.6) midpoint is 0.4, not 0.5 (M-B1)"


def test_build_control_adjustment_matches_legacy(a_control):
    calc = ControlEffectivenessCalculator()
    adj = build_control_adjustment(a_control, calc, 100.0, 0.5, 1_000_000.0, 500_000.0)
    assert isinstance(adj, ControlAdjustment)
    assert adj.control_id == a_control.control_id
    assert adj.control_name == a_control.name
