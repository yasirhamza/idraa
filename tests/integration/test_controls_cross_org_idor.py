"""Cross-org IDOR regression tests for control routes (issue #260).

`get_control` (services/controls.py) is a bare `db.get(Control, id)` with no
org filter. Three handlers must mirror the sibling `control_edit_post` pattern
(`require_sole_org` + `organization_id` mismatch -> 404):

- POST /controls/{id}/delete
- POST /controls/{id}/assignments/{aid}/confirm
- GET  /controls/{id}

Each test seeds a control in a SECOND organization (via
`seed_organization_factory`, like test_reports_executive_cross_org_idor) and
asserts the authenticated admin of the seeded org cannot reach it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction
from tests.conftest import csrf_post


async def _make_other_org_control(
    db_session: AsyncSession, org_id: uuid.UUID
) -> tuple[Control, ControlFunctionAssignment]:
    """Seed an ACTIVE control + one confirmed assignment in ``org_id``."""
    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="OtherOrgControl",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("2000"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db_session.add(ctrl)
    await db_session.flush()
    asgn = ControlFunctionAssignment(
        control_id=ctrl.id,
        organization_id=org_id,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.9,
        coverage=0.8,
        reliability=0.95,
        confirmed_by_user_at=None,  # unconfirmed so /confirm has work to do
    )
    db_session.add(asgn)
    await db_session.commit()
    return ctrl, asgn


async def test_control_detail_cross_org_idor_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Any,
) -> None:
    other_org = await seed_organization_factory(name="OtherInc")
    ctrl, _ = await _make_other_org_control(db_session, other_org.id)
    client, _ = authed_admin
    r = await client.get(f"/controls/{ctrl.id}")
    assert r.status_code == 404


async def test_control_delete_cross_org_idor_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Any,
) -> None:
    other_org = await seed_organization_factory(name="OtherInc")
    ctrl, _ = await _make_other_org_control(db_session, other_org.id)
    client, _ = authed_admin
    r = await csrf_post(
        client,
        f"/controls/{ctrl.id}/delete",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 404
    # The victim control must NOT be soft-deleted.
    await db_session.refresh(ctrl)
    assert ctrl.status is EntityStatus.ACTIVE


async def test_control_confirm_assignment_cross_org_idor_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Any,
) -> None:
    other_org = await seed_organization_factory(name="OtherInc")
    ctrl, asgn = await _make_other_org_control(db_session, other_org.id)
    client, _ = authed_admin
    r = await csrf_post(
        client,
        f"/controls/{ctrl.id}/assignments/{asgn.id}/confirm",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 404
    # The victim assignment must NOT have been confirmed.
    await db_session.refresh(asgn)
    assert asgn.confirmed_by_user_at is None
