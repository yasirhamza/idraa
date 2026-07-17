"""List + detail show a stage badge; non-active is visually marked (#395).

Plan Task 8. The plan's placeholder fixtures (``client_as_analyst`` /
``sole_org``) do not exist in this repo; the real fixtures are
``authed_analyst`` (a ``(client, org_id)`` tuple — sibling idiom in
``tests/test_controls_form_stage_select.py``) plus ``db_session``.

The control MUST be created under the analyst's own org id (the tuple's
second element), because ``GET /controls`` scopes ``list_controls`` to the
authenticated user's org — a row under the unrelated ``organization`` fixture
would never appear in the list. ``ControlImplementationStage.PLANNED.label``
is "Proposed / Planned", so the humanized label contains "Planned".
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import (
    ControlImplementationStage,
    ControlType,
    EntityStatus,
)


@pytest.mark.asyncio
async def test_list_shows_stage_badge_for_planned(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    c = Control(
        organization_id=org_id,
        name="Planned ctl",
        type=ControlType.TECHNICAL,
        status=EntityStatus.ACTIVE,
        implementation_stage=ControlImplementationStage.PLANNED,
    )
    db_session.add(c)
    # The `client` and `db_session` fixtures use independent engines on the same
    # SQLite file (conftest), so the app's separate connection only sees committed
    # rows — commit, not just flush.
    await db_session.commit()

    resp = await client.get("/controls")
    assert resp.status_code == 200
    # Humanized stage label rendered near the control (PLANNED.label).
    assert ControlImplementationStage.PLANNED.label in resp.text
    assert "Planned" in resp.text
