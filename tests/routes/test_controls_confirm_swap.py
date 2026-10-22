"""Regression test for issue #159: maintenance Confirm leaves stale row.

Pre-fix: POST /controls/{cid}/assignments/{aid}/confirm returned 204 No
Content + HX-Trigger: confirmationDone. HTMX 1.9.x silently skips the
swap on a 204 response (regardless of hx-swap directive), and the
template has no listener for the confirmationDone event — so the
clicked Confirm row stayed visible in the DOM until the user manually
refreshed. Server state was correct (the row stayed unconfirmed... wait,
the row was confirmed server-side; just the *visual* stayed).

Post-fix: HTMX callers receive 200 + empty body, which HTMX swaps in via
``hx-swap="outerHTML"``, removing the row from the DOM. HX-Trigger
header is preserved for any downstream listeners (e.g. future badge
refresh wiring).

Non-HTMX (browser-form) callers still get 303 → /controls/maintenance.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction


async def _seed_unconfirmed_assignment(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (control_id, assignment_id)."""
    ctrl = Control(
        organization_id=org_id,
        name="Issue #159 confirm-swap regression",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("100"),
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
        confirmed_by_user_at=None,
    )
    db.add(asn)
    await db.flush()
    return ctrl.id, asn.id


async def _csrf_token_for(client: AsyncClient) -> str:
    """Prime CSRF by GET'ing /controls/maintenance + return the cookie value."""
    r = await client.get("/controls/maintenance")
    assert r.status_code == 200, f"bootstrap GET returned {r.status_code}"
    token = client.cookies.get("csrf_token")
    assert token, "csrf_token cookie missing post-bootstrap"
    return token


@pytest.mark.asyncio
async def test_confirm_htmx_returns_200_empty_body_for_swap(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """HTMX-flagged POST /confirm MUST return 200 with empty body so
    hx-swap="outerHTML" removes the row from the DOM. Pre-fix this was
    204 which HTMX silently no-ops — the row stayed visible (issue #159).
    """
    client, org_id = authed_admin
    ctrl_id, asn_id = await _seed_unconfirmed_assignment(db_session, org_id)
    await db_session.commit()

    csrf = await _csrf_token_for(client)
    response = await client.post(
        f"/controls/{ctrl_id}/assignments/{asn_id}/confirm",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 200, (
        f"Expected 200 (was 204 pre-#159). Got {response.status_code}. "
        f"HTMX 1.9 silently skips the swap on 204 regardless of hx-swap; "
        f"a 200 with empty body lets outerHTML remove the row cleanly."
    )
    assert response.content == b"", (
        f"Body must be empty so outerHTML swap removes the row. Got {response.content!r}."
    )
    # HX-Trigger preserved — future listeners (badge refresh, etc.) still fire.
    assert response.headers.get("HX-Trigger") == "confirmationDone"


@pytest.mark.asyncio
async def test_confirm_non_htmx_still_303_redirects(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Non-HTMX (plain browser form) callers still get 303 → /controls/maintenance.
    The route's HTMX/non-HTMX branch must not regress.
    """
    client, org_id = authed_admin
    ctrl_id, asn_id = await _seed_unconfirmed_assignment(db_session, org_id)
    await db_session.commit()

    csrf = await _csrf_token_for(client)
    response = await client.post(
        f"/controls/{ctrl_id}/assignments/{asn_id}/confirm",
        data={"_csrf": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    # Issue #154 added the ?confirmed=1 query flag for the non-HTMX flash banner.
    assert response.headers["location"].startswith("/controls/maintenance")
    assert "confirmed=1" in response.headers["location"]


# ---------------------------------------------------------------------------
# #268 — the derived Correcting Misaligned Decisions row through the confirm route
# ---------------------------------------------------------------------------


async def _seed_unconfirmed_linked_pair(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (control_id, source_assignment_id, derived_assignment_id), both unconfirmed."""
    ctrl_id, source_id = await _seed_unconfirmed_assignment(db, org_id)
    source = await db.get(ControlFunctionAssignment, source_id)
    assert source is not None
    source.sub_function = FairCamSubFunction.LEC_RESP_RESILIENCE
    derived = ControlFunctionAssignment(
        organization_id=org_id,
        control_id=ctrl_id,
        sub_function=FairCamSubFunction.DSC_CORR_MISALIGNED,
        capability_value=None,
        coverage=0.0,
        reliability=0.0,
        derived_from_assignment_id=source_id,
        confirmed_by_user_at=None,
    )
    db.add(derived)
    await db.flush()
    return ctrl_id, source_id, derived.id


async def _confirm_audit_count(db: AsyncSession, entity_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.entity_id == entity_id,
            AuditLog.action == "control_function_assignment.confirm",
        )
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_confirming_the_source_row_also_confirms_the_derived_row(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    ctrl_id, source_id, derived_id = await _seed_unconfirmed_linked_pair(db_session, org_id)
    await db_session.commit()

    csrf = await _csrf_token_for(client)
    response = await client.post(
        f"/controls/{ctrl_id}/assignments/{source_id}/confirm",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200

    db_session.expire_all()
    source = await db_session.get(ControlFunctionAssignment, source_id)
    derived = await db_session.get(ControlFunctionAssignment, derived_id)
    assert source is not None and derived is not None
    assert source.confirmed_by_user_at is not None
    assert derived.confirmed_by_user_at is not None
    assert await _confirm_audit_count(db_session, source_id) == 1
    assert await _confirm_audit_count(db_session, derived_id) == 1  # its own audit row


@pytest.mark.asyncio
async def test_confirming_a_derived_row_directly_is_a_noop(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    ctrl_id, source_id, derived_id = await _seed_unconfirmed_linked_pair(db_session, org_id)
    await db_session.commit()

    csrf = await _csrf_token_for(client)
    response = await client.post(
        f"/controls/{ctrl_id}/assignments/{derived_id}/confirm",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200  # the route is untouched; the service ignores the row

    db_session.expire_all()
    derived = await db_session.get(ControlFunctionAssignment, derived_id)
    source = await db_session.get(ControlFunctionAssignment, source_id)
    assert derived is not None and source is not None
    assert derived.confirmed_by_user_at is None
    assert source.confirmed_by_user_at is None
    assert await _confirm_audit_count(db_session, derived_id) == 0
