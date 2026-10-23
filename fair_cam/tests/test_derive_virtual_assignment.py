"""Issue #268 — Correcting Misaligned Decisions (FAIR-CAM §5.3, virtual) derived
from a linked correction/response assignment (register C12).

Pins:
  - bit identity (exact ``==``): the derived PROBABILITY row scores exactly what
    its source scores under ``compute_assignment_opeff_two_branch`` for every
    eligible source × capability (incl. the NULL 0.5 anchor) × (coverage,
    reliability);
  - the eligibility set and the raise paths;
  - ``compute_assignment_part`` is bit-identical to the BASE formula for every
    sub-function after its refactor onto ``capability_opeff``.
"""

from __future__ import annotations

import math

import pytest

from fair_cam.calibration.elapsed_time_taus import get_canonical_tau
from fair_cam.composition import (
    capability_opeff,
    compute_assignment_opeff_two_branch,
    compute_assignment_part,
    derive_virtual_assignment,
)
from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import (
    DERIVABLE_SOURCES,
    SUB_FUNCTION_UNITS,
    FairCamSubFunction,
    UnitType,
)
from fair_cam.normalization import elapsed_time_to_opeff

_COV_REL = ((0.8, 0.8), (0.3, 0.95), (0.0, 1.0))
_CAPS: dict[UnitType, tuple[float | None, ...]] = {
    UnitType.PROBABILITY: (None, 0.0, 0.7, 1.0),
    UnitType.ELAPSED_TIME: (None, 0.0, 30.0, 400.0),
}


def _a(
    sf: FairCamSubFunction, cap: float | None, cov: float, rel: float
) -> FairCamControlFunctionAssignment:
    return FairCamControlFunctionAssignment(
        sub_function=sf, capability_value=cap, coverage=cov, reliability=rel
    )


def test_derivable_sources_is_the_spec_set() -> None:
    assert (
        frozenset(
            {
                FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
                FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
                FairCamSubFunction.LEC_RESP_RESILIENCE,
            }
        )
        == DERIVABLE_SOURCES
    )
    # Every source has an opeff (CURRENCY is excluded by construction).
    assert all(SUB_FUNCTION_UNITS[sf] in _CAPS for sf in DERIVABLE_SOURCES)


def test_derived_assignment_scores_bit_identical_to_source() -> None:
    """Exact ``==`` on purpose: the derived row IS the source's opeff."""
    checked = 0
    for src_sf in sorted(DERIVABLE_SOURCES):
        for cap in _CAPS[SUB_FUNCTION_UNITS[src_sf]]:
            for cov, rel in _COV_REL:
                src = _a(src_sf, cap, cov, rel)
                derived = derive_virtual_assignment(src)
                assert derived.sub_function is FairCamSubFunction.DSC_CORR_MISALIGNED
                assert SUB_FUNCTION_UNITS[derived.sub_function] is UnitType.PROBABILITY
                assert derived.reliability == src.reliability
                assert derived.coverage == src.coverage
                assert compute_assignment_opeff_two_branch(
                    derived
                ) == compute_assignment_opeff_two_branch(src), (src_sf, cap, cov, rel)
                checked += 1
    assert checked == 3 * 4 * 3


def test_hand_anchor_event_termination() -> None:
    """lec_resp_event_termination: tau = 64.0 days; t = 44.4, cov 0.8, r 0.8 ->
    exp(-44.4/64) * 0.8 * 0.8 = 0.3198071559..."""
    src = _a(FairCamSubFunction.LEC_RESP_EVENT_TERMINATION, 44.4, 0.8, 0.8)
    derived = derive_virtual_assignment(src)
    expected = math.exp(-44.4 / 64.0) * 0.8 * 0.8
    assert compute_assignment_opeff_two_branch(derived) == pytest.approx(expected, rel=1e-12)
    assert derived.capability_value == pytest.approx(math.exp(-44.4 / 64.0), rel=1e-12)


def test_null_capability_source_keeps_the_half_anchor() -> None:
    """NULL capability is carried as NULL (not 0.5), so the consumer applies the
    same 0.5 anchor the source gets — identity, not a re-derivation."""
    src = _a(FairCamSubFunction.VMC_CORR_IMPLEMENTATION, None, 0.8, 0.8)
    derived = derive_virtual_assignment(src)
    assert derived.capability_value is None
    assert compute_assignment_opeff_two_branch(derived) == (0.5 * 0.8) * 0.8


@pytest.mark.parametrize(
    "sf",
    [
        FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION,
        FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
        FairCamSubFunction.DSC_ID_MISALIGNED,
        FairCamSubFunction.DSC_CORR_MISALIGNED,
        FairCamSubFunction.LEC_DET_MONITORING,
    ],
)
def test_ineligible_source_raises(sf: FairCamSubFunction) -> None:
    cap: float | None = 5000.0 if SUB_FUNCTION_UNITS[sf] is UnitType.CURRENCY else None
    with pytest.raises(ValueError, match=sf.value):
        derive_virtual_assignment(_a(sf, cap, 0.8, 0.8))


def test_target_other_than_dsc_corr_misaligned_raises() -> None:
    src = _a(FairCamSubFunction.VMC_CORR_IMPLEMENTATION, 30.0, 0.8, 0.8)
    for target in FairCamSubFunction:
        if target is FairCamSubFunction.DSC_CORR_MISALIGNED:
            continue
        with pytest.raises(ValueError, match=target.value):
            derive_virtual_assignment(src, sub_function=target)


def test_capability_opeff_raises_on_currency() -> None:
    cur = _a(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 5000.0, 0.8, 0.8)
    with pytest.raises(ValueError, match="CURRENCY"):
        capability_opeff(cur)


def test_capability_opeff_unit_dispatch() -> None:
    assert capability_opeff(_a(FairCamSubFunction.LEC_PREV_RESISTANCE, None, 1.0, 1.0)) is None
    assert capability_opeff(_a(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.7, 1.0, 1.0)) == 0.7
    assert (
        capability_opeff(_a(FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ, 0.25, 1.0, 1.0)) == 0.25
    )
    assert capability_opeff(
        _a(FairCamSubFunction.VMC_CORR_IMPLEMENTATION, 30.0, 1.0, 1.0)
    ) == elapsed_time_to_opeff(30.0, 79.3)
    assert capability_opeff(_a(FairCamSubFunction.VMC_CORR_IMPLEMENTATION, 0.0, 1.0, 1.0)) == 1.0


def _base_part(a: FairCamControlFunctionAssignment) -> float | None:
    """The pre-refactor BASE formula, written inline."""
    unit = SUB_FUNCTION_UNITS[a.sub_function]
    if unit == UnitType.CURRENCY:
        return None
    if unit == UnitType.ELAPSED_TIME:
        if a.capability_value is None:
            return 0.5 * a.coverage
        tau = get_canonical_tau(a.sub_function)
        return elapsed_time_to_opeff(a.capability_value, tau) * a.coverage
    if a.capability_value is None:
        return 0.5 * a.coverage
    return a.capability_value * a.coverage


def test_compute_assignment_part_is_bit_identical_to_base_formula() -> None:
    """Exact ``==``: the refactor onto ``capability_opeff`` must preserve the
    BASE left-associative operation order for every sub-function and unit."""
    cov = 0.7
    checked = 0
    for sf, unit in SUB_FUNCTION_UNITS.items():
        non_null: float
        if unit is UnitType.ELAPSED_TIME:
            non_null = 10.0
        elif unit is UnitType.CURRENCY:
            non_null = 5000.0
        else:
            non_null = 0.5
        for cap in (None, non_null):
            a = _a(sf, cap, cov, 0.9)
            assert compute_assignment_part(a) == _base_part(a), (sf, cap)
            part = compute_assignment_part(a)
            two = compute_assignment_opeff_two_branch(a)
            if part is None:
                assert two is None
            else:
                assert two == part * a.reliability
            checked += 1
    assert checked == 2 * len(SUB_FUNCTION_UNITS) == 2 * 26
