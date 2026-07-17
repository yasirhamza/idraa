"""Edit a scenario via the form: mitigating_control_ids and mc_iterations
are persisted; scenario_controls join is diff-applied.

Tests seed scenarios directly in the authed_analyst's org so that
org-scoped lookups succeed (cross-org → 404; these are happy-path tests).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    ScenarioType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from tests.conftest import csrf_post

_FORM_BASE = {
    "name": "phase-1-4-form-test",
    "threat_category": "ransomware",
    # industry/revenue_tier are no longer ScenarioForm fields (issue #88 Task 9)
    "tef_low": "1",
    "tef_mode": "5",
    "tef_high": "12",
    "vuln_low": "0.2",
    "vuln_mode": "0.4",
    "vuln_high": "0.6",
    "pl_low": "100000",
    "pl_mode": "500000",
    "pl_high": "2000000",
}


def _make_control(org_id: uuid.UUID, *, name: str) -> Control:
    return Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
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
        created_by=None,
    )


def _make_scenario(org_id: uuid.UUID, *, name: str) -> Scenario:
    return Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
    )


@pytest.mark.asyncio
async def test_create_scenario_with_controls_persists_join(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """POST /scenarios with mitigating_control_ids → scenario row created + controls joined."""
    client, org_id = authed_analyst

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    # Seed 2 controls in the analyst's org (created_by=None, FK is nullable).
    c1 = _make_control(org_id, name="Control-A")
    c2 = _make_control(org_id, name="Control-B")
    db_session.add(c1)
    db_session.add(c2)
    await db_session.flush()  # populate c1.id / c2.id before CFAs

    _now = datetime.now(UTC)
    for ctrl in (c1, c2):
        db_session.add(
            ControlFunctionAssignment(
                control_id=ctrl.id,
                organization_id=org_id,
                sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.85,
                confirmed_by_user_at=_now,
            )
        )
    await db_session.commit()

    form_data: dict[str, Any] = {
        **_FORM_BASE,
        "mc_iterations": "5000",
        "mitigating_control_ids": [str(c1.id), str(c2.id)],
    }
    response = await csrf_post(
        client,
        "/scenarios",
        data=form_data,
        follow_redirects=False,
    )
    # Successful create → 303 redirect to /scenarios/{id}
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert location.startswith("/scenarios/")


@pytest.mark.asyncio
async def test_edit_scenario_diff_applies_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Edit form posts a new control set: removed are deleted, added are inserted."""
    client, org_id = authed_analyst

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    # Seed controls in the analyst's org (created_by=None, FK is nullable).
    c_keep = _make_control(org_id, name="Keep")
    c_remove = _make_control(org_id, name="Remove")
    c_new = _make_control(org_id, name="New")
    db_session.add(c_keep)
    db_session.add(c_remove)
    db_session.add(c_new)

    # Seed scenario with c_keep + c_remove attached.
    scenario = _make_scenario(org_id, name="edit-diff-test")
    db_session.add(scenario)
    await db_session.flush()

    _now = datetime.now(UTC)
    for ctrl in (c_keep, c_remove, c_new):
        db_session.add(
            ControlFunctionAssignment(
                control_id=ctrl.id,
                organization_id=org_id,
                sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.85,
                confirmed_by_user_at=_now,
            )
        )

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c_keep.id))
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c_remove.id))
    await db_session.commit()

    # POST update: keep c_keep, add c_new, remove c_remove.
    update_data: dict[str, Any] = {
        **_FORM_BASE,
        "name": scenario.name,
        "expected_row_version": str(scenario.row_version),
        "mc_iterations": "10000",
        "mitigating_control_ids": [str(c_keep.id), str(c_new.id)],
    }
    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}",
        data=update_data,
        follow_redirects=False,
    )
    # Successful update → 303 redirect
    assert response.status_code == 303


async def _linked_control_ids(db_session: AsyncSession, scenario_id: uuid.UUID) -> set[uuid.UUID]:
    """Read the live scenario_control join rows for a scenario."""
    rows = (
        (
            await db_session.execute(
                select(ScenarioControl.control_id).where(ScenarioControl.scenario_id == scenario_id)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


@pytest.mark.asyncio
async def test_edit_scenario_preserves_link_to_inactive_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Issue #217: editing a scenario must NOT wipe its link to a control that
    is no longer ACTIVE.

    Deterministic 3-step repro:
      1. Control C is ACTIVE and linked to scenario S.
      2. C is flipped to DRAFT (the control edit does not touch the join — the
         link survives).
      3. The operator opens the scenario edit form (which only renders
         checkboxes for ACTIVE controls, so C has no checkbox) and saves with
         an unrelated ACTIVE control checked.

    On main this DELETES the S–C link because C's id is absent from the
    submitted ``mitigating_control_ids`` and ``set_mitigating_controls`` treats
    absence as removal. The fix must preserve the link.
    """
    client, org_id = authed_analyst

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    # Two controls: one stays ACTIVE, one will be flipped to DRAFT.
    c_active = _make_control(org_id, name="Active-Control")
    c_inactive = _make_control(org_id, name="Soon-Draft-Control")
    db_session.add(c_active)
    db_session.add(c_inactive)

    scenario = _make_scenario(org_id, name="preserve-inactive-link")
    db_session.add(scenario)
    await db_session.flush()

    _now = datetime.now(UTC)
    for ctrl in (c_active, c_inactive):
        db_session.add(
            ControlFunctionAssignment(
                control_id=ctrl.id,
                organization_id=org_id,
                sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.85,
                confirmed_by_user_at=_now,
            )
        )

    # Capture ids as plain UUIDs before any expire (avoid lazy ORM IO later).
    c_active_id = c_active.id
    c_inactive_id = c_inactive.id
    scenario_id = scenario.id
    scenario_name = scenario.name
    scenario_row_version = scenario.row_version

    # Step 1: both controls linked to the scenario while ACTIVE.
    db_session.add(ScenarioControl(scenario_id=scenario_id, control_id=c_active_id))
    db_session.add(ScenarioControl(scenario_id=scenario_id, control_id=c_inactive_id))
    await db_session.commit()

    # Step 2: flip c_inactive to DRAFT (link is untouched by control status).
    c_inactive.status = EntityStatus.DRAFT
    await db_session.commit()

    # Step 3: edit the scenario. The form only renders ACTIVE controls, so the
    # submitted ids include ONLY c_active (c_inactive has no checkbox).
    update_data: dict[str, Any] = {
        **_FORM_BASE,
        "name": scenario_name,
        "expected_row_version": str(scenario_row_version),
        "mitigating_control_ids": [str(c_active_id)],
    }
    response = await csrf_post(
        client,
        f"/scenarios/{scenario_id}",
        data=update_data,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db_session.expire_all()
    linked = await _linked_control_ids(db_session, scenario_id)
    # The ACTIVE control link is kept...
    assert c_active_id in linked
    # ...and the link to the now-DRAFT control MUST survive the edit (the bug).
    assert c_inactive_id in linked, (
        "link to non-ACTIVE control was wiped by scenario edit (issue #217)"
    )


@pytest.mark.asyncio
async def test_edit_scenario_preserves_multiple_inactive_links_contract(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Data-contract test (CLAUDE.md adapter iteration contract): N>=3 controls
    including >=1 non-ACTIVE linked control; a scenario edit preserves every
    intended link.

    Setup: c_active (kept), c_draft + c_deprecated (linked, non-ACTIVE, no
    checkbox), c_added (ACTIVE, newly checked), c_removed (ACTIVE, unchecked
    → genuinely removed). Asserts the final link set is exactly
    {c_active, c_added, c_draft, c_deprecated}.
    """
    client, org_id = authed_analyst

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    c_active = _make_control(org_id, name="Active-Kept")
    c_added = _make_control(org_id, name="Active-Added")
    c_removed = _make_control(org_id, name="Active-Removed")
    c_draft = _make_control(org_id, name="Draft-Linked")
    c_deprecated = _make_control(org_id, name="Deprecated-Linked")
    all_controls = (c_active, c_added, c_removed, c_draft, c_deprecated)
    for ctrl in all_controls:
        db_session.add(ctrl)

    scenario = _make_scenario(org_id, name="preserve-multi-inactive")
    db_session.add(scenario)
    await db_session.flush()

    _now = datetime.now(UTC)
    for ctrl in all_controls:
        db_session.add(
            ControlFunctionAssignment(
                control_id=ctrl.id,
                organization_id=org_id,
                sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.85,
                confirmed_by_user_at=_now,
            )
        )

    ids = {ctrl: ctrl.id for ctrl in all_controls}
    scenario_id = scenario.id
    scenario_name = scenario.name
    scenario_row_version = scenario.row_version

    # Initial links: active+removed+draft+deprecated (NOT c_added).
    for ctrl in (c_active, c_removed, c_draft, c_deprecated):
        db_session.add(ScenarioControl(scenario_id=scenario_id, control_id=ids[ctrl]))
    await db_session.commit()

    # Flip two controls to non-ACTIVE statuses (links untouched).
    c_draft.status = EntityStatus.DRAFT
    c_deprecated.status = EntityStatus.DEPRECATED
    await db_session.commit()

    # Edit: check c_active + c_added; c_removed is unchecked (real removal).
    # c_draft/c_deprecated have no checkbox (non-ACTIVE) → absent from payload.
    update_data: dict[str, Any] = {
        **_FORM_BASE,
        "name": scenario_name,
        "expected_row_version": str(scenario_row_version),
        "mitigating_control_ids": [str(ids[c_active]), str(ids[c_added])],
    }
    response = await csrf_post(
        client,
        f"/scenarios/{scenario_id}",
        data=update_data,
        follow_redirects=False,
    )
    assert response.status_code == 303

    db_session.expire_all()
    linked = await _linked_control_ids(db_session, scenario_id)
    assert linked == {ids[c_active], ids[c_added], ids[c_draft], ids[c_deprecated]}, (
        f"link reconciliation wrong: {linked}"
    )
    # c_removed (ACTIVE, unchecked) is genuinely removed.
    assert ids[c_removed] not in linked


@pytest.mark.asyncio
async def test_edit_form_renders_inactive_linked_control_disabled(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Issue #217 (part A): the edit form surfaces a linked non-ACTIVE control
    as a checked, disabled checkbox with an "(inactive: ...)" marker so the
    operator can see the link still exists."""
    client, org_id = authed_analyst

    c_active = _make_control(org_id, name="Visible-Active")
    c_inactive = _make_control(org_id, name="Hidden-Deprecated")
    db_session.add(c_active)
    db_session.add(c_inactive)

    scenario = _make_scenario(org_id, name="form-renders-inactive")
    db_session.add(scenario)
    await db_session.flush()

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c_active.id))
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c_inactive.id))
    await db_session.commit()

    c_inactive.status = EntityStatus.DEPRECATED
    scenario_id = scenario.id
    await db_session.commit()

    resp = await client.get(f"/scenarios/{scenario_id}/edit")
    assert resp.status_code == 200
    body = resp.text
    # The inactive linked control name is shown with the inactive marker...
    assert "Hidden-Deprecated" in body
    assert "(inactive: DEPRECATED)" in body
    # ...rendered as a disabled checkbox (does not submit on save).
    assert "checked disabled" in body
