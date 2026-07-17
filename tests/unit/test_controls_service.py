"""Control CRUD service — phase 1.2.2.

Verifies `create_control` + `list_controls` + `soft_delete_control` form
a coherent CRUD shape: creation round-trips via the list (active only by
default), and soft-delete hides the row from the active list while still
being retrievable via direct `get_control` lookup.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio  # noqa: F401 -- fixture discovery
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
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
    found = await svc.get_control(db_session, c.id)
    assert found is not None and found.status is EntityStatus.DELETED


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
async def test_create_control_iota_rejects_derived_from_not_null(db_session: AsyncSession) -> None:
    """Service rejects non-NULL derived_from_assignment_id (Decision 9, B-NEW3)."""
    import uuid

    org = await _seed_org(db_session)
    dto = _make_assignment_dto(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        derived_from_assignment_id=uuid.uuid4(),
    )
    form = _make_control_form(assignments=[dto])
    with pytest.raises(ValueError, match="reserved-but-unused"):
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
