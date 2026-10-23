"""#268 Task 5 -- ``assignment_display_rows``: derived rows display their SOURCE's values.

A derived ``dsc_corr_misaligned`` row stores only a link plus inert sentinels (capability
NULL, coverage 0.0, reliability 0.0). Every display surface must show the linked source's
capability / coverage / reliability, never the sentinels; an unresolvable derived row shows
"derived (source unavailable)" and no numbers. The helper takes the rows of ONE control as
ORM rows (id-based link), V3 snapshot DTOs or their JSON dicts (slug-based link).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest

from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import FairCamSubFunction, UnitType
from idraa.schemas.run_snapshot import ControlFunctionAssignmentSnapshotDTO
from idraa.services._view_model_helpers import (
    AssignmentDisplayRow,
    assignment_display_rows,
)

DSC = "dsc_corr_misaligned"
ET = "lec_resp_event_termination"
PREV = "lec_prev_resistance"
LABEL = "Correcting Misaligned Decisions"
ET_HUMAN = "LEC Resp Event Termination"
ORG = uuid.uuid4()


def _orm(
    sf: str,
    cap: float | None,
    cov: float,
    rel: float,
    *,
    rid: uuid.UUID | None = None,
    link: uuid.UUID | None = None,
    confirmed: bool = False,
) -> ControlFunctionAssignment:
    from datetime import UTC, datetime

    return ControlFunctionAssignment(
        id=rid or uuid.uuid4(),
        organization_id=ORG,
        control_id=uuid.uuid4(),
        sub_function=FairCamSubFunction(sf),
        capability_value=cap,
        coverage=cov,
        reliability=rel,
        derived_from_assignment_id=link,
        confirmed_by_user_at=datetime(2026, 10, 1, tzinfo=UTC) if confirmed else None,
    )


def _dto(
    sf: str,
    unit: UnitType,
    cap: float | None,
    cov: float,
    rel: float,
    *,
    link_sf: str | None = None,
) -> ControlFunctionAssignmentSnapshotDTO:
    return ControlFunctionAssignmentSnapshotDTO(
        sub_function=FairCamSubFunction(sf),
        capability_value=cap,
        coverage=cov,
        reliability=rel,
        unit_type=unit,
        derived_from_sub_function=FairCamSubFunction(link_sf) if link_sf else None,
    )


def _snapshot_rows(*, link_sf: str | None = ET, with_source: bool = True) -> list[Any]:
    rows: list[Any] = [_dto(PREV, UnitType.PROBABILITY, 0.75, 0.9, 0.85)]
    if with_source:
        rows.append(_dto(ET, UnitType.ELAPSED_TIME, 30.0, 0.83, 0.61))
    rows.append(_dto(DSC, UnitType.PROBABILITY, None, 0.0, 0.0, link_sf=link_sf))
    return rows


# --- non-derived rows pass through ------------------------------------------------------


def test_a_plain_row_displays_its_own_values_unchanged() -> None:
    (row,) = assignment_display_rows([_orm(PREV, 0.75, 0.9, 0.85, confirmed=True)])
    assert row == AssignmentDisplayRow(
        sub_function=PREV,
        label="LEC Prev Resistance",
        capability_value=0.75,
        capability_sub_function=PREV,
        unit_type=None,
        coverage=0.9,
        reliability=0.85,
        confirmed=True,
        derived=False,
        source_unavailable=False,
    )


def test_a_plain_snapshot_row_keeps_its_frozen_unit_type() -> None:
    (row,) = assignment_display_rows([_dto(ET, UnitType.ELAPSED_TIME, 30.0, 0.8, 0.7)])
    assert (row.unit_type, row.capability_value, row.derived) == ("elapsed_time", 30.0, False)


# --- ORM rows: the link is a row id -----------------------------------------------------


def test_orm_derived_row_displays_the_sources_values_not_the_sentinels() -> None:
    source_id = uuid.uuid4()
    rows = [
        _orm(PREV, 0.75, 0.9, 0.85),
        _orm(ET, 30.0, 0.83, 0.61, rid=source_id),
        _orm(DSC, None, 0.0, 0.0, link=source_id, confirmed=True),
    ]
    plain, source, derived = assignment_display_rows(rows)
    assert derived.derived is True and derived.source_unavailable is False
    assert derived.sub_function == DSC
    assert derived.label == f"{LABEL} — derived from {ET_HUMAN}"
    assert (derived.capability_value, derived.coverage, derived.reliability) == (30.0, 0.83, 0.61)
    assert derived.capability_sub_function == ET  # the unit the 30.0 is expressed in
    assert derived.confirmed is True  # the derived row's OWN confirmation
    # the plain rows are untouched
    assert (plain.capability_value, source.coverage) == (0.75, 0.83)


def test_orm_derived_row_resolves_when_it_sorts_before_its_source() -> None:
    source_id = uuid.uuid4()
    rows = [
        _orm(DSC, None, 0.0, 0.0, link=source_id),
        _orm(ET, 12.0, 0.5, 0.4, rid=source_id),
        _orm(PREV, 0.75, 0.9, 0.85),
    ]
    derived, source, plain = assignment_display_rows(rows)
    assert [r.sub_function for r in (derived, source, plain)] == [DSC, ET, PREV]  # order kept
    assert (derived.capability_value, derived.coverage, derived.reliability) == (12.0, 0.5, 0.4)


def test_a_source_with_a_null_capability_shows_a_null_capability_not_unavailable() -> None:
    source_id = uuid.uuid4()
    rows = [_orm(ET, None, 0.5, 0.4, rid=source_id), _orm(DSC, None, 0.0, 0.0, link=source_id)]
    _, derived = assignment_display_rows(rows)
    assert derived.source_unavailable is False
    assert derived.capability_value is None  # the source's own NULL, shown like the source row
    assert (derived.coverage, derived.reliability) == (0.5, 0.4)


# --- V3 snapshot DTOs and their JSON dicts: the link is the source's slug ---------------


def test_snapshot_dto_derived_row_displays_the_sources_values_and_unit() -> None:
    _, source, derived = assignment_display_rows(_snapshot_rows())
    assert derived.label == f"{LABEL} — derived from {ET_HUMAN}"
    assert (derived.capability_value, derived.coverage, derived.reliability) == (30.0, 0.83, 0.61)
    assert derived.unit_type == "elapsed_time"  # the SOURCE's frozen unit, not probability
    assert derived.capability_sub_function == ET
    assert source.unit_type == "elapsed_time"


def test_json_dict_snapshot_rows_resolve_like_the_dtos() -> None:
    dicts = [r.model_dump(mode="json") for r in _snapshot_rows()]
    assert assignment_display_rows(dicts) == assignment_display_rows(_snapshot_rows())


# --- unresolvable derived rows ----------------------------------------------------------


def _assert_unavailable(row: AssignmentDisplayRow) -> None:
    assert row.derived is True and row.source_unavailable is True
    assert row.label == f"{LABEL} — derived (source unavailable)"
    assert row.capability_value is None
    assert row.coverage is None and row.reliability is None
    assert row.unit_type is None


@pytest.mark.parametrize(
    "rows_factory",
    [
        pytest.param(lambda: _snapshot_rows(link_sf=None), id="snapshot-no-source-slug"),
        pytest.param(lambda: _snapshot_rows(with_source=False), id="snapshot-source-absent"),
        pytest.param(lambda: _snapshot_rows(link_sf=DSC), id="snapshot-link-to-itself"),
        pytest.param(
            lambda: [_orm(PREV, 0.75, 0.9, 0.85), _orm(DSC, None, 0.0, 0.0, link=uuid.uuid4())],
            id="orm-dangling-link",
        ),
        pytest.param(
            lambda: [_orm(PREV, 0.75, 0.9, 0.85), _orm(DSC, None, 0.0, 0.0, link=None)],
            id="orm-link-cleared",
        ),
    ],
)
def test_an_unresolvable_derived_row_shows_source_unavailable_and_no_numbers(
    rows_factory: Callable[[], list[Any]],
) -> None:
    *_, derived = assignment_display_rows(rows_factory())
    _assert_unavailable(derived)


def test_a_link_to_another_derived_row_is_unavailable() -> None:
    first_id = uuid.uuid4()
    rows = [
        _orm(DSC, None, 0.0, 0.0, rid=first_id, link=uuid.uuid4()),
        _orm("dsc_id_misaligned", 0.7, 0.9, 0.9),
    ]
    _assert_unavailable(assignment_display_rows(rows)[0])
    # and a second derived row pointing at the first never borrows the first's sentinels
    rows.append(_orm(DSC, None, 0.0, 0.0, link=first_id))
    assert assignment_display_rows(rows)[-1].source_unavailable is True


# --- adapter iteration contract (N >= 3) ------------------------------------------------


def test_every_row_is_returned_in_order() -> None:
    source_id = uuid.uuid4()
    rows = [
        _orm(PREV, 0.1, 0.1, 0.1),
        _orm("lec_det_visibility", 0.2, 0.2, 0.2),
        _orm(ET, 30.0, 0.83, 0.61, rid=source_id),
        _orm(DSC, None, 0.0, 0.0, link=source_id),
        _orm("vmc_prev_reduce_change_freq", 0.5, 0.5, 0.5),
    ]
    out = assignment_display_rows(rows)
    assert [r.sub_function for r in out] == [r.sub_function.value for r in rows]


def test_an_empty_input_returns_an_empty_list() -> None:
    assert assignment_display_rows([]) == []
