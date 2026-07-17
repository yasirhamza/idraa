"""Issue #90 Task 1: GET /controls?domain=... query-param filter.

Pre-issue-90 the list view did not accept a domain filter (the deprecated
column wouldn't have supported multi-domain assignments anyway). Post-
issue-90 the route validates the query-string against `ControlDomain`,
delegates to `list_controls` with a JOIN-against-assignments filter, and
returns a generic "unknown domain" 400 (NOT echoing user input) on bad
input.

Plan-gate fixes guarded here:
  - Sec-I1: cross-org leak. The JOIN must NOT cross organizations.
  - Sec-I3: 400 response body must NOT contain the offending user input.
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
from tests.factories import create_org


def _make_control_with_assignment(
    org_id: uuid.UUID,
    *,
    name: str,
    sub_function: FairCamSubFunction,
) -> tuple[Control, ControlFunctionAssignment]:
    ctrl = Control(
        organization_id=org_id,
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    asn = ControlFunctionAssignment(
        organization_id=org_id,
        control_id=None,  # set after flush
        sub_function=sub_function,
        capability_value=0.8,
        coverage=0.85,
        reliability=0.9,
    )
    return ctrl, asn


@pytest.mark.asyncio
async def test_list_controls_domain_filter_returns_only_loss_event_for_org(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """`?domain=loss_event` returns only controls with at least one LEC
    assignment, scoped to the caller's org.
    """
    client, org_id = authed_admin

    # LEC control — should appear under ?domain=loss_event
    lec_ctrl, lec_asn = _make_control_with_assignment(
        org_id,
        name="LEC-only Control",
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
    )
    # DSC-only control — should NOT appear under ?domain=loss_event
    dsc_ctrl, dsc_asn = _make_control_with_assignment(
        org_id,
        name="DSC-only Control",
        sub_function=FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
    )
    db_session.add_all([lec_ctrl, dsc_ctrl])
    await db_session.flush()
    lec_asn.control_id = lec_ctrl.id
    dsc_asn.control_id = dsc_ctrl.id
    db_session.add_all([lec_asn, dsc_asn])
    await db_session.commit()

    r = await client.get("/controls?domain=loss_event")
    assert r.status_code == 200, r.text[:300]
    body = r.text
    assert "LEC-only Control" in body
    assert "DSC-only Control" not in body


@pytest.mark.asyncio
async def test_list_controls_domain_filter_cross_org_isolated(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Plan-gate Sec-I1: org-A user filtering by domain MUST NOT see org-B controls.

    Seed both orgs with a LEC-assignment control. Authed as org-A, the
    domain-filtered list must contain only org-A's control.
    """
    client, org_a_id = authed_admin

    # Seed a SECOND org with its own LEC control + assignment.
    org_b = await create_org(db_session, name="OrgB-leak-test")
    a_ctrl, a_asn = _make_control_with_assignment(
        org_a_id,
        name="OrgA-LEC",
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
    )
    b_ctrl, b_asn = _make_control_with_assignment(
        org_b.id,
        name="OrgB-LEC-SHOULD-NOT-LEAK",
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
    )
    db_session.add_all([a_ctrl, b_ctrl])
    await db_session.flush()
    a_asn.control_id = a_ctrl.id
    b_asn.control_id = b_ctrl.id
    db_session.add_all([a_asn, b_asn])
    await db_session.commit()

    r = await client.get("/controls?domain=loss_event")
    assert r.status_code == 200, r.text[:300]
    body = r.text
    assert "OrgA-LEC" in body
    assert "OrgB-LEC-SHOULD-NOT-LEAK" not in body, (
        "cross-org leak: org-A request returned an org-B control under domain filter"
    )


@pytest.mark.asyncio
async def test_list_controls_unknown_domain_returns_generic_400_no_echo(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Plan-gate Sec-I3: invalid `?domain=` returns a generic 400 — body
    must NOT contain the offending input.
    """
    client, _org_id = authed_admin

    payload = "<script>alert(1)</script>"
    r = await client.get(f"/controls?domain={payload}")
    assert r.status_code == 400
    # Response body / detail must NOT echo the attacker-controlled input.
    assert payload not in r.text, (
        "400 response echoed the offending user input — XSS / log-injection risk"
    )
    # FastAPI's default JSON error has {"detail": "..."}; we accept either
    # the JSON form or any other generic representation, but the literal
    # phrase "unknown domain" must appear in the response payload.
    assert "unknown domain" in r.text


@pytest.mark.asyncio
async def test_list_controls_no_domain_filter_returns_all(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Sanity: no `?domain=` param returns the full active controls list."""
    client, org_id = authed_admin

    lec_ctrl, lec_asn = _make_control_with_assignment(
        org_id,
        name="ANY-LEC",
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
    )
    dsc_ctrl, dsc_asn = _make_control_with_assignment(
        org_id,
        name="ANY-DSC",
        sub_function=FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
    )
    db_session.add_all([lec_ctrl, dsc_ctrl])
    await db_session.flush()
    lec_asn.control_id = lec_ctrl.id
    dsc_asn.control_id = dsc_ctrl.id
    db_session.add_all([lec_asn, dsc_asn])
    await db_session.commit()

    r = await client.get("/controls")
    assert r.status_code == 200, r.text[:300]
    assert "ANY-LEC" in r.text
    assert "ANY-DSC" in r.text
