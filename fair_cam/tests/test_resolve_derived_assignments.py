"""Issue #268 — ``resolve_derived_assignments``: the one link resolver shared by the
v3 run mapper (ORM rows) and the verification workbook (V3 snapshots).

Each item is (mapped assignment, derived_from sub-function or None) for ONE control.
Non-derived rows pass through untouched; a derived row is replaced by
``derive_virtual_assignment(<this control's non-derived row of that sub-function>)``;
order is preserved.
"""

from __future__ import annotations

import pytest

from fair_cam.composition import (
    compute_assignment_opeff_two_branch,
    derive_virtual_assignment,
    resolve_derived_assignments,
)
from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import DERIVABLE_SOURCES, FairCamSubFunction

_SF = FairCamSubFunction
_CTRL = "11111111-1111-1111-1111-111111111111"


def _a(
    sf: FairCamSubFunction, cap: float | None = 0.7, cov: float = 0.8, rel: float = 0.9
) -> FairCamControlFunctionAssignment:
    return FairCamControlFunctionAssignment(
        sub_function=sf, capability_value=cap, coverage=cov, reliability=rel
    )


def _placeholder() -> FairCamControlFunctionAssignment:
    """What the mapper emits for a derived row: the stored sentinel scalars."""
    return FairCamControlFunctionAssignment(
        sub_function=_SF.DSC_CORR_MISALIGNED, capability_value=None, coverage=0.0, reliability=0.0
    )


_CAP_BY_SOURCE = {
    _SF.VMC_CORR_IMPLEMENTATION: 30.0,  # ELAPSED_TIME
    _SF.LEC_RESP_EVENT_TERMINATION: 44.4,  # ELAPSED_TIME
    _SF.LEC_RESP_RESILIENCE: 0.6,  # PROBABILITY
}


@pytest.mark.parametrize("src_sf", sorted(DERIVABLE_SOURCES))
def test_derived_row_is_replaced_by_the_derivation_of_its_source(src_sf: _SF) -> None:
    src = _a(src_sf, _CAP_BY_SOURCE[src_sf], 0.8, 0.9)
    out = resolve_derived_assignments([(src, None), (_placeholder(), src_sf)], control_ref=_CTRL)
    assert len(out) == 2
    assert out[0] is src
    expected = derive_virtual_assignment(src)
    assert out[1] == expected
    assert out[1].sub_function is _SF.DSC_CORR_MISALIGNED
    # Exact ``==``: the derived row IS the source's opeff (register C12).
    assert compute_assignment_opeff_two_branch(out[1]) == compute_assignment_opeff_two_branch(src)


def test_n4_order_is_preserved_with_the_derived_row_in_the_middle() -> None:
    """Adapter iteration contract: 4 in, 4 out, same order, derived row resolved in place."""
    r0 = _a(_SF.LEC_PREV_RESISTANCE, 0.85)
    src = _a(_SF.VMC_CORR_IMPLEMENTATION, 30.0)
    derived = _placeholder()
    r3 = _a(_SF.DSC_ID_MISALIGNED, 0.6)
    out = resolve_derived_assignments(
        [(r0, None), (derived, _SF.VMC_CORR_IMPLEMENTATION), (src, None), (r3, None)],
        control_ref=_CTRL,
    )
    assert [a.sub_function for a in out] == [
        _SF.LEC_PREV_RESISTANCE,
        _SF.DSC_CORR_MISALIGNED,
        _SF.VMC_CORR_IMPLEMENTATION,
        _SF.DSC_ID_MISALIGNED,
    ]
    assert out[0] is r0 and out[2] is src and out[3] is r3
    assert out[1] == derive_virtual_assignment(src)


def test_source_may_appear_after_its_derived_row() -> None:
    """Resolution is by sub-function over the whole list, not by position."""
    src = _a(_SF.LEC_RESP_RESILIENCE, 0.6)
    out = resolve_derived_assignments(
        [(_placeholder(), _SF.LEC_RESP_RESILIENCE), (src, None)], control_ref=_CTRL
    )
    assert out[0] == derive_virtual_assignment(src)


def test_no_derived_rows_passes_every_row_through_unchanged() -> None:
    rows = [(_a(_SF.LEC_PREV_RESISTANCE), None), (_a(_SF.LEC_DET_VISIBILITY), None)]
    out = resolve_derived_assignments(rows, control_ref=_CTRL)
    assert [id(a) for a in out] == [id(a) for a, _ in rows]


def test_empty_rows_return_an_empty_list() -> None:
    assert resolve_derived_assignments([], control_ref=_CTRL) == []


def test_link_on_a_row_that_is_not_dsc_corr_misaligned_is_refused() -> None:
    src = _a(_SF.VMC_CORR_IMPLEMENTATION, 30.0)
    bad = _a(_SF.LEC_PREV_RESISTANCE)
    with pytest.raises(ValueError, match=_CTRL) as ei:
        resolve_derived_assignments(
            [(src, None), (bad, _SF.VMC_CORR_IMPLEMENTATION)], control_ref=_CTRL
        )
    assert "lec_prev_resistance" in str(ei.value)


def test_dsc_corr_misaligned_without_a_link_is_refused() -> None:
    src = _a(_SF.VMC_CORR_IMPLEMENTATION, 30.0)
    with pytest.raises(ValueError, match=_CTRL) as ei:
        resolve_derived_assignments([(src, None), (_placeholder(), None)], control_ref=_CTRL)
    assert "dsc_corr_misaligned" in str(ei.value)


def test_source_absent_from_the_control_is_refused() -> None:
    other = _a(_SF.LEC_PREV_RESISTANCE)
    with pytest.raises(ValueError, match=_CTRL) as ei:
        resolve_derived_assignments(
            [(other, None), (_placeholder(), _SF.VMC_CORR_IMPLEMENTATION)], control_ref=_CTRL
        )
    assert "vmc_corr_implementation" in str(ei.value)


@pytest.mark.parametrize(
    "ineligible",
    [
        _SF.VMC_CORR_TREATMENT_SELECTION,  # register C8: score-neutral
        _SF.LEC_RESP_LOSS_REDUCTION,  # CURRENCY: no opeff
        _SF.LEC_PREV_RESISTANCE,  # not a correction/response process
        _SF.DSC_ID_MISALIGNED,
    ],
)
def test_ineligible_source_is_refused(ineligible: _SF) -> None:
    cap = 1000.0 if ineligible is _SF.LEC_RESP_LOSS_REDUCTION else 0.5
    src = _a(ineligible, cap)
    with pytest.raises(ValueError, match=_CTRL) as ei:
        resolve_derived_assignments([(src, None), (_placeholder(), ineligible)], control_ref=_CTRL)
    assert ineligible.value in str(ei.value)


def test_source_that_is_itself_derived_is_refused() -> None:
    """A link naming the derived row's own sub-function (a derived row cannot be a
    source, and there is no non-derived dsc_corr_misaligned row to resolve to)."""
    src = _a(_SF.VMC_CORR_IMPLEMENTATION, 30.0)
    with pytest.raises(ValueError, match=_CTRL) as ei:
        resolve_derived_assignments(
            [(src, None), (_placeholder(), _SF.DSC_CORR_MISALIGNED)], control_ref=_CTRL
        )
    assert "derived" in str(ei.value)


def test_error_message_names_control_and_slugs_only() -> None:
    """The message is built from the control ref and slugs: nothing from the row values."""
    other = _a(_SF.LEC_PREV_RESISTANCE, cap=0.123456, cov=0.654321)
    with pytest.raises(ValueError) as ei:
        resolve_derived_assignments(
            [(other, None), (_placeholder(), _SF.LEC_RESP_RESILIENCE)], control_ref=_CTRL
        )
    msg = str(ei.value)
    assert _CTRL in msg
    assert "0.123456" not in msg and "0.654321" not in msg
