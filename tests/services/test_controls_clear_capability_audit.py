"""Issue #129 T6 -- distinct audit action for capability clear.

When a user clears a capability_value (non-NULL -> NULL) via the
maintenance/edit form, the service emits
``control_function_assignment.clear`` instead of the generic
``control_function_assignment.update``. This lets audit reviewers grep
the audit log for silent ALE-model degradations (NULL falls back to the
operational-effectiveness midpoint at t = tau * ln(2), per the warning
modal in T6 §3).

Non-NULL -> non-NULL changes continue to emit
``control_function_assignment.update`` unchanged.

Mirrors fixture patterns from ``tests/unit/test_controls_service_lambda.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from idraa.models.audit_log import AuditLog
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services.controls import create_control, update_control


@pytest.mark.asyncio
async def test_clear_capability_emits_clear_action(db_session, organization, admin_user):
    """Setting capability_value to NULL on an existing assignment emits
    ``control_function_assignment.clear`` (NOT
    ``control_function_assignment.update``).

    The changes dict still records the [old, new] = [0.5, None] diff so
    the audit-trail UI can render the transition; only the action verb
    differs from the generic update path.
    """
    db, org, user = db_session, organization, admin_user

    # Create with one assignment at capability_value = 0.5
    create_form = ControlForm(
        name="Clear Test",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.5,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    control = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Clear the capability_value (non-NULL -> NULL)
    clear_form = ControlForm(
        name=control.name,
        description="",
        type=control.type,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=None,  # cleared
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    await update_control(db, control=control, user_id=user.id, form=clear_form)
    await db.commit()

    rows = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.entity_type == "control_function_assignment")
            )
        )
        .scalars()
        .all()
    )
    actions = [r.action for r in rows]

    # Should see: create (initial) + clear (the NULL transition) + confirm (save=confirm)
    assert "control_function_assignment.clear" in actions, (
        f"expected .clear action for non-NULL -> NULL transition; got actions={actions}"
    )
    # Specifically: the .update action must NOT appear -- clear supersedes it
    # for this transition.
    assert "control_function_assignment.update" not in actions, (
        f".clear must replace .update for non-NULL -> NULL transitions; got actions={actions}"
    )

    # The .clear audit row's changes dict still records the field diff
    # so the audit UI can render the before/after.
    clear_row = next(r for r in rows if r.action == "control_function_assignment.clear")
    assert clear_row.changes["capability_value"] == [0.5, None]


@pytest.mark.asyncio
async def test_update_capability_emits_update_action_not_clear(
    db_session, organization, admin_user
):
    """Changing capability_value to a different non-NULL value
    (0.5 -> 0.8) emits ``control_function_assignment.update``
    UNCHANGED -- the .clear action is reserved exclusively for the
    non-NULL -> NULL transition.
    """
    db, org, user = db_session, organization, admin_user

    create_form = ControlForm(
        name="Update Test",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.5,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    control = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Real edit, both endpoints non-NULL
    update_form = ControlForm(
        name=control.name,
        description="",
        type=control.type,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.8,  # changed, still non-NULL
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    await update_control(db, control=control, user_id=user.id, form=update_form)
    await db.commit()

    rows = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.entity_type == "control_function_assignment")
            )
        )
        .scalars()
        .all()
    )
    actions = [r.action for r in rows]

    assert "control_function_assignment.update" in actions, (
        f"non-NULL -> non-NULL diff must still emit .update; got actions={actions}"
    )
    assert "control_function_assignment.clear" not in actions, (
        f".clear must NOT fire for non-NULL -> non-NULL transitions; got actions={actions}"
    )


@pytest.mark.asyncio
async def test_setting_capability_from_null_to_value_emits_update_not_clear(
    db_session, organization, admin_user
):
    """The inverse direction (NULL -> non-NULL) is NOT a clear -- it's a
    regular update. Pins that ``.clear`` is direction-asymmetric: only
    the NULL-fallback-engaging transition (non-NULL -> NULL) gets the
    distinct action verb.
    """
    db, org, user = db_session, organization, admin_user

    # Create with NULL capability_value. NULL is universally permitted by the
    # DTO validator (OQ1 sentinel). We use LEC_DET_MONITORING (ELAPSED_TIME)
    # so the populated branch can plug in a realistic day-magnitude value
    # (14.0 days) rather than a probability-bounded one.
    create_form = ControlForm(
        name="Null Start Test",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_DET_MONITORING,
                capability_value=None,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    control = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Now SET the capability_value
    update_form = ControlForm(
        name=control.name,
        description="",
        type=control.type,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_DET_MONITORING,
                capability_value=14.0,  # days
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    await update_control(db, control=control, user_id=user.id, form=update_form)
    await db.commit()

    rows = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.entity_type == "control_function_assignment")
            )
        )
        .scalars()
        .all()
    )
    actions = [r.action for r in rows]

    assert "control_function_assignment.update" in actions
    assert "control_function_assignment.clear" not in actions, (
        f"NULL -> non-NULL is a populate, not a clear; got actions={actions}"
    )
