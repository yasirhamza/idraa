"""Regression for issue #154: state-change routes redirect without flash.

Three routes change state and redirect to a list page with NO flash banner:
- POST /controls/{id}/delete → /controls
- POST /overlays/{id}/deactivate → /overlays
- POST /controls/{cid}/assignments/{aid}/confirm (non-HTMX path) → /controls/maintenance

Post-fix: each appends a query string flag that the GET handler reads
and renders as a success flash, matching the ``?saved=1`` precedent at
``routes/organization.py:82-90`` and the ``?imported=N&skipped=K``
precedent at ``routes/controls.py:288-308``.

Tests cover (per route):
1. POST redirects with the new query-string flag.
2. GET with the flag renders the flash.
3. GET without the flag does NOT render a stale flash.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
)
from idraa.models.overlay import OverlayDefinition
from tests.conftest import csrf_post

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_control(db: AsyncSession, org_id: uuid.UUID, *, name: str) -> Control:
    ctrl = Control(
        organization_id=org_id,
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("100"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db.add(ctrl)
    await db.flush()
    return ctrl


async def _seed_assignment(
    db: AsyncSession, org_id: uuid.UUID, ctrl_id: uuid.UUID
) -> ControlFunctionAssignment:
    asn = ControlFunctionAssignment(
        organization_id=org_id,
        control_id=ctrl_id,
        sub_function=FairCamSubFunction.LEC_PREV_AVOIDANCE,
        capability_value=0.8,
        coverage=0.85,
        reliability=0.9,
        confirmed_by_user_at=None,
    )
    db.add(asn)
    await db.flush()
    return asn


async def _seed_overlay(db: AsyncSession, org_id: uuid.UUID, *, name: str) -> OverlayDefinition:
    od = OverlayDefinition(
        organization_id=org_id,
        tag=name.lower().replace(" ", "_"),
        display_name=name,
        frequency_multiplier=1.0,
        magnitude_multiplier=1.0,
        methodology="seed for #154 flash test (must be >=20 chars)",
        is_active=True,
    )
    db.add(od)
    await db.flush()
    return od


# ---------------------------------------------------------------------------
# POST /controls/{id}/delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_control_delete_redirects_with_deleted_param(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(db_session, org_id, name="ToDelete")
    await db_session.commit()

    r = await csrf_post(client, f"/controls/{ctrl.id}/delete", {}, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/controls"), f"unexpected location {location!r}"
    assert "deleted=1" in location, (
        f"location {location!r} missing deleted=1 query flag (issue #154)"
    )


@pytest.mark.asyncio
async def test_get_controls_with_deleted_flag_renders_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/controls?deleted=1")
    assert r.status_code == 200
    assert "Deleted" in r.text or "deleted" in r.text


@pytest.mark.asyncio
async def test_get_controls_without_deleted_flag_no_stale_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/controls")
    assert r.status_code == 200
    assert "Deleted" not in r.text


# ---------------------------------------------------------------------------
# POST /overlays/{id}/deactivate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_overlay_deactivate_redirects_with_deactivated_param(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    od = await _seed_overlay(db_session, org_id, name="ToDeactivate")
    await db_session.commit()

    r = await csrf_post(
        client,
        f"/overlays/{od.id}/deactivate",
        {"reason": "testing #154 flash"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/overlays"), f"unexpected location {location!r}"
    assert "deactivated=1" in location, (
        f"location {location!r} missing deactivated=1 query flag (issue #154)"
    )


@pytest.mark.asyncio
async def test_get_overlays_with_deactivated_flag_renders_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/overlays?deactivated=1")
    assert r.status_code == 200
    assert "Deactivated" in r.text or "deactivated" in r.text


@pytest.mark.asyncio
async def test_get_overlays_without_deactivated_flag_no_stale_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/overlays")
    assert r.status_code == 200
    assert "Deactivated" not in r.text


# ---------------------------------------------------------------------------
# POST /controls/{cid}/assignments/{aid}/confirm (non-HTMX path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_confirm_non_htmx_redirects_with_confirmed_param(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    ctrl = await _seed_control(db_session, org_id, name="FlashConfirmCtrl")
    asn = await _seed_assignment(db_session, org_id, ctrl.id)
    await db_session.commit()

    # No HX-Request header → non-HTMX path → 303 redirect.
    r = await csrf_post(
        client,
        f"/controls/{ctrl.id}/assignments/{asn.id}/confirm",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/controls/maintenance"), f"unexpected location {location!r}"
    assert "confirmed=1" in location, (
        f"location {location!r} missing confirmed=1 query flag (issue #154)"
    )


@pytest.mark.asyncio
async def test_get_maintenance_with_confirmed_flag_renders_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/controls/maintenance?confirmed=1")
    assert r.status_code == 200
    assert "Confirmed" in r.text or "confirmed" in r.text


@pytest.mark.asyncio
async def test_get_maintenance_without_confirmed_flag_no_stale_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/controls/maintenance")
    assert r.status_code == 200
    # The page uses "confirmation" / "confirm" in static help text; check
    # for the exact flash-banner sentinel "assignment confirmed".
    assert "Assignment confirmed" not in r.text
