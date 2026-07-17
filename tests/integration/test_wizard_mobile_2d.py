"""Mobile tranche 2d: the scenario wizard is un-gated for phones, and the
step-3/4 SME estimate "table" card-stacks via responsive CSS grid.

These render-level assertions complement the existing finalize/render tests
(which prove the Alpine field `:name` bindings still drive a valid POST) by
locking in the mobile-layout markup that the un-gate depends on.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _user_id_from_org,
)

# The exact responsive grid template the SME rows + header share. If the
# column layout is retuned, update this in one place.
_SME_GRID = "md:grid-cols-[5.5rem_minmax(0,1fr)_7rem_7rem_2.25rem]"


async def _bootstrap_to_step_3(
    client: AsyncClient, db_session: AsyncSession, org_id: uuid.UUID
) -> str:
    user_id = await _user_id_from_org(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    return str(tx)


@pytest.mark.parametrize("step", [3, 4])
async def test_wizard_fair_params_step_is_ungated_and_card_stacks(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    step: int,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_to_step_3(client, db_session, org_id)

    resp = await client.get(f"/scenarios/new/wizard/step/{step}?tx={tx}")
    assert resp.status_code == 200
    body = resp.text

    # Un-gated: the "Switch device" viewport block is gone. (The left progress
    # rail still legitimately uses `hidden md:block`, so we assert on the gate's
    # heading text rather than that class.)
    assert "Switch device" not in body

    # The SME estimate grid card-stacks: it is a responsive CSS grid, not a
    # <table>, with per-cell mobile labels.
    assert "<table" not in body, f"step {step}: SME grid should not be a <table>"
    assert _SME_GRID in body, f"step {step}: expected the responsive SME grid template"
    # Mobile inline field labels (also present as desktop column headers).
    assert ">Low (5%)<" in body
    assert ">High (95%)<" in body


async def test_wizard_step_1_is_ungated(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """The wizard entry step renders on phones with no 'Switch device' block."""
    client, _ = authed_analyst
    resp = await client.get("/scenarios/new/wizard/step/1")
    assert resp.status_code == 200
    assert "Switch device" not in resp.text
    # The left progress rail is still present (desktop-only via hidden md:block).
    assert "sticky top-20" in resp.text
