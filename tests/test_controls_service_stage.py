"""create/update/duplicate_control persist + audit implementation_stage (#395).

Test-only proof for plan Task 4. Fixtures (`db_session`, `organization`,
`admin_user`) and the AuditLog query idiom mirror the sibling service-audit
test ``tests/services/test_controls_clear_capability_audit.py``.

The plan's placeholder ``sole_org`` does not exist in this repo's fixture set;
the real org fixture is ``organization`` (tests/conftest.py). create/update
take ``user_id`` so we thread ``admin_user.id`` to keep the audit FK valid.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.enums import ControlImplementationStage, ControlType, FairCamSubFunction
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services import controls as svc


def _form(stage: ControlImplementationStage) -> ControlForm:
    return ControlForm(
        name="staged control",
        type=ControlType.TECHNICAL,
        implementation_stage=stage,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_AVOIDANCE,
                capability_value=0.5,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )


async def _latest_audit_for(db: AsyncSession, control_id: uuid.UUID) -> AuditLog:
    """Most-recent ``control.update`` audit row for a control id.

    Mirrors the ``select(AuditLog).where(...)`` idiom from
    ``tests/services/test_controls_clear_capability_audit.py``; the diff lives
    in the ``changes`` JSON column.
    """
    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "control")
                .where(AuditLog.entity_id == control_id)
                .where(AuditLog.action == "control.update")
                .order_by(AuditLog.timestamp.desc())
            )
        )
        .scalars()
        .all()
    )
    assert rows, "expected a control.update audit row"
    return rows[0]


@pytest.mark.asyncio
async def test_create_persists_stage(
    db_session: AsyncSession, organization: Organization, admin_user: User
) -> None:
    c = await svc.create_control(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        form=_form(ControlImplementationStage.PLANNED),
    )
    await db_session.flush()
    assert c.implementation_stage is ControlImplementationStage.PLANNED


@pytest.mark.asyncio
async def test_update_changes_stage(
    db_session: AsyncSession, organization: Organization, admin_user: User
) -> None:
    c = await svc.create_control(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        form=_form(ControlImplementationStage.ACTIVE),
    )
    await db_session.flush()
    # update_control reads control.assignments (lazy relationship); load it
    # eagerly to avoid a MissingGreenlet lazy-load under async (sibling-test idiom).
    await db_session.refresh(c, attribute_names=["assignments"])
    await svc.update_control(
        db_session,
        control=c,
        user_id=admin_user.id,
        form=_form(ControlImplementationStage.IN_PROJECT),
    )
    assert c.implementation_stage is ControlImplementationStage.IN_PROJECT


@pytest.mark.asyncio
async def test_update_audits_stage_change(
    db_session: AsyncSession, organization: Organization, admin_user: User
) -> None:
    # Plan-gate Sec-NTH: a stage demotion silently raises modeled risk, so the
    # transition must land in the audit log.
    c = await svc.create_control(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        form=_form(ControlImplementationStage.ACTIVE),
    )
    await db_session.flush()
    await db_session.refresh(c, attribute_names=["assignments"])
    await svc.update_control(
        db_session,
        control=c,
        user_id=admin_user.id,
        form=_form(ControlImplementationStage.PLANNED),
    )
    audit = await _latest_audit_for(db_session, c.id)
    assert "implementation_stage" in audit.changes
    assert audit.changes["implementation_stage"] == ["active", "planned"]


@pytest.mark.asyncio
async def test_duplicate_carries_stage(
    db_session: AsyncSession, organization: Organization, admin_user: User
) -> None:
    # Plan-gate Arch-NTH: duplicate_control reflects all columns, so a clone of
    # a planned control stays planned. Pin it so a future excludes-set change
    # can't silently reset cloned controls to active.
    c = await svc.create_control(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        form=_form(ControlImplementationStage.IN_PROJECT),
    )
    await db_session.flush()
    await db_session.refresh(c, attribute_names=["assignments"])
    clone = await svc.duplicate_control(db_session, control=c, user_id=admin_user.id)
    assert isinstance(clone, Control)
    assert clone.implementation_stage is ControlImplementationStage.IN_PROJECT
