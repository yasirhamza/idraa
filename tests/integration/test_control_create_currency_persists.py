"""Investigation + regression for issue #155: CURRENCY capability_value not persisted.

UAT-observed: filling capability_value=5000 in the CURRENCY widget and
clicking "Create control" results in a saved Control with
capability_value=NULL (the x-clear button never appears on the edit
page, which is template-gated on a non-NULL value).

The harness (Playwright) confirms:
1. The DOM input element holds "5000" immediately before submit.
2. POST returns 204 (success — no validation error surfaced).
3. The edit page shows no x-clear button → capability_value is NULL.

This test bypasses HTMX/browser serialization and POSTs directly to
``/controls/new`` with the exact form-data shape a real submit would
produce. Goal: bisect whether the bug is:
  (a) Server-side (form parsing / Pydantic / service) — test FAILS.
  (b) Client-side (HTMX form serialization / widget swap) — test PASSES.

If (a), the test will give us the surface to fix. If (b), the test
proves the server is correct and we need to chase the widget side
(probably a post-swap detached input).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_post_controls_new_with_currency_assignment_persists_capability_value(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """POST /controls/new with CURRENCY assignment + capability_value=5000
    MUST persist the value to the DB (currently #155: value is dropped to NULL).
    """
    client, org_id = authed_admin
    # EXACT payload from UAT Phase 4.b form-data dump (so the test mirrors
    # the real browser submit byte-for-byte instead of an idealized payload).
    payload = {
        "name": "Issue #155 CURRENCY regression",
        "description": "",
        "type": "technical",
        "status": "active",
        "annual_cost": "0",
        "assignments[0][sub_function]": "lec_resp_loss_reduction",
        "assignments[0][capability_value]": "5000",
        "assignments[0][coverage]": "0.8",
        "assignments[0][reliability]": "0.8",
    }
    # Mirror UAT exactly: HTMX hx-post sends HX-Request header → server
    # returns 204 + HX-Redirect (not 303). This matches what the UAT
    # Playwright captures.
    response = await csrf_post(
        client,
        "/controls/new",
        payload,
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code in (204, 200, 303), (
        f"Unexpected status {response.status_code}. Body: {response.text[:500]!r}"
    )

    ctrl = (
        await db_session.execute(
            select(Control).where(
                Control.organization_id == org_id,
                Control.name == "Issue #155 CURRENCY regression",
            )
        )
    ).scalar_one_or_none()
    assert ctrl is not None, "Control not created"

    assignment = (
        await db_session.execute(
            select(ControlFunctionAssignment).where(
                ControlFunctionAssignment.control_id == ctrl.id,
            )
        )
    ).scalar_one()

    assert assignment.capability_value == 5000.0, (
        f"capability_value should persist as 5000.0, got {assignment.capability_value!r}. "
        f"Per #155 this is the symptom: a CURRENCY assignment's value is silently "
        f"dropped to NULL on persistence. If this test FAILS, the bug is server-side "
        f"(form parsing / Pydantic / service). If it PASSES, the bug is client-side "
        f"(HTMX form serialization / widget-swap timing) — chase the browser path."
    )
