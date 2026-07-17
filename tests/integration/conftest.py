"""Integration-layer fixtures for PR λ control wizard tests.

Provides three fixtures that seed a Control with two ControlFunctionAssignment
rows in various states (confirmed, unconfirmed, soft-deleted). All three share
a single DRY helper (_make_control_with_two_assignments) to avoid fixture
drift.

Sub-functions used:
  LEC_PREV_RESISTANCE  — PROBABILITY unit, valid for new-style form POSTs.
  LEC_DET_VISIBILITY   — PROBABILITY unit (NOT LEC_DET_COLLECT_SIGNALS which
                          does not exist; F4 implementer hit this same issue).

The `await db.refresh(ctrl, attribute_names=["assignments"])` after commit
is the project pattern for selectin relationships (per F4 implementer's note).

Organisation-isolation note: these fixtures take ``authed_admin`` (not the
separate ``organization`` fixture) so that the seeded Control belongs to the
SAME org the ``admin_client`` session is logged into. The ``require_sole_org``
route helper returns ``scalars().first()`` — if two orgs exist (one from
``authed_admin``, one from ``organization``), the route may get the wrong one
and return 404 for a control that belongs to the other org. By reusing
``authed_admin`` the org_id is guaranteed to match.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
)


async def _make_control_with_two_assignments(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    status: EntityStatus = EntityStatus.ACTIVE,
    confirmed: bool = True,
) -> Control:
    """DRY helper — base for the three fixtures.

    Creates a Control under ``org_id`` with two ControlFunctionAssignment rows.
    ``created_by`` is intentionally left NULL — mirrors the ORM-seed pattern
    in test_controls_crud.py which does not require a real user FK.
    """
    ctrl = Control(
        organization_id=org_id,
        created_by=None,
        name=(f"Test Control ({status.value}, {'confirmed' if confirmed else 'unconfirmed'})"),
        type=ControlType.TECHNICAL,
        status=status,
        version="1.0",
    )
    db.add(ctrl)
    await db.flush()

    now = datetime.now(UTC) if confirmed else None

    for sf in (
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        FairCamSubFunction.LEC_DET_VISIBILITY,
    ):
        db.add(
            ControlFunctionAssignment(
                control_id=ctrl.id,
                organization_id=org_id,
                sub_function=sf,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.85,
                confirmed_by_user_at=now,
                measured_by=None,
                measured_at=now,
            )
        )

    await db.commit()
    await db.refresh(ctrl, attribute_names=["assignments"])
    return ctrl


@pytest_asyncio.fixture
async def existing_control_with_2_assignments(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> Control:
    """Active Control with 2 confirmed assignments (ACTIVE + confirmed).

    Seeded under the same org as the ``admin_client`` session so route-level
    assertions against /controls/{id}/edit do not get a 404 from org mismatch.
    """
    _client, org_id = authed_admin
    return await _make_control_with_two_assignments(
        db_session,
        org_id,
        status=EntityStatus.ACTIVE,
        confirmed=True,
    )


@pytest_asyncio.fixture
async def existing_control_with_2_assignments_unconfirmed(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> Control:
    """Active Control with 2 unconfirmed assignments (confirmed_by_user_at=NULL).

    Seeded under the same org as the ``admin_client`` session.
    """
    _client, org_id = authed_admin
    return await _make_control_with_two_assignments(
        db_session,
        org_id,
        status=EntityStatus.ACTIVE,
        confirmed=False,
    )


@pytest_asyncio.fixture
async def soft_deleted_control(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> Control:
    """Soft-deleted Control (status=DELETED) — GET /edit must return 404.

    Seeded under the same org as the ``admin_client`` session.
    """
    _client, org_id = authed_admin
    return await _make_control_with_two_assignments(
        db_session,
        org_id,
        status=EntityStatus.DELETED,
        confirmed=True,
    )
