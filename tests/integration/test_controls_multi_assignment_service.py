"""Multi-assignment cap relaxation — service layer integration (PR kappa, spec §6.1).

PR lambda F9: route handlers are now active. Service-layer tests exercise
svc.create_control directly per spec §6.1 paranoid-review fix S4. Route-level
tests updated to reflect that /controls/new returns 422 (not 503) for
incomplete payloads.

Uses the same fixture pattern as tests/integration/test_controls_crud.py:
  - authed_admin: tuple[AsyncClient, uuid.UUID]  (client, org_id)
  - db_session: AsyncSession

2 tests.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services import controls as svc
from tests.conftest import csrf_post


def _dto(sub_function: FairCamSubFunction) -> ControlFunctionAssignmentDTO:
    return ControlFunctionAssignmentDTO(
        sub_function=sub_function,
        capability_value=0.85,
        coverage=0.88,
        reliability=0.92,
    )


async def test_service_layer_accepts_multi_assignment(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """svc.create_control accepts >= 2 assignments; all ControlFunctionAssignment
    rows persist and are queryable (spec §6.1)."""
    _client, org_id = authed_admin

    form = ControlForm(
        name="EDR Multi",
        type=ControlType.TECHNICAL,
        assignments=[
            _dto(FairCamSubFunction.LEC_PREV_RESISTANCE),
            _dto(FairCamSubFunction.LEC_DET_VISIBILITY),
            _dto(FairCamSubFunction.LEC_DET_RECOGNITION),
        ],
    )
    control = await svc.create_control(db_session, org_id=org_id, user_id=None, form=form)
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(ControlFunctionAssignment).where(
                    ControlFunctionAssignment.control_id == control.id
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(rows) == 3
    sub_fns = {r.sub_function for r in rows}
    assert FairCamSubFunction.LEC_PREV_RESISTANCE in sub_fns
    assert FairCamSubFunction.LEC_DET_VISIBILITY in sub_fns
    assert FairCamSubFunction.LEC_DET_RECOGNITION in sub_fns


async def test_route_handler_returns_422_on_incomplete_form(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Spec §6.1 + PR lambda F9: /controls/new active; incomplete form returns 422."""
    client, _org_id = authed_admin
    r = await csrf_post(client, "/controls/new", {"name": "Incomplete", "domain": "loss_event"})
    assert r.status_code == 422
