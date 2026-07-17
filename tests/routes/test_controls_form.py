"""Issue #66: form parser empty-string filter — annual_cost='' → Decimal('0').

The pre-Pydantic empty-string filter in routes/controls.py drops blank
annual_cost from the dict so Pydantic's default Decimal('0') fills.
Without the filter, Pydantic would raise ValidationError on '' for a
Decimal field.

Issue #125: AuditWriter JSON-coercion regression — editing a Control with
a non-zero annual_cost diffs Decimal values into ``audit_log.changes``;
pre-fix that produced a 500 (TypeError: Decimal not JSON serializable) and
HTMX silently swallowed it, leaving Save looking unresponsive.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_control_create_with_empty_annual_cost_persists_zero_decimal(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """An empty-string annual_cost POST submission persists as Decimal('0').

    The route's pre-Pydantic empty-string filter drops the blank key so
    ControlForm's default fires instead of raising ValidationError on ''.
    """
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        data={
            "name": "Empty Cost Field",
            "description": "annual_cost left blank",
            "domain": "loss_event",
            "type": "administrative",
            "annual_cost": "",  # empty string — must coerce to Decimal('0')
            "assignments[0][sub_function]": "lec_prev_resistance",
            "assignments[0][capability_value]": "0.8",
            "assignments[0][coverage]": "1.0",
            "assignments[0][reliability]": "1.0",
        },
        follow_redirects=False,
    )
    # Success path: 204 (HTMX) or 303 (full page).
    assert r.status_code in (204, 303), (
        f"Empty annual_cost POST should succeed; got {r.status_code}. Body: {r.text[:300]!r}"
    )

    ctrl = (
        await db_session.execute(select(Control).where(Control.name == "Empty Cost Field"))
    ).scalar_one()
    assert ctrl.annual_cost == Decimal("0")


@pytest.mark.asyncio
async def test_control_create_with_negative_annual_cost_returns_422(
    authed_admin: tuple[AsyncClient, object],
) -> None:
    """ControlForm's ge=0 validator rejects negative values via Pydantic
    ValidationError; the route's existing error path re-renders the form at 422."""
    client, _org_id = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        data={
            "name": "Negative Cost",
            "description": "test",
            "domain": "loss_event",
            "type": "administrative",
            "annual_cost": "-1",
            "assignments[0][sub_function]": "lec_prev_resistance",
            "assignments[0][capability_value]": "0.8",
            "assignments[0][coverage]": "1.0",
            "assignments[0][reliability]": "1.0",
        },
        follow_redirects=False,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_control_edit_post_persists_nonzero_annual_cost(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
) -> None:
    """Edit a $0-cost control to annual_cost=10000 → save succeeds, persists,
    audit row's ``changes`` carries string-form Decimal (not bare Decimal).

    Regression for issue #125. Pre-fix this round-trip 500'd because the
    ``[Decimal('0.00'), Decimal('10000')]`` diff pair landed in
    ``audit_log.changes`` (SQLAlchemy ``JSON`` column) and stdlib
    ``json.dumps`` raised ``TypeError`` on flush.
    """
    client, _org_id = authed_admin

    # Seed: create a control with $0 cost via POST /controls/new (uses the
    # same write path as production, so the audit row from creation also
    # exercises the coercion contract).
    create = await csrf_post(
        client,
        "/controls/new",
        data={
            "name": "Cost Edit Issue 125",
            "description": "seed",
            "type": "administrative",
            "status": "active",
            "version": "1.0",
            "annual_cost": "0",
            "assignments[0][sub_function]": "lec_prev_resistance",
            "assignments[0][capability_value]": "0.7",
            "assignments[0][coverage]": "0.8",
            "assignments[0][reliability]": "0.8",
        },
        follow_redirects=False,
    )
    assert create.status_code in (204, 303), (
        f"seed create should succeed; got {create.status_code}. Body head: {create.text[:300]!r}"
    )
    location = create.headers.get("HX-Redirect") or create.headers.get("location", "")
    m = re.match(r"/controls/([0-9a-f-]{36})", location)
    assert m, f"could not parse control id from create redirect: {location!r}"
    control_id = m.group(1)

    # The user's exact case — type a whole number into Annual cost.
    r = await csrf_post(
        client,
        f"/controls/{control_id}/edit",
        data={
            "name": "Cost Edit Issue 125",
            "description": "now with cost",
            "type": "administrative",
            "status": "active",
            "version": "1.0",
            "annual_cost": "10000",
            "assignments[0][sub_function]": "lec_prev_resistance",
            "assignments[0][capability_value]": "0.7",
            "assignments[0][coverage]": "0.8",
            "assignments[0][reliability]": "0.8",
        },
        follow_redirects=False,
    )
    assert r.status_code in (204, 303), (
        f"Edit with annual_cost=10000 should succeed; got {r.status_code}. "
        f"Body head: {r.text[:500]!r}"
    )

    # Cost persisted on the Control row.
    ctrl = (
        await db_session.execute(select(Control).where(Control.name == "Cost Edit Issue 125"))
    ).scalar_one()
    assert ctrl.annual_cost == Decimal("10000")

    # Audit row exists with string-form Decimal in changes (NOT bare Decimal —
    # that would mean the coercion contract regressed).
    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "control")
                .where(AuditLog.action == "control.update")
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) >= 1, "no control.update audit row written for the edit"
    cost_change_rows = [r for r in audit_rows if "annual_cost" in r.changes]
    assert len(cost_change_rows) == 1, (
        f"expected exactly one audit row carrying an annual_cost diff; got {len(cost_change_rows)}"
    )
    prev, new = cost_change_rows[0].changes["annual_cost"]
    assert isinstance(prev, str) and isinstance(new, str), (
        f"audit changes carried non-string Decimal — coercion regressed. "
        f"types: {type(prev).__name__}, {type(new).__name__}"
    )
    assert Decimal(prev) == Decimal("0.00")
    assert Decimal(new) == Decimal("10000")
