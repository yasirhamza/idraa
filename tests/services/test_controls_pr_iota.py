"""Services-layer integration tests for PR iota control reshape.

Uses an in-memory SQLite AsyncEngine (not the httpx client) to exercise the
service layer end-to-end with real ORM objects and real SQLAlchemy flushing.

Covers (spec §11.1, §11.2):
  - create/update/delete/confirm flow with assignment CRUD
  - audit row shapes (entity_type, action, changes dict)
  - Bridge safe defaults: empty assignments, NULL capability, ELAPSED_TIME unit
  - _snapshot_control_v2 output shape and ControlSnapshotV2 round-trip
  - count_unconfirmed_assignments accuracy

10 test functions.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.db import Base
from idraa.models.audit_log import AuditLog
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlDomain, ControlType, FairCamSubFunction
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services import controls as svc

# Test fixtures


_ORG_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all PR iota tables created."""
    from idraa.db import strict_json_dumps

    # json_serializer mirrors get_engine() (#327): this fixture writes
    # AuditLog JSON columns — non-finite floats must fail at flush exactly
    # as they do in prod.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", echo=False, json_serializer=strict_json_dumps
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _dto(**kwargs: Any) -> ControlFunctionAssignmentDTO:
    defaults: dict[str, Any] = {
        "sub_function": FairCamSubFunction.LEC_PREV_RESISTANCE,
        "capability_value": 0.75,
        "coverage": 0.8,
        "reliability": 0.9,
    }
    defaults.update(kwargs)
    return ControlFunctionAssignmentDTO(**defaults)


def _form(**kwargs: Any) -> ControlForm:
    defaults: dict[str, Any] = {
        "name": "Firewall",
        "domain": ControlDomain.LOSS_EVENT,
        "type": ControlType.TECHNICAL,
        "assignments": [_dto()],
    }
    defaults.update(kwargs)
    return ControlForm(**defaults)


# CRUD + audit row tests


@pytest.mark.asyncio
async def test_create_persists_control_and_one_assignment(db: AsyncSession) -> None:
    """create_control inserts Control + 1 ControlFunctionAssignment in same flush."""
    control = await svc.create_control(db, org_id=_ORG_ID, user_id=None, form=_form())
    await db.flush()
    rows = await db.execute(
        select(ControlFunctionAssignment).where(ControlFunctionAssignment.control_id == control.id)
    )
    assignments = list(rows.scalars().all())
    assert len(assignments) == 1
    assert assignments[0].sub_function == FairCamSubFunction.LEC_PREV_RESISTANCE


@pytest.mark.asyncio
async def test_create_audit_row_has_correct_action(db: AsyncSession) -> None:
    """create_control writes audit row with action='control.create'."""
    await svc.create_control(db, org_id=_ORG_ID, user_id=None, form=_form())
    await db.flush()
    rows = await db.execute(select(AuditLog).where(AuditLog.action == "control.create"))
    audit_rows = list(rows.scalars().all())
    assert len(audit_rows) == 1
    assert "name" in audit_rows[0].changes


@pytest.mark.asyncio
async def test_create_assignment_audit_row_has_correct_action(db: AsyncSession) -> None:
    """create_control writes audit row with action='control_function_assignment.create'."""
    await svc.create_control(db, org_id=_ORG_ID, user_id=None, form=_form())
    await db.flush()
    rows = await db.execute(
        select(AuditLog).where(AuditLog.action == "control_function_assignment.create")
    )
    audit_rows = list(rows.scalars().all())
    assert len(audit_rows) == 1
    assert audit_rows[0].changes["sub_function"] == [
        None,
        FairCamSubFunction.LEC_PREV_RESISTANCE.value,
    ]


@pytest.mark.asyncio
async def test_confirm_assignment_sets_all_three_fields(db: AsyncSession) -> None:
    """confirm_assignment sets confirmed_by_user_at, measured_by, measured_at."""
    from idraa.models.enums import UserRole
    from idraa.models.user import User
    from idraa.services.auth import hash_password

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        organization_id=_ORG_ID,
        email="confirm-test@example.com",
        password_hash=hash_password("pw-12345678"),
        full_name="Confirm Test",
        role=UserRole.ANALYST,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    control = await svc.create_control(db, org_id=_ORG_ID, user_id=user_id, form=_form())
    await db.flush()
    await db.refresh(control, attribute_names=["assignments"])
    assignment = control.assignments[0]
    assignment.confirmed_by_user_at = None
    await db.flush()

    result = await svc.confirm_assignment(db, assignment=assignment, user_id=user_id)
    assert result.confirmed_by_user_at is not None
    assert result.measured_by == user_id
    assert result.measured_at is not None


@pytest.mark.asyncio
async def test_confirm_assignment_audit_row_shape(db: AsyncSession) -> None:
    """confirm_assignment audit row has action='control_function_assignment.confirm'
    and changes dict with [None, <iso ts>] for confirmed_by_user_at, and measured_by propagates."""
    from idraa.models.enums import UserRole
    from idraa.models.user import User
    from idraa.services.auth import hash_password

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        organization_id=_ORG_ID,
        email="audit-test@example.com",
        password_hash=hash_password("pw-12345678"),
        full_name="Audit Test",
        role=UserRole.ANALYST,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    control = await svc.create_control(db, org_id=_ORG_ID, user_id=user_id, form=_form())
    await db.flush()
    await db.refresh(control, attribute_names=["assignments"])
    assignment = control.assignments[0]
    assignment.confirmed_by_user_at = None
    await db.flush()

    await svc.confirm_assignment(db, assignment=assignment, user_id=user_id)
    await db.flush()

    rows = await db.execute(
        select(AuditLog).where(AuditLog.action == "control_function_assignment.confirm")
    )
    audit_rows = list(rows.scalars().all())
    assert len(audit_rows) == 1
    changes = audit_rows[0].changes
    confirmed_field = cast(list[object], changes["confirmed_by_user_at"])
    assert confirmed_field[0] is None
    assert isinstance(confirmed_field[1], str)
    # Verify measured_by propagated through confirm_assignment to audit changes
    measured_field = cast(list[object], changes["measured_by"])
    assert measured_field[1] == str(user_id)


@pytest.mark.asyncio
async def test_count_unconfirmed_returns_one_for_null_assignment(db: AsyncSession) -> None:
    """count_unconfirmed_assignments returns 1 when the assignment is unconfirmed."""
    control = await svc.create_control(db, org_id=_ORG_ID, user_id=None, form=_form())
    await db.flush()
    await db.refresh(control, attribute_names=["assignments"])
    control.assignments[0].confirmed_by_user_at = None
    await db.flush()
    count = await svc.count_unconfirmed_assignments(db, control_id=control.id)
    assert count == 1


# Bridge safe-default tests

# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_bridge_returns_safe_default_for_empty_assignments


@pytest.mark.asyncio
async def test_bridge_passes_through_null_capability(db: AsyncSession) -> None:
    """_v3_to_fair_cam_control passes NULL capability_value through (issue #209).

    The stale NULL-reject gate (T11 PR κ / paranoid-review fix S3) is removed:
    fair_cam handles NULL via its documented opeff(median)=0.5 midpoint anchor
    (``_null_safe_default``). The adapter must build the fair_cam assignment with
    ``capability_value is None`` and NOT raise, so the run can complete at the
    midpoint instead of hard-failing.
    """
    from idraa.services.run_executor import _v3_to_fair_cam_control

    control = await svc.create_control(db, org_id=_ORG_ID, user_id=None, form=_form())
    await db.flush()
    await db.refresh(control, attribute_names=["assignments"])
    control.assignments[0].capability_value = None

    fc = _v3_to_fair_cam_control(control)
    assert fc.assignments[0].capability_value is None


# Test superseded by tests/integration/test_run_executor_adapter_lambda.py — F1 (PR λ)
# test_bridge_returns_safe_default_for_elapsed_time_unit


# Snapshot writer round-trip


@pytest.mark.asyncio
async def test_snapshot_control_v2_round_trips_through_pydantic(db: AsyncSession) -> None:
    """_snapshot_control_v2 returns a ControlSnapshotV2 Pydantic model (T13 rewrite)."""
    from idraa.schemas.run_snapshot import ControlSnapshotV2
    from idraa.services.run_executor import _snapshot_control_v2

    control = await svc.create_control(db, org_id=_ORG_ID, user_id=None, form=_form())
    await db.flush()
    await db.refresh(control, attribute_names=["assignments"])

    snap = _snapshot_control_v2(control)
    assert isinstance(snap, ControlSnapshotV2)
    assert snap.snapshot_version == 2
    assert len(snap.assignments) == 1
    assert snap.assignments[0].sub_function == FairCamSubFunction.LEC_PREV_RESISTANCE
    assert snap.assignments[0].capability_value == pytest.approx(0.75)

    # Ensure it can still be serialised to a JSON-compatible dict for DB storage.
    snapshot_dict = snap.model_dump(mode="json")
    assert snapshot_dict["snapshot_version"] == 2
    assert "assignments" in snapshot_dict
