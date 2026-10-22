"""Control CRUD service — phase 1.2.2.

Verifies `create_control` + `list_controls` + `soft_delete_control` form
a coherent CRUD shape: creation round-trips via the list (active only by
default), and soft-delete hides the row from the active list while still
being retrievable via direct `get_control_for_org` lookup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
import pytest_asyncio  # noqa: F401 -- fixture discovery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    DSC_CORR_LINK_SOURCES,
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    IndustryType,
    OrganizationSize,
)
from idraa.models.organization import Organization
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services import controls as svc


async def _seed_org(db: AsyncSession) -> Organization:
    org = Organization(
        name="A",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db.add(org)
    await db.flush()
    return org


def _form() -> ControlForm:
    """Minimal ControlForm with one assignment (PR iota shape)."""
    return ControlForm(
        name="MFA",
        description="x",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.9,
                coverage=0.8,
                reliability=0.95,
            )
        ],
        annual_cost=Decimal("2000"),
    )


async def test_create_then_list_active(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    c = await svc.create_control(db_session, org_id=org.id, user_id=None, form=_form())
    await db_session.commit()
    listed = await svc.list_controls(db_session, org_id=org.id)
    assert [x.id for x in listed] == [c.id]


async def test_soft_delete_hides_from_list(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    c = await svc.create_control(db_session, org_id=org.id, user_id=None, form=_form())
    await svc.soft_delete_control(db_session, c, user_id=None)
    await db_session.commit()
    assert await svc.list_controls(db_session, org_id=org.id) == []
    # But still retrievable with include_deleted=True
    found = await svc.get_control_for_org(db_session, organization_id=org.id, control_id=c.id)
    assert found is not None and found.status is EntityStatus.DELETED


async def test_get_control_for_org_returns_none_cross_org(db_session: AsyncSession) -> None:
    """The org check now lives IN the service (issue #260 root cause), not just at
    each of routes/controls.py's 8 call sites — a control owned by org B must be
    invisible through org A's organization_id, at the service layer directly."""
    org_a = await _seed_org(db_session)
    org_b = await _seed_org(db_session)
    c = await svc.create_control(db_session, org_id=org_b.id, user_id=None, form=_form())
    await db_session.commit()

    cross_org = await svc.get_control_for_org(db_session, organization_id=org_a.id, control_id=c.id)
    assert cross_org is None

    same_org = await svc.get_control_for_org(db_session, organization_id=org_b.id, control_id=c.id)
    assert same_org is not None and same_org.id == c.id


async def test_get_control_for_org_returns_none_when_missing(db_session: AsyncSession) -> None:
    import uuid

    org = await _seed_org(db_session)
    result = await svc.get_control_for_org(
        db_session, organization_id=org.id, control_id=uuid.uuid4()
    )
    assert result is None


# ---------------------------------------------------------------------------
# PR iota: assignment-aware CRUD, count_unconfirmed, confirm_assignment
# ---------------------------------------------------------------------------


def _make_assignment_dto(**kwargs: object) -> ControlFunctionAssignmentDTO:
    defaults: dict[str, object] = {
        "sub_function": FairCamSubFunction.LEC_PREV_RESISTANCE,
        "capability_value": 0.75,
        "coverage": 0.8,
        "reliability": 0.9,
    }
    defaults.update(kwargs)
    return ControlFunctionAssignmentDTO(**defaults)  # type: ignore[arg-type]


def _make_control_form(**kwargs: object) -> ControlForm:
    defaults: dict[str, object] = {
        "name": "Test Firewall",
        "type": ControlType.TECHNICAL,
        "assignments": [_make_assignment_dto()],
    }
    defaults.update(kwargs)
    return ControlForm(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_control_iota_creates_assignment_row(db_session: AsyncSession) -> None:
    """create_control persists Control + ControlFunctionAssignment in same flush."""
    from sqlalchemy import select

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    org = await _seed_org(db_session)
    form = _make_control_form()
    control = await svc.create_control(db_session, org_id=org.id, user_id=None, form=form)
    await db_session.flush()

    rows = await db_session.execute(
        select(ControlFunctionAssignment).where(ControlFunctionAssignment.control_id == control.id)
    )
    assignments = list(rows.scalars().all())
    assert len(assignments) == 1
    assert assignments[0].sub_function == FairCamSubFunction.LEC_PREV_RESISTANCE
    assert assignments[0].capability_value == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_create_control_iota_confirmed_by_user_at_set(db_session: AsyncSession) -> None:
    """User-submitted create_control sets confirmed_by_user_at on the assignment (spec §8.1)."""
    org = await _seed_org(db_session)
    form = _make_control_form()
    control = await svc.create_control(db_session, org_id=org.id, user_id=None, form=form)
    await db_session.flush()
    # Refresh to populate the `assignments` relationship via async load
    # (Control doesn't inherit AsyncAttrs, so `awaitable_attrs` isn't available).
    await db_session.refresh(control, attribute_names=["assignments"])
    assert control.assignments[0].confirmed_by_user_at is not None


@pytest.mark.asyncio
async def test_create_control_rejects_client_supplied_derived_from(
    db_session: AsyncSession,
) -> None:
    """A client-supplied derived_from_assignment_id is refused (#268, spec §3.4).

    Replaces ``test_create_control_iota_rejects_derived_from_not_null`` (Decision 9 /
    B-NEW3, "reserved-but-unused"): the derived-row path is now LIVE, but only the
    server may write the link (resolved from ``dsc_corr_source`` on the same control);
    no row id is ever accepted from the client. The refusal lives in the pure
    ``validate_dsc_corr_link`` (the first statement of create/update).
    """
    # #199 slice 1 listed dsc_id_misaligned in `_LABEL_ONLY_SUB_FUNCTIONS`
    # (services/_view_model_helpers.py) BECAUSE no org-authored row carried
    # dsc_corr_misaligned; #268 made the derived row authorable and removed it from that
    # set (its zero is now run-scoped: control_zero_value_reason's run_has_* signals).
    # This test and test_dto_rejects_virtual_without_derived guard the client side.
    import uuid

    org = await _seed_org(db_session)
    dto = _make_assignment_dto(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        derived_from_assignment_id=uuid.uuid4(),
    )
    form = _make_control_form(assignments=[dto])
    with pytest.raises(ValueError, match="derived_from_assignment_id"):
        await svc.create_control(db_session, org_id=org.id, user_id=None, form=form)


@pytest.mark.asyncio
async def test_count_unconfirmed_assignments_returns_zero_for_fresh_create(
    db_session: AsyncSession,
) -> None:
    """User-submitted create: count_unconfirmed == 0 (assignment is confirmed)."""
    org = await _seed_org(db_session)
    form = _make_control_form()
    control = await svc.create_control(db_session, org_id=org.id, user_id=None, form=form)
    await db_session.flush()
    count = await svc.count_unconfirmed_assignments(db_session, control_id=control.id)
    assert count == 0


@pytest.mark.asyncio
async def test_confirm_assignment_sets_confirmed_fields(db_session: AsyncSession) -> None:
    """confirm_assignment sets confirmed_by_user_at, measured_by, measured_at."""

    org = await _seed_org(db_session)
    form = _make_control_form()
    control = await svc.create_control(db_session, org_id=org.id, user_id=None, form=form)
    await db_session.flush()

    await db_session.refresh(control, attribute_names=["assignments"])
    assignment = control.assignments[0]
    # Manually clear confirmed_by_user_at to simulate a backfilled row
    assignment.confirmed_by_user_at = None
    await db_session.flush()

    # user_id=None: the audit_log row's user_id FK→users.id allows NULL; we
    # don't have a seeded user fixture in this file. The test's primary
    # assertion is that confirm_assignment populates the three fields.
    result = await svc.confirm_assignment(
        db_session,
        assignment=assignment,
        user_id=None,
        ip_address="127.0.0.1",
    )
    assert result.confirmed_by_user_at is not None
    assert result.measured_by is None  # because we passed None
    assert result.measured_at is not None


# ---------------------------------------------------------------------------
# #268: derived dsc_corr_misaligned link — validation, create, confirm, maintenance
# ---------------------------------------------------------------------------

_IMPL = FairCamSubFunction.VMC_CORR_IMPLEMENTATION
_TERM = FairCamSubFunction.LEC_RESP_EVENT_TERMINATION
_RESIL = FairCamSubFunction.LEC_RESP_RESILIENCE
_RES = FairCamSubFunction.LEC_PREV_RESISTANCE
_DSC = FairCamSubFunction.DSC_CORR_MISALIGNED

# Capability values legal for each unit (ELAPSED_TIME: any >= 0; PROBABILITY: [0, 1]).
_CAP: dict[FairCamSubFunction, float] = {
    _IMPL: 3.0,
    _TERM: 2.0,
    _RESIL: 0.7,
    _RES: 0.75,
    FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION: 0.6,
}


def _linked_form(*subfns: FairCamSubFunction, name: str = "Linked") -> ControlForm:
    """Form with one assignment per sub-function; non-zero cost so the control is
    never a zero-cost maintenance candidate."""
    return _make_control_form(
        name=name,
        annual_cost=Decimal("1000"),
        assignments=[
            _make_assignment_dto(sub_function=sf, capability_value=_CAP.get(sf, 1.0))
            for sf in subfns
        ],
    )


async def _cfa_rows(
    db: AsyncSession, control_id: object
) -> dict[FairCamSubFunction, ControlFunctionAssignment]:
    rows = await db.execute(
        select(ControlFunctionAssignment).where(ControlFunctionAssignment.control_id == control_id)
    )
    return {r.sub_function: r for r in rows.scalars().all()}


async def _audit_rows(
    db: AsyncSession, action: str, entity_id: object | None = None
) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return list((await db.execute(stmt.order_by(AuditLog.timestamp, AuditLog.id))).scalars().all())


def _utc(dt: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; compare instants in UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _assert_session_clean(db: AsyncSession) -> None:
    assert not db.new
    assert not db.dirty
    assert not db.deleted


def _dicts(*subfns: FairCamSubFunction) -> list[dict[str, object]]:
    return [{"sub_function": sf, "derived_from_assignment_id": None} for sf in subfns]


def test_dsc_corr_link_sources_constant_is_exactly_the_three_eligible_sources() -> None:
    assert frozenset({_IMPL, _TERM, _RESIL}) == DSC_CORR_LINK_SOURCES


@pytest.mark.parametrize("source", [None, _IMPL, _TERM, _RESIL])
def test_validate_dsc_corr_link_accepts_eligible_present_source(
    source: FairCamSubFunction | None,
) -> None:
    svc.validate_dsc_corr_link(_dicts(_IMPL, _TERM, _RESIL, _RES), source)


@pytest.mark.parametrize(
    "ineligible",
    [
        FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION,
        FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
        _RES,
    ],
)
def test_validate_dsc_corr_link_refuses_ineligible_source(
    ineligible: FairCamSubFunction,
) -> None:
    with pytest.raises(ValueError, match="not an eligible source"):
        svc.validate_dsc_corr_link(_dicts(ineligible, _IMPL), ineligible)


def test_validate_dsc_corr_link_refuses_the_virtual_row_as_its_own_source() -> None:
    with pytest.raises(ValueError, match="not an eligible source"):
        svc.validate_dsc_corr_link(_dicts(_IMPL), _DSC)


def test_validate_dsc_corr_link_refuses_source_not_among_submitted() -> None:
    with pytest.raises(ValueError, match="no longer has"):
        svc.validate_dsc_corr_link(_dicts(_RES, _RESIL), _IMPL)


def test_validate_dsc_corr_link_refuses_submitted_dsc_corr_misaligned() -> None:
    with pytest.raises(ValueError, match="cannot be submitted"):
        svc.validate_dsc_corr_link(_dicts(_IMPL, _DSC), None)


def test_validate_dsc_corr_link_refuses_injected_derived_from_id() -> None:
    import uuid

    rows = _dicts(_IMPL, _RES)
    rows[1]["derived_from_assignment_id"] = uuid.uuid4()
    with pytest.raises(ValueError, match="derived_from_assignment_id"):
        svc.validate_dsc_corr_link(rows, _IMPL)


def test_validate_dsc_corr_link_is_pure() -> None:
    """The validator must not mutate its inputs (it runs before any state change)."""
    rows = _dicts(_IMPL, _RES)
    snapshot = [dict(r) for r in rows]
    svc.validate_dsc_corr_link(rows, _IMPL)
    assert rows == snapshot


@pytest.mark.asyncio
async def test_create_with_link_writes_derived_row_sentinels_and_audit(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session,
        org_id=org.id,
        user_id=user.id,
        form=_linked_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )
    rows = await _cfa_rows(db_session, control.id)
    assert set(rows) == {_IMPL, _RES, _DSC}
    source, derived = rows[_IMPL], rows[_DSC]
    assert derived.derived_from_assignment_id == source.id
    assert derived.capability_value is None
    assert derived.coverage == 0.0
    assert derived.reliability == 0.0
    # Form path: the authoring act confirms the derived row like every saved row.
    assert derived.confirmed_by_user_at is not None
    assert derived.measured_by == user.id
    assert derived.measured_at is not None

    audits = await _audit_rows(db_session, "control_function_assignment.create", derived.id)
    assert len(audits) == 1
    changes = dict(audits[0].changes)
    stamp = cast(list[Any], changes.pop("confirmed_by_user_at"))
    assert stamp[0] is None
    assert _utc(datetime.fromisoformat(stamp[1])) == _utc(derived.confirmed_by_user_at)
    assert changes == {
        "sub_function": [None, "dsc_corr_misaligned"],
        "derived_from_sub_function": [None, "vmc_corr_implementation"],
    }


@pytest.mark.asyncio
async def test_create_without_link_writes_no_derived_row(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session, org_id=org.id, user_id=user.id, form=_linked_form(_IMPL, _RES)
    )
    assert _DSC not in await _cfa_rows(db_session, control.id)


@pytest.mark.parametrize(
    ("subfns", "source", "match"),
    [
        pytest.param(
            (FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION, _IMPL),
            FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION,
            "not an eligible source",
            id="treatment-selection-ineligible",
        ),
        pytest.param(
            (FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, _IMPL),
            FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
            "not an eligible source",
            id="loss-reduction-ineligible",
        ),
        pytest.param((_RES, _RESIL), _IMPL, "no longer has", id="source-not-submitted"),
    ],
)
@pytest.mark.asyncio
async def test_create_refuses_bad_link_and_writes_nothing(
    db_session: AsyncSession,
    seed_org_user: Any,
    subfns: tuple[FairCamSubFunction, ...],
    source: FairCamSubFunction,
    match: str,
) -> None:
    org, user = await seed_org_user(db_session)
    _assert_session_clean(db_session)
    with pytest.raises(ValueError, match=match):
        await svc.create_control(
            db_session,
            org_id=org.id,
            user_id=user.id,
            form=_linked_form(*subfns),
            dsc_corr_source=source,
        )
    _assert_session_clean(db_session)
    assert (await db_session.execute(select(func.count()).select_from(Control))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(AuditLog))).scalar_one() == 0


@pytest.mark.asyncio
async def test_create_and_update_refuse_submitted_dsc_corr_misaligned(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """A submitted dsc_corr_misaligned assignment (the DTO only admits one WITH a
    derived_from id) is refused on both paths — the row is never client-authored."""
    import uuid

    org, user = await seed_org_user(db_session)
    form = _make_control_form(
        assignments=[
            _make_assignment_dto(sub_function=_IMPL, capability_value=3.0),
            _make_assignment_dto(
                sub_function=_DSC,
                capability_value=None,
                derived_from_assignment_id=uuid.uuid4(),
            ),
        ]
    )
    with pytest.raises(ValueError, match="cannot be submitted"):
        await svc.create_control(db_session, org_id=org.id, user_id=user.id, form=form)

    control = await svc.create_control(
        db_session, org_id=org.id, user_id=user.id, form=_linked_form(_IMPL)
    )
    await db_session.refresh(control, attribute_names=["assignments"])
    with pytest.raises(ValueError, match="cannot be submitted"):
        await svc.update_control(db_session, control=control, user_id=user.id, form=form)


@pytest.mark.asyncio
async def test_update_refuses_injected_derived_from_id(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    import uuid

    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session, org_id=org.id, user_id=user.id, form=_linked_form(_IMPL, _RES)
    )
    await db_session.refresh(control, attribute_names=["assignments"])
    bad = _make_control_form(
        assignments=[
            _make_assignment_dto(sub_function=_IMPL, capability_value=3.0),
            _make_assignment_dto(sub_function=_RES, derived_from_assignment_id=uuid.uuid4()),
        ]
    )
    with pytest.raises(ValueError, match="derived_from_assignment_id"):
        await svc.update_control(db_session, control=control, user_id=user.id, form=bad)


@pytest.mark.asyncio
async def test_refused_update_leaves_the_session_untouched(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """validate_dsc_corr_link is the first statement of update_control: a refusal
    happens before the name change, the merge or any audit row (the route's
    db.rollback() is defense in depth, not the mechanism)."""
    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session,
        org_id=org.id,
        user_id=user.id,
        form=_linked_form(_IMPL, _RES, name="Before"),
        dsc_corr_source=_IMPL,
    )
    await db_session.refresh(control, attribute_names=["assignments"])
    audit_count_before = (
        await db_session.execute(select(func.count()).select_from(AuditLog))
    ).scalar_one()
    rows_before = {sf: r.id for sf, r in (await _cfa_rows(db_session, control.id)).items()}
    _assert_session_clean(db_session)

    # Name changes, a source row is dropped, AND the link names the dropped source.
    with pytest.raises(ValueError, match="no longer has"):
        await svc.update_control(
            db_session,
            control=control,
            user_id=user.id,
            form=_linked_form(_RES, name="After"),
            dsc_corr_source=_IMPL,
        )
    _assert_session_clean(db_session)
    assert control.name == "Before"
    assert (
        await db_session.execute(select(func.count()).select_from(AuditLog))
    ).scalar_one() == audit_count_before
    assert {sf: r.id for sf, r in (await _cfa_rows(db_session, control.id)).items()} == rows_before


@pytest.mark.asyncio
async def test_linked_lec_response_control_spans_decision_support_domain(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """Intended (spec §3.1, R2): a linked control now performs a DSC function."""
    from idraa.models.enums import ControlDomain

    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session,
        org_id=org.id,
        user_id=user.id,
        form=_linked_form(_RESIL),
        dsc_corr_source=_RESIL,
    )
    await db_session.refresh(control, attribute_names=["assignments"])
    assert control.domains == frozenset({ControlDomain.LOSS_EVENT, ControlDomain.DECISION_SUPPORT})


async def _duplicate_of_linked(
    db: AsyncSession, seed_org_user: Any
) -> tuple[Any, Any, Control, Control]:
    """(org, user, original, clone) — the clone's derived row is NULL-confirmed."""
    org, user = await seed_org_user(db)
    original = await svc.create_control(
        db,
        org_id=org.id,
        user_id=user.id,
        form=_linked_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )
    await db.refresh(original, attribute_names=["assignments"])
    clone = await svc.duplicate_control(db, control=original, user_id=user.id)
    return org, user, original, clone


@pytest.mark.asyncio
async def test_confirm_assignment_on_derived_row_is_a_noop(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, _, clone = await _duplicate_of_linked(db_session, seed_org_user)
    derived = (await _cfa_rows(db_session, clone.id))[_DSC]
    assert derived.confirmed_by_user_at is None
    confirm_before = len(await _audit_rows(db_session, "control_function_assignment.confirm"))

    result = await svc.confirm_assignment(db_session, assignment=derived, user_id=user.id)

    assert result is derived
    assert derived.confirmed_by_user_at is None
    assert derived.measured_by is None
    assert derived.measured_at is None
    assert (
        len(await _audit_rows(db_session, "control_function_assignment.confirm")) == confirm_before
    )


@pytest.mark.asyncio
async def test_confirm_assignment_on_source_also_confirms_null_derived_row(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, _, clone = await _duplicate_of_linked(db_session, seed_org_user)
    rows = await _cfa_rows(db_session, clone.id)
    source, derived = rows[_IMPL], rows[_DSC]
    assert source.confirmed_by_user_at is None and derived.confirmed_by_user_at is None

    await svc.confirm_assignment(db_session, assignment=source, user_id=user.id)

    rows = await _cfa_rows(db_session, clone.id)  # re-read (narrowing-free)
    source, derived = rows[_IMPL], rows[_DSC]
    assert source.confirmed_by_user_at is not None
    assert derived.confirmed_by_user_at is not None
    assert derived.measured_by == user.id
    assert derived.measured_at is not None
    # The derived row has its OWN .confirm audit row (never folded into the source's).
    derived_audits = await _audit_rows(
        db_session, "control_function_assignment.confirm", derived.id
    )
    assert len(derived_audits) == 1
    stamp = cast(list[Any], derived_audits[0].changes["confirmed_by_user_at"])
    assert stamp[0] is None
    assert _utc(datetime.fromisoformat(stamp[1])) == _utc(derived.confirmed_by_user_at)
    source_audits = await _audit_rows(db_session, "control_function_assignment.confirm", source.id)
    assert len(source_audits) == 1


@pytest.mark.asyncio
async def test_confirm_assignment_on_unlinked_source_confirms_only_itself(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, _, clone = await _duplicate_of_linked(db_session, seed_org_user)
    rows = await _cfa_rows(db_session, clone.id)

    await svc.confirm_assignment(db_session, assignment=rows[_RES], user_id=user.id)

    assert rows[_RES].confirmed_by_user_at is not None
    assert rows[_DSC].confirmed_by_user_at is None
    assert len(await _audit_rows(db_session, "control_function_assignment.confirm")) == 1


@pytest.mark.asyncio
async def test_maintenance_confirming_every_listed_row_leaves_control_fully_confirmed(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """Snapshot-style "n of n": the maintenance page never lists the derived row, yet
    confirming every row it DOES list leaves no NULL-confirmed row behind."""
    from idraa.services.controls_maintenance import maintenance_summary

    org, user, _, clone = await _duplicate_of_linked(db_session, seed_org_user)
    assert await svc.count_unconfirmed_assignments(db_session, control_id=clone.id) == 3

    summary = await maintenance_summary(db_session, org.id)
    listed = [a for a in summary.unconfirmed_assignments if a.control_id == clone.id]
    assert {a.sub_function for a in listed} == {_IMPL, _RES}  # derived row not listed
    assert all(a.derived_from_assignment_id is None for a in listed)

    for a in listed:
        await svc.confirm_assignment(db_session, assignment=a, user_id=user.id)

    assert await svc.count_unconfirmed_assignments(db_session, control_id=clone.id) == 0
    after = await maintenance_summary(db_session, org.id)
    assert [a for a in after.unconfirmed_assignments if a.control_id == clone.id] == []


@pytest.mark.asyncio
async def test_maintenance_query_ignores_a_lone_null_confirmed_derived_row(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """A linked control whose ONLY unconfirmed row is the derived one is neither
    listed nor counted by the maintenance page / badge / import flash."""
    from idraa.services.controls_maintenance import maintenance_badge_count, maintenance_summary

    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session,
        org_id=org.id,
        user_id=user.id,
        form=_linked_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )
    derived = (await _cfa_rows(db_session, control.id))[_DSC]
    derived.confirmed_by_user_at = None
    await db_session.flush()
    assert await svc.count_unconfirmed_assignments(db_session, control_id=control.id) == 1

    summary = await maintenance_summary(db_session, org.id)
    assert summary.unconfirmed_assignments == []
    assert summary.unconfirmed_assignments_count == 0
    assert summary.total_needs_attention == 0
    assert await maintenance_badge_count(db_session, org.id) == 0


@pytest.mark.parametrize(
    "ineligible",
    [
        pytest.param(FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION, id="treatment-selection"),
        pytest.param(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, id="loss-reduction"),
    ],
)
@pytest.mark.asyncio
async def test_update_refuses_ineligible_source_and_leaves_the_session_clean(
    db_session: AsyncSession, seed_org_user: Any, ineligible: FairCamSubFunction
) -> None:
    """Update-path twin of ``test_create_refuses_bad_link_and_writes_nothing``: the
    ineligible source IS among the submitted assignments, so only the eligibility rule
    can refuse it. Refusal precedes the rename, the merge and any audit row."""
    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session,
        org_id=org.id,
        user_id=user.id,
        form=_linked_form(_IMPL, ineligible, _RES, name="Before"),
        dsc_corr_source=_IMPL,
    )
    await db_session.refresh(control, attribute_names=["assignments"])
    audit_count_before = (
        await db_session.execute(select(func.count()).select_from(AuditLog))
    ).scalar_one()
    rows_before = {sf: r.id for sf, r in (await _cfa_rows(db_session, control.id)).items()}
    derived_link_before = rows_before[_DSC]
    _assert_session_clean(db_session)

    with pytest.raises(ValueError, match="not an eligible source"):
        await svc.update_control(
            db_session,
            control=control,
            user_id=user.id,
            form=_linked_form(_IMPL, ineligible, _RES, name="After"),
            dsc_corr_source=ineligible,
        )

    _assert_session_clean(db_session)
    assert control.name == "Before"
    assert (
        await db_session.execute(select(func.count()).select_from(AuditLog))
    ).scalar_one() == audit_count_before
    rows_after = await _cfa_rows(db_session, control.id)
    assert {sf: r.id for sf, r in rows_after.items()} == rows_before
    assert rows_after[_DSC].id == derived_link_before
    assert rows_after[_DSC].derived_from_assignment_id == rows_after[_IMPL].id  # still on IMPL


@pytest.mark.asyncio
async def test_confirm_assignment_on_source_restamps_an_already_confirmed_derived_row(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """Pins spec §3.4 (and the plan-gate r6 ruling): confirming a source row confirms
    the derived row linked to it UNCONDITIONALLY — also when the derived row is already
    confirmed (periodic re-assessment). It gets a new timestamp equal to the source's,
    the confirming user, and exactly one new ``.confirm`` row with a truthful
    ``[prev, now]`` pair. A NULL-only rule would leave its measured_at/by older than the
    values it carries (it is scored from its source)."""
    org, user = await seed_org_user(db_session)
    control = await svc.create_control(
        db_session,
        org_id=org.id,
        user_id=user.id,
        form=_linked_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )
    rows = await _cfa_rows(db_session, control.id)
    source, derived = rows[_IMPL], rows[_DSC]
    derived_id = derived.id
    prev = derived.confirmed_by_user_at
    assert prev is not None  # form create confirmed it
    # create_control writes a .create (not a .confirm) row for the derived row.
    assert await _audit_rows(db_session, "control_function_assignment.confirm", derived_id) == []

    other_user_id = user.id  # same actor; the instant is what must change
    await svc.confirm_assignment(db_session, assignment=source, user_id=other_user_id)

    rows = await _cfa_rows(db_session, control.id)
    source, derived = rows[_IMPL], rows[_DSC]
    assert derived.confirmed_by_user_at is not None and source.confirmed_by_user_at is not None
    assert _utc(derived.confirmed_by_user_at) > _utc(prev)  # re-stamped, new instant
    assert _utc(derived.confirmed_by_user_at) == _utc(source.confirmed_by_user_at)
    assert derived.measured_by == source.measured_by == other_user_id
    assert derived.measured_at is not None and source.measured_at is not None
    assert _utc(derived.measured_at) == _utc(source.measured_at)

    audits = await _audit_rows(db_session, "control_function_assignment.confirm", derived_id)
    assert len(audits) == 1
    stamp = cast(list[Any], audits[0].changes["confirmed_by_user_at"])
    assert _utc(datetime.fromisoformat(stamp[0])) == _utc(prev)
    assert _utc(datetime.fromisoformat(stamp[1])) == _utc(derived.confirmed_by_user_at)
