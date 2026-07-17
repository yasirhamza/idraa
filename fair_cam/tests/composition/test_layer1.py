"""Layer 1 - intra-assignment composition.

Spec §3.1: OpEff = capability * coverage * reliability per assignment.
Backtest Scenario A (spec §9.1): Standard §3.2.1 firewall on 1-of-4 entrances
yields OpEff = 0.25 exactly.

Slice 2 (#439): the standalone ``compute_assignment_opeff`` was deleted (its
only remaining callers were tests; the production Layer-1 dispatch is now
``compute_assignment_part`` + the ``compute_assignment_opeff_two_branch``
reliability multiply). These §3.1 multiplicative pins are re-pointed at
``compute_assignment_opeff_two_branch`` — for PROBABILITY assignments it
returns the same ``capability * coverage * reliability`` product.
"""

import pytest
from fair_cam.composition import compute_assignment_opeff_two_branch
from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction


def test_layer1_basic_multiplication():
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.8,
    )
    assert compute_assignment_opeff_two_branch(a) == pytest.approx(0.448, abs=1e-9)


def test_layer1_boundary_zero_capability():
    """Capability=0 -> OpEff=0 regardless of coverage/reliability.
    Boundary correctness is the primary justification for Option A over B."""
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.0,
        coverage=1.0,
        reliability=1.0,
    )
    assert compute_assignment_opeff_two_branch(a) == 0.0


def test_layer1_boundary_zero_coverage():
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=1.0,
        coverage=0.0,
        reliability=1.0,
    )
    assert compute_assignment_opeff_two_branch(a) == 0.0


def test_layer1_boundary_zero_reliability():
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=1.0,
        coverage=1.0,
        reliability=0.0,
    )
    assert compute_assignment_opeff_two_branch(a) == 0.0


def test_layer1_perfect_control():
    a = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=1.0,
        coverage=1.0,
        reliability=1.0,
    )
    assert compute_assignment_opeff_two_branch(a) == pytest.approx(1.0, abs=1e-9)


def test_backtest_scenario_a_standard_worked_example():
    """Spec §9.1 Scenario A — Standard §3.2.1 firewall on 1-of-4 entrances.

    capability=1.0 (firewall is fully capable), coverage=0.25 (covers 1 of 4
    entrances), reliability=1.0 (assume perfect operation). Expected OpEff = 0.25.

    This is the Standard's only worked example for composition (loosely consistent
    with Layer 1 multiplicative; not Standard-derived per audit §2.3).
    """
    firewall = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=1.0,
        coverage=0.25,
        reliability=1.0,
    )
    assert compute_assignment_opeff_two_branch(firewall) == pytest.approx(0.25, abs=1e-9)
