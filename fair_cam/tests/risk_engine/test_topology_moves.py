"""Task 0 (#130): topology-relocation contract.

`_NON_OPEFF_SUB_FUNCTIONS` + the pair-group topology (`PAIR_GROUPS`,
`PAIR_RECIPES`) are data, not engine logic, so they live in
`composition_topology.py`. The shared composition routine (Task 2) imports them
from there to break the `control_aware -> group_composition -> control_aware`
import cycle (plan-gate B-arch-1). `control_aware` re-exports
`_NON_OPEFF_SUB_FUNCTIONS` for back-compat.
"""

from fair_cam.models.composition_topology import (
    _NON_OPEFF_SUB_FUNCTIONS,
    PAIR_GROUPS,
    PAIR_RECIPES,
    BooleanGroup,
)
from fair_cam.models.sub_function import (
    TIME_UNIT_EXCLUDED,
    FairCamSubFunction,
    UnitType,
)


def test_non_opeff_sub_functions_is_currency_only():
    """`_NON_OPEFF_SUB_FUNCTIONS` excludes ONLY the CURRENCY (Loss-Reduction)
    sub-function from the opeff composition path (#130 D3)."""
    assert frozenset({FairCamSubFunction.LEC_RESP_LOSS_REDUCTION}) == _NON_OPEFF_SUB_FUNCTIONS


def test_non_opeff_is_proper_subset_of_time_unit_excluded():
    """NIT NEW-5 guard: `_NON_OPEFF_SUB_FUNCTIONS` (CURRENCY only) is a PROPER
    subset of `TIME_UNIT_EXCLUDED` (ELAPSED_TIME + CURRENCY). A future "dedupe"
    that collapses the two would wrongly drop ELAPSED_TIME opeffs from
    composition — this pins them distinct."""
    assert _NON_OPEFF_SUB_FUNCTIONS < TIME_UNIT_EXCLUDED
    # the difference is exactly the ELAPSED_TIME sub-functions
    elapsed = frozenset(
        sf
        for sf, ut in __import__(
            "fair_cam.models.sub_function", fromlist=["SUB_FUNCTION_UNITS"]
        ).SUB_FUNCTION_UNITS.items()
        if ut == UnitType.ELAPSED_TIME
    )
    assert elapsed == (TIME_UNIT_EXCLUDED - _NON_OPEFF_SUB_FUNCTIONS)


def test_control_aware_reexports_non_opeff_for_back_compat():
    """`control_aware` must keep exposing `_NON_OPEFF_SUB_FUNCTIONS` (re-export)
    so existing importers don't break."""
    from fair_cam.risk_engine.control_aware import (
        _NON_OPEFF_SUB_FUNCTIONS as REEXPORTED,
    )

    assert REEXPORTED is _NON_OPEFF_SUB_FUNCTIONS


def test_pair_recipes_match_topology():
    """PAIR_RECIPES maps each pair group to its (left, right) leaf children;
    PAIR_GROUPS is exactly the key set."""
    assert PAIR_RECIPES == {
        BooleanGroup.LEC_DETECTION_RESPONSE_PAIR: (
            BooleanGroup.LEC_DETECTION,
            BooleanGroup.LEC_RESPONSE,
        ),
        BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR: (
            BooleanGroup.VMC_IDENTIFICATION,
            BooleanGroup.VMC_CORRECTION,
        ),
    }
    assert frozenset(PAIR_RECIPES.keys()) == PAIR_GROUPS


def test_no_import_cycle_between_control_aware_and_topology():
    """Both modules import cleanly with no circular ImportError."""
    import fair_cam.models.composition_topology
    import fair_cam.risk_engine.control_aware  # noqa: F401
