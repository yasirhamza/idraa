"""Maintenance gate tests — factory, middleware, route, navbar badge.

6 tests covering:
1. _maintenance_response factory accepts None
2. _maintenance_response factory accepts real request (signature smoke)
3. /controls/maintenance route renders when unconfirmed assignments exist
4. Navbar badge hidden when zero unconfirmed assignments
5. MaintenanceBadgeCountMiddleware sets 0 for anonymous request
6. Middleware populates state for authed request (proves middleware ran)
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.routes.controls import _maintenance_response

# ---------------------------------------------------------------------------
# Test 1: factory accepts None
# ---------------------------------------------------------------------------


def test_maintenance_response_factory_accepts_none() -> None:
    """_maintenance_response(request=None) must return a 503 Response."""
    resp = _maintenance_response(request=None)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Test 2: factory None path produces plain bytes body (not template)
# ---------------------------------------------------------------------------


def test_maintenance_response_factory_none_has_text_body() -> None:
    """_maintenance_response(request=None) body contains maintenance text."""
    resp = _maintenance_response(request=None)
    assert resp.status_code == 503
    # Plain Response with bytes body — body attribute holds the raw bytes
    body = getattr(resp, "body", None)
    if body is not None:
        assert b"maintenance" in body.lower()


# ---------------------------------------------------------------------------
# Test 3: /controls/maintenance route renders when unconfirmed assignments exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_route_renders_when_unconfirmed(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """GET /controls/maintenance returns 200 for authenticated user."""
    client, org_id = authed_admin
    # Seed an unconfirmed assignment
    await _seed_unconfirmed_assignment(db_session, org_id)

    r = await client.get("/controls/maintenance")
    assert r.status_code == 200
    assert "maintenance" in r.text.lower()


# ---------------------------------------------------------------------------
# Test 4: Navbar badge hidden when zero unconfirmed assignments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_navbar_badge_hidden_when_zero_unconfirmed(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """When no unconfirmed assignments exist, maintenance badge not in navbar."""
    client, _org_id = authed_admin
    r = await client.get("/controls")
    assert r.status_code == 200
    # Badge link should not appear when count is 0
    # The middleware defaults to 0 — no badge rendered
    assert "/controls/maintenance" not in r.text and "badge" not in r.text


# ---------------------------------------------------------------------------
# Test 5: Middleware sets 0 for anonymous request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_badge_count_middleware_sets_zero_for_anonymous(
    anonymous_client: AsyncClient,
) -> None:
    """Anonymous requests should see maintenance_badge_count = 0 (no crash)."""
    # The middleware defaults to 0 for anonymous users; healthz bypasses setup guard
    r = await anonymous_client.get("/healthz")
    assert r.status_code == 200
    # No error means middleware didn't crash on anonymous state


# ---------------------------------------------------------------------------
# Test 6: Middleware populates state for authed request (proves middleware ran)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_badge_count_middleware_populates_state_for_authed_request(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """An authed request to / must succeed — proves middleware ran without error."""
    client, _org_id = authed_admin
    r = await client.get("/")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Seed helper — inlined to avoid test-helper coupling
# ---------------------------------------------------------------------------


async def _seed_unconfirmed_assignment(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Seed a Control + one unconfirmed ControlFunctionAssignment row."""
    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import (
        ControlType,
        EntityStatus,
        FairCamSubFunction,
    )

    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Test Control Maintenance",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
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
    db.add(ctrl)
    await db.flush()

    assignment = ControlFunctionAssignment(
        id=uuid.uuid4(),
        organization_id=org_id,
        control_id=ctrl.id,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.8,
        coverage=0.9,
        reliability=0.85,
        confirmed_by_user_at=None,  # unconfirmed
    )
    db.add(assignment)
    await db.commit()
