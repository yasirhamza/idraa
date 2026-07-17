"""Regression test for issue #157: /controls/maintenance 500 (MissingGreenlet).

Pre-fix: services.controls_maintenance.maintenance_summary eager-loaded only
ControlFunctionAssignment.control (one level deep). The route handler then
accessed ``a.control.assignments`` to gate per-domain bucket appends, which
triggered an implicit lazy load on AsyncSession and raised
``sqlalchemy.exc.MissingGreenlet`` — a 500 the user saw as a blank
"Internal Server Error" page.

Post-fix: the eager-load chain extends to ``Control.assignments`` so the
route handler can traverse the back-reference without re-entering the
async event loop.

This is async-only: the bug is invisible to sync SQLAlchemy sessions and
to unit tests that mock the query. The test MUST hit the route through
the real httpx/ASGI client so the AsyncSession session is in play.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction


async def _seed_maintenance_state(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> None:
    """Seed one control with an UNCONFIRMED assignment so the maintenance
    route exercises the per-domain grouping branch that triggers #157.
    """
    ctrl = Control(
        organization_id=org_id,
        name="UAT smoke ctrl (issue #157)",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),  # also exercises zero-cost branch
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db.add(ctrl)
    await db.flush()
    asn = ControlFunctionAssignment(
        organization_id=org_id,
        control_id=ctrl.id,
        sub_function=FairCamSubFunction.LEC_PREV_AVOIDANCE,
        capability_value=0.8,
        coverage=0.85,
        reliability=0.9,
        confirmed_by_user_at=None,  # explicit: this is the per-domain group trigger
    )
    db.add(asn)
    await db.flush()


@pytest.mark.asyncio
async def test_controls_maintenance_returns_200_with_unconfirmed_assignment(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """GET /controls/maintenance MUST return 200 when an unconfirmed
    assignment exists. Pre-fix this raised MissingGreenlet → 500.
    """
    client, org_id = authed_admin
    await _seed_maintenance_state(db_session, org_id)
    await db_session.commit()

    response = await client.get("/controls/maintenance")

    assert response.status_code == 200, (
        f"Expected 200 OK, got {response.status_code}. "
        f"Pre-fix this returned 500 because a.control.assignments triggered "
        f"an implicit lazy load on AsyncSession (sqlalchemy.exc.MissingGreenlet). "
        f"Body excerpt: {response.text[:300]!r}"
    )
    # The page should render the unconfirmed-assignments section.
    assert "UAT smoke ctrl (issue #157)" in response.text
