"""#268 — lifecycle of the derived ``dsc_corr_misaligned`` assignment row.

A derived row stores only a link (``derived_from_assignment_id``) to another row
of the SAME control, with sentinels capability NULL / coverage 0.0 / reliability
0.0 (fail-closed). These tests drive the service write paths end-to-end against a
real SQLite session with ``PRAGMA foreign_keys=ON`` (the ``db_session`` fixture
installs the production PRAGMAs):

  create / unchanged save / re-point / clear / duplicate / delete ordering.

DELETE ORDERING. SQLAlchemy sorts the DELETEs of one flush by primary key, not by
call order. With foreign keys on, deleting a source row while its derived row still
exists fires the FK's ``ON DELETE SET NULL``, which violates
``ck_cfa_virtual_requires_derived`` (a ``dsc_corr_misaligned`` row needs a non-NULL
link). Every path that deletes rows must therefore delete the derived row and
``flush()`` BEFORE deleting any other row of the control. The delete-path tests below
seed the rows with FIXED ids in BOTH orders (source id < derived id and source id >
derived id) so the primary-key sort cannot hide a missing ordering.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services import controls as svc
from idraa.services.audit import AuditWriter

_IMPL = FairCamSubFunction.VMC_CORR_IMPLEMENTATION
_TERM = FairCamSubFunction.LEC_RESP_EVENT_TERMINATION
_RESIL = FairCamSubFunction.LEC_RESP_RESILIENCE
_RES = FairCamSubFunction.LEC_PREV_RESISTANCE
_DSC = FairCamSubFunction.DSC_CORR_MISALIGNED

_CAP: dict[FairCamSubFunction, float] = {_IMPL: 3.0, _TERM: 2.0, _RESIL: 0.7, _RES: 0.75}

# Fixed ids (never uuid4 in a parametrize id). The two orderings of (source, derived)
# are what the primary-key-sorted DELETE flush is sensitive to.
_LOW = uuid.UUID("00000000-0000-4000-8000-000000000001")
_HIGH = uuid.UUID("ffffffff-ffff-4fff-bfff-fffffffffffe")
_MID_A = uuid.UUID("7fffffff-ffff-4fff-8fff-00000000000a")
_MID_B = uuid.UUID("7fffffff-ffff-4fff-8fff-00000000000b")
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_BOTH_ORDERS = pytest.mark.parametrize(
    ("source_id", "derived_id"),
    [
        pytest.param(_LOW, _HIGH, id="source_id_lt_derived_id"),
        pytest.param(_HIGH, _LOW, id="source_id_gt_derived_id"),
    ],
)


def _form(*subfns: FairCamSubFunction, name: str = "Linked") -> ControlForm:
    return ControlForm(
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("1000"),
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=sf, capability_value=_CAP[sf], coverage=0.8, reliability=0.9
            )
            for sf in subfns
        ],
    )


async def _rows(db: AsyncSession, control_id: uuid.UUID) -> dict[FairCamSubFunction, Any]:
    result = await db.execute(
        select(ControlFunctionAssignment).where(ControlFunctionAssignment.control_id == control_id)
    )
    return {r.sub_function: r for r in result.scalars().all()}


async def _audits(
    db: AsyncSession, action: str, entity_id: uuid.UUID | None = None
) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return list((await db.execute(stmt.order_by(AuditLog.timestamp, AuditLog.id))).scalars().all())


def _utc(dt: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; compare instants in UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _assert_stamp(pair: list[Any], prev: datetime | None, current: datetime) -> None:
    """A ``confirmed_by_user_at`` audit pair is ``[prev, now]`` (ISO strings or None)."""
    assert len(pair) == 2
    if prev is None:
        assert pair[0] is None
    else:
        assert _utc(datetime.fromisoformat(pair[0])) == _utc(prev)
    assert _utc(datetime.fromisoformat(pair[1])) == _utc(current)


async def _audit_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()


async def _create_linked(
    db: AsyncSession,
    seed_org_user: Any,
    *subfns: FairCamSubFunction,
    source: FairCamSubFunction | None,
) -> tuple[Any, Any, Control]:
    org, user = await seed_org_user(db)
    control = await svc.create_control(
        db, org_id=org.id, user_id=user.id, form=_form(*subfns), dsc_corr_source=source
    )
    await db.refresh(control, attribute_names=["assignments"])
    return org, user, control


async def _seed_fixed(
    db: AsyncSession,
    seed_org_user: Any,
    *,
    source_sf: FairCamSubFunction,
    source_id: uuid.UUID,
    derived_id: uuid.UUID,
    others: dict[FairCamSubFunction, uuid.UUID],
) -> tuple[Any, Any, Control]:
    """A control with a derived row, rows inserted with FIXED primary keys."""
    assert source_id != derived_id
    org, user = await seed_org_user(db)
    control = Control(
        organization_id=org.id,
        created_by=None,
        name="Seeded",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("1000"),
    )
    db.add(control)
    await db.flush()
    for sf, row_id in [(source_sf, source_id), *others.items()]:
        db.add(
            ControlFunctionAssignment(
                id=row_id,
                control_id=control.id,
                organization_id=org.id,
                sub_function=sf,
                capability_value=_CAP[sf],
                coverage=0.8,
                reliability=0.9,
                confirmed_by_user_at=_T0,
                measured_by=None,
                measured_at=_T0,
            )
        )
    await db.flush()  # the source row must exist before the row that links to it
    db.add(
        ControlFunctionAssignment(
            id=derived_id,
            control_id=control.id,
            organization_id=org.id,
            sub_function=_DSC,
            capability_value=None,
            coverage=0.0,
            reliability=0.0,
            derived_from_assignment_id=source_id,
            confirmed_by_user_at=_T0,
            measured_by=None,
            measured_at=_T0,
        )
    )
    await db.flush()
    await db.refresh(control, attribute_names=["assignments"])
    return org, user, control


_DELETE_PAYLOAD_IMPL = {
    "sub_function": ["dsc_corr_misaligned", None],
    "derived_from_sub_function": ["vmc_corr_implementation", None],
}


# ---------------------------------------------------------------------------
# Unchanged save, re-point, clear, add (no id-order sensitivity)
# ---------------------------------------------------------------------------


async def test_unchanged_save_keeps_derived_row_and_confirms_it(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, control = await _create_linked(db_session, seed_org_user, _IMPL, _RES, source=_IMPL)
    before = (await _rows(db_session, control.id))[_DSC]
    derived_id, prev = before.id, before.confirmed_by_user_at
    assert prev is not None

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )

    rows = await _rows(db_session, control.id)
    after = rows[_DSC]
    assert after.id == derived_id  # same row, not recreated
    assert after.derived_from_assignment_id == rows[_IMPL].id
    assert _utc(after.confirmed_by_user_at) >= _utc(prev)
    assert after.measured_by == user.id
    confirms = await _audits(db_session, "control_function_assignment.confirm", derived_id)
    assert len(confirms) == 1
    assert set(confirms[0].changes) == {"confirmed_by_user_at"}
    _assert_stamp(
        cast(list[Any], confirms[0].changes["confirmed_by_user_at"]),
        prev,
        after.confirmed_by_user_at,
    )
    # Nothing else was written for the derived row beyond its original create.
    for action in ("update", "delete", "clear"):
        assert await _audits(db_session, f"control_function_assignment.{action}", derived_id) == []
    assert len(await _audits(db_session, "control_function_assignment.create", derived_id)) == 1


async def test_unchanged_save_confirms_a_derived_row_a_duplicate_left_null(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, original = await _create_linked(db_session, seed_org_user, _IMPL, _RES, source=_IMPL)
    clone = await svc.duplicate_control(db_session, control=original, user_id=user.id)
    await db_session.refresh(clone, attribute_names=["assignments"])
    derived = (await _rows(db_session, clone.id))[_DSC]
    derived_id = derived.id
    assert derived.confirmed_by_user_at is None

    await svc.update_control(
        db_session,
        control=clone,
        user_id=user.id,
        form=_form(_IMPL, _RES, name="Linked (copy)"),
        dsc_corr_source=_IMPL,
    )

    after = (await _rows(db_session, clone.id))[_DSC]
    assert after.id == derived_id
    assert after.confirmed_by_user_at is not None
    assert after.measured_by == user.id
    assert after.measured_at is not None
    confirms = await _audits(db_session, "control_function_assignment.confirm", derived_id)
    assert len(confirms) == 1
    assert set(confirms[0].changes) == {"confirmed_by_user_at"}
    _assert_stamp(
        cast(list[Any], confirms[0].changes["confirmed_by_user_at"]),
        None,
        after.confirmed_by_user_at,
    )


async def test_repoint_updates_the_same_row_and_audits_the_change(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, control = await _create_linked(db_session, seed_org_user, _IMPL, _RESIL, source=_IMPL)
    derived_id = (await _rows(db_session, control.id))[_DSC].id

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_IMPL, _RESIL),
        dsc_corr_source=_RESIL,
    )

    rows = await _rows(db_session, control.id)
    assert rows[_DSC].id == derived_id  # re-pointed in place
    assert rows[_DSC].derived_from_assignment_id == rows[_RESIL].id
    updates = await _audits(db_session, "control_function_assignment.update", derived_id)
    assert len(updates) == 1
    assert updates[0].changes == {
        "derived_from_sub_function": ["vmc_corr_implementation", "lec_resp_resilience"]
    }
    assert await _audits(db_session, "control_function_assignment.delete", derived_id) == []
    # Save = confirm applies to the re-pointed row too.
    assert len(await _audits(db_session, "control_function_assignment.confirm", derived_id)) == 1


@_BOTH_ORDERS
async def test_clearing_the_link_deletes_the_derived_row_with_a_pinned_audit(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    """Link cleared, source kept: only the derived row goes (fixed ids, both orders)."""
    _, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RES: _MID_A},
    )

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_IMPL, _RES),
        dsc_corr_source=None,
    )
    await db_session.commit()

    rows = await _rows(db_session, control.id)
    assert {sf: r.id for sf, r in rows.items()} == {_IMPL: source_id, _RES: _MID_A}
    deletes = await _audits(db_session, "control_function_assignment.delete", derived_id)
    assert len(deletes) == 1
    assert deletes[0].changes == _DELETE_PAYLOAD_IMPL


async def test_update_default_means_no_link(db_session: AsyncSession, seed_org_user: Any) -> None:
    """``update_control`` without ``dsc_corr_source`` is "no link" (default None)."""
    _, user, control = await _create_linked(db_session, seed_org_user, _IMPL, _RES, source=_IMPL)

    await svc.update_control(db_session, control=control, user_id=user.id, form=_form(_IMPL, _RES))

    assert _DSC not in await _rows(db_session, control.id)


async def test_adding_a_link_to_an_unlinked_control_creates_a_confirmed_row(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, control = await _create_linked(db_session, seed_org_user, _IMPL, _RES, source=None)
    assert _DSC not in await _rows(db_session, control.id)

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )

    rows = await _rows(db_session, control.id)
    derived = rows[_DSC]
    assert derived.derived_from_assignment_id == rows[_IMPL].id
    assert (derived.capability_value, derived.coverage, derived.reliability) == (None, 0.0, 0.0)
    creates = await _audits(db_session, "control_function_assignment.create", derived.id)
    assert len(creates) == 1
    changes = dict(creates[0].changes)
    _assert_stamp(
        cast(list[Any], changes.pop("confirmed_by_user_at")), None, derived.confirmed_by_user_at
    )
    assert changes == {
        "sub_function": [None, "dsc_corr_misaligned"],
        "derived_from_sub_function": [None, "vmc_corr_implementation"],
    }


async def test_form_save_stamps_derived_row_with_the_same_instant_as_its_siblings(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """One authoring act, one instant: on create AND on an unchanged save the derived
    row's confirmed_by_user_at / measured_at (and the instant in its audit payload)
    equal those of the rows saved in the same call."""
    _, user, control = await _create_linked(db_session, seed_org_user, _IMPL, _RES, source=_IMPL)

    rows = await _rows(db_session, control.id)
    stamp = _utc(rows[_DSC].confirmed_by_user_at)
    for sf in (_IMPL, _RES):
        assert _utc(rows[sf].confirmed_by_user_at) == stamp
        assert _utc(rows[sf].measured_at) == _utc(rows[_DSC].measured_at) == stamp
    creates = await _audits(db_session, "control_function_assignment.create", rows[_DSC].id)
    _assert_stamp(
        cast(list[Any], creates[0].changes["confirmed_by_user_at"]),
        None,
        rows[_DSC].confirmed_by_user_at,
    )

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )

    rows = await _rows(db_session, control.id)
    stamp2 = _utc(rows[_DSC].confirmed_by_user_at)
    assert stamp2 > stamp
    for sf in (_IMPL, _RES):
        assert _utc(rows[sf].confirmed_by_user_at) == stamp2
        assert _utc(rows[sf].measured_at) == _utc(rows[_DSC].measured_at) == stamp2
    # The derived row's .confirm payload carries the same instant as a sibling's.
    derived_confirm = (
        await _audits(db_session, "control_function_assignment.confirm", rows[_DSC].id)
    )[0]
    sibling_confirm = (
        await _audits(db_session, "control_function_assignment.confirm", rows[_RES].id)
    )[0]
    derived_pair = cast(list[Any], derived_confirm.changes["confirmed_by_user_at"])
    sibling_pair = cast(list[Any], sibling_confirm.changes["confirmed_by_user_at"])
    assert derived_pair[1] == sibling_pair[1]


# ---------------------------------------------------------------------------
# Delete paths — BOTH primary-key orders (fixed ids)
# ---------------------------------------------------------------------------


@_BOTH_ORDERS
async def test_source_removed_and_link_cleared_deletes_derived_first(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    _, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RES: _MID_A},
    )

    # No IntegrityError: the derived row is deleted and flushed before the source.
    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_RES),
        dsc_corr_source=None,
    )
    await db_session.commit()

    assert set(await _rows(db_session, control.id)) == {_RES}
    derived_deletes = await _audits(db_session, "control_function_assignment.delete", derived_id)
    assert [a.changes for a in derived_deletes] == [_DELETE_PAYLOAD_IMPL]
    source_deletes = await _audits(db_session, "control_function_assignment.delete", source_id)
    assert [a.changes for a in source_deletes] == [
        {"sub_function": ["vmc_corr_implementation", None]}
    ]


@_BOTH_ORDERS
async def test_source_removed_and_link_repointed_recreates_row_against_new_source(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    _, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RESIL: _MID_A, _RES: _MID_B},
    )

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_RESIL, _RES),
        dsc_corr_source=_RESIL,
    )
    await db_session.commit()

    rows = await _rows(db_session, control.id)
    assert set(rows) == {_RESIL, _RES, _DSC}
    new_derived = rows[_DSC]
    assert new_derived.id != derived_id  # recreated, not re-pointed
    assert new_derived.derived_from_assignment_id == _MID_A  # the RESIL row
    old_deletes = await _audits(db_session, "control_function_assignment.delete", derived_id)
    assert [a.changes for a in old_deletes] == [_DELETE_PAYLOAD_IMPL]
    creates = await _audits(db_session, "control_function_assignment.create", new_derived.id)
    assert len(creates) == 1
    changes = dict(creates[0].changes)
    _assert_stamp(
        cast(list[Any], changes.pop("confirmed_by_user_at")), None, new_derived.confirmed_by_user_at
    )
    assert changes == {
        "sub_function": [None, "dsc_corr_misaligned"],
        "derived_from_sub_function": [None, "lec_resp_resilience"],
    }


@_BOTH_ORDERS
async def test_source_retyped_in_the_same_save_recreates_row_against_the_new_row(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    """The source is replaced by a NEW row (a different eligible sub-function) in one
    submission: the derived row is deleted first, the merge inserts the new source,
    then the link is recreated against the freshly inserted row."""
    _, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RES: _MID_A},
    )

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_TERM, _RES),
        dsc_corr_source=_TERM,
    )
    await db_session.commit()

    rows = await _rows(db_session, control.id)
    assert set(rows) == {_TERM, _RES, _DSC}
    assert rows[_DSC].derived_from_assignment_id == rows[_TERM].id
    assert rows[_DSC].id != derived_id


@_BOTH_ORDERS
async def test_link_naming_a_removed_source_is_refused_and_nothing_changes(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    _, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RES: _MID_A},
    )
    audits_before = await _audit_count(db_session)

    with pytest.raises(ValueError, match="no longer has"):
        await svc.update_control(
            db_session,
            control=control,
            user_id=user.id,
            form=_form(_RES, name="Renamed"),
            dsc_corr_source=_IMPL,
        )

    assert not db_session.new and not db_session.dirty and not db_session.deleted
    assert control.name == "Seeded"
    assert await _audit_count(db_session) == audits_before
    rows = await _rows(db_session, control.id)
    assert {sf: r.id for sf, r in rows.items()} == {
        _IMPL: source_id,
        _DSC: derived_id,
        _RES: _MID_A,
    }
    assert rows[_DSC].derived_from_assignment_id == source_id


@_BOTH_ORDERS
async def test_delete_derived_rows_first_deletes_flushes_and_returns_the_source(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    org, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RES: _MID_A},
    )
    audit = AuditWriter(db_session)

    removed = await svc._delete_derived_rows_first(
        db_session,
        control.id,
        organization_id=org.id,
        audit=audit,
        user_id=user.id,
        ip_address=None,
    )

    assert removed is _IMPL
    assert not db_session.deleted  # flushed, not merely marked
    assert set(await _rows(db_session, control.id)) == {_IMPL, _RES}
    deletes = await _audits(db_session, "control_function_assignment.delete", derived_id)
    assert [a.changes for a in deletes] == [_DELETE_PAYLOAD_IMPL]

    # Idempotent: no derived row left -> None, no second audit row.
    again = await svc._delete_derived_rows_first(
        db_session,
        control.id,
        organization_id=org.id,
        audit=audit,
        user_id=user.id,
        ip_address=None,
    )
    assert again is None
    assert len(await _audits(db_session, "control_function_assignment.delete", derived_id)) == 1


@_BOTH_ORDERS
async def test_unchanged_save_of_a_seeded_control_keeps_both_rows(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    """Control: the fixed-id fixtures themselves round-trip an unchanged save."""
    _, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RES: _MID_A},
    )

    await svc.update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=_form(_IMPL, _RES),
        dsc_corr_source=_IMPL,
    )

    rows = await _rows(db_session, control.id)
    assert {sf: r.id for sf, r in rows.items()} == {
        _IMPL: source_id,
        _DSC: derived_id,
        _RES: _MID_A,
    }


# ---------------------------------------------------------------------------
# Duplicate
# ---------------------------------------------------------------------------


async def test_duplicate_links_the_clone_to_its_own_source_row(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """N >= 3 non-derived rows: the clone keeps all of them (adapter iteration
    contract) and its derived row targets the CLONE's source, never the original's."""
    _, user, original = await _create_linked(
        db_session, seed_org_user, _IMPL, _RESIL, _RES, source=_IMPL
    )

    clone = await svc.duplicate_control(db_session, control=original, user_id=user.id)

    orig_rows = await _rows(db_session, original.id)
    clone_rows = await _rows(db_session, clone.id)
    assert set(clone_rows) == set(orig_rows) == {_IMPL, _RESIL, _RES, _DSC}
    for sf in (_IMPL, _RESIL, _RES):  # non-derived rows copied with their values
        assert clone_rows[sf].id != orig_rows[sf].id
        assert clone_rows[sf].capability_value == orig_rows[sf].capability_value
        assert clone_rows[sf].confirmed_by_user_at is None
    assert clone_rows[_DSC].id != orig_rows[_DSC].id
    assert clone_rows[_DSC].derived_from_assignment_id == clone_rows[_IMPL].id
    assert clone_rows[_DSC].derived_from_assignment_id != orig_rows[_IMPL].id
    # The original is untouched.
    assert orig_rows[_DSC].derived_from_assignment_id == orig_rows[_IMPL].id
    assert orig_rows[_DSC].confirmed_by_user_at is not None
    # Clone's derived row: sentinels, NOT an authoring act -> unconfirmed.
    d = clone_rows[_DSC]
    assert (d.capability_value, d.coverage, d.reliability) == (None, 0.0, 0.0)
    assert (d.confirmed_by_user_at, d.measured_by, d.measured_at) == (None, None, None)
    creates = await _audits(db_session, "control_function_assignment.create", d.id)
    assert len(creates) == 1
    assert creates[0].changes == {
        "sub_function": [None, "dsc_corr_misaligned"],
        "derived_from_sub_function": [None, "vmc_corr_implementation"],
        "confirmed_by_user_at": [None, None],
    }


async def test_duplicate_of_an_unlinked_control_adds_no_derived_row(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    _, user, original = await _create_linked(db_session, seed_org_user, _IMPL, _RES, source=None)

    clone = await svc.duplicate_control(db_session, control=original, user_id=user.id)

    assert set(await _rows(db_session, clone.id)) == {_IMPL, _RES}


# ---------------------------------------------------------------------------
# _sync_derived_row edge contracts
# ---------------------------------------------------------------------------


@_BOTH_ORDERS
async def test_sync_with_no_source_removes_a_leftover_derived_row_first(
    db_session: AsyncSession, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    org, user, control = await _seed_fixed(
        db_session,
        seed_org_user,
        source_sf=_IMPL,
        source_id=source_id,
        derived_id=derived_id,
        others={_RES: _MID_A},
    )

    await svc._sync_derived_row(
        db_session,
        control.id,
        org.id,
        None,
        confirm=True,
        audit=AuditWriter(db_session),
        user_id=user.id,
        ip_address=None,
    )

    assert set(await _rows(db_session, control.id)) == {_IMPL, _RES}
    deletes = await _audits(db_session, "control_function_assignment.delete", derived_id)
    assert [a.changes for a in deletes] == [_DELETE_PAYLOAD_IMPL]


async def test_sync_refuses_a_source_the_control_does_not_have(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    org, user, control = await _create_linked(db_session, seed_org_user, _RES, source=None)

    with pytest.raises(ValueError, match="has no vmc_corr_implementation row"):
        await svc._sync_derived_row(
            db_session,
            control.id,
            org.id,
            _IMPL,
            confirm=True,
            audit=AuditWriter(db_session),
            user_id=user.id,
            ip_address=None,
        )

    assert set(await _rows(db_session, control.id)) == {_RES}


async def test_unlinked_save_writes_exactly_the_audit_rows_it_did_before(
    db_session: AsyncSession, seed_org_user: Any
) -> None:
    """Regression guard: a control with no derived row and no link saves as before —
    one ``.confirm`` per submitted row, no derived-row audit rows of any kind."""
    _, user, control = await _create_linked(db_session, seed_org_user, _IMPL, _RES, source=None)
    before = await _audit_count(db_session)

    await svc.update_control(db_session, control=control, user_id=user.id, form=_form(_IMPL, _RES))

    assert await _audit_count(db_session) - before == 2  # one .confirm per submitted row
    assert _DSC not in await _rows(db_session, control.id)
