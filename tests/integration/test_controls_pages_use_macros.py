"""F15-F17: Controls list page + form uses the design-system macros."""

from __future__ import annotations

import uuid
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def test_controls_list_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    resp = await client.get("/controls")
    assert resp.status_code == 200
    body = resp.text
    # page_header is sticky
    assert "sticky" in body
    # Breadcrumb walks to /controls — check for Controls label in nav context
    assert "Home" in body
    assert ">Controls<" in body or "Controls</a>" in body or "Controls</span>" in body


async def test_controls_list_uses_data_table_wrapper(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """With at least one control, data_table renders both desktop and mobile wrappers."""
    from idraa.models.enums import FairCamSubFunction
    from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
    from idraa.services import controls as svc

    client, org_id = authed_admin

    # Seed one control so data_table renders table wrappers instead of empty_state.
    form = ControlForm(
        name="Test Firewall",
        type="technical",
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.8,
                coverage=0.9,
                reliability=0.85,
            )
        ],
        annual_cost=Decimal("1000"),
        nist_csf_functions=[],
        iso_27001_domains=[],
    )
    await svc.create_control(db_session, org_id=org_id, user_id=None, form=form)
    await db_session.commit()

    resp = await client.get("/controls")
    body = resp.text
    # data_table desktop signature — overflow-x-auto on the wrapper div
    assert "overflow-x-auto" in body
    # data_table mobile card stack signature
    assert "md:hidden" in body


# ---------------------------------------------------------------------------
# F16 — controls/form.html re-skin tests
# ---------------------------------------------------------------------------


async def test_controls_new_form_uses_form_field(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    resp = await client.get("/controls/new")
    assert resp.status_code == 200
    body = resp.text
    # form_field label class
    assert "text-meta" in body
    # form_field focus ring
    assert "focus:ring-brand" in body
    # REQUIRED chip from form_field on required fields
    assert "REQUIRED" in body


async def test_controls_new_form_is_ungated_for_phones(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Mobile tranche 2b: the control authoring form is no longer device-gated.
    It renders the real <form> on phones (assignment rows reflow via
    grid-cols-1 md:grid-cols-6) and must NOT carry the only_on_md device gate."""
    client, _ = authed_analyst
    resp = await client.get("/controls/new")
    assert resp.status_code == 200
    body = resp.text
    # The real form renders directly...
    assert "<form" in body
    assert "focus:ring-brand" in body
    # ...with no only_on_md device gate and no "Switch device" block.
    assert "hidden md:block" not in body
    assert "Switch device" not in body


async def test_controls_new_form_has_sticky_action_bar(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    resp = await client.get("/controls/new")
    assert resp.status_code == 200
    body = resp.text
    # Sticky action bar with backdrop blur per spec §6
    assert "sticky bottom-0" in body or "sticky\n" in body
    assert "backdrop-blur" in body


# ---------------------------------------------------------------------------
# F17 — detail, maintenance, import re-skin tests
# ---------------------------------------------------------------------------


async def test_controls_detail_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    existing_control_with_2_assignments: object,
) -> None:
    """detail.html: page_header (sticky) + display-mode form_field grid."""
    client, _ = authed_admin
    ctrl = existing_control_with_2_assignments
    resp = await client.get(f"/controls/{ctrl.id}")
    assert resp.status_code == 200
    body = resp.text
    # page_header emits sticky header
    assert "sticky" in body
    # display-mode form_field uses text-meta label class
    assert "text-meta" in body
    # Breadcrumb present
    assert "Controls" in body


async def test_controls_detail_uses_status_pill(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    existing_control_with_2_assignments: object,
) -> None:
    """detail.html: status_pill renders for control status."""
    client, _ = authed_admin
    ctrl = existing_control_with_2_assignments
    resp = await client.get(f"/controls/{ctrl.id}")
    assert resp.status_code == 200
    body = resp.text
    # status_pill emits aria-label with "control:" prefix
    assert 'aria-label="control:' in body or "kind: control" in body or "control:" in body


async def test_controls_detail_assignments_use_data_table(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    existing_control_with_2_assignments: object,
) -> None:
    """detail.html: assignments rendered via data_table (overflow-x-auto)."""
    client, _ = authed_admin
    ctrl = existing_control_with_2_assignments
    resp = await client.get(f"/controls/{ctrl.id}")
    assert resp.status_code == 200
    body = resp.text
    # data_table desktop wrapper
    assert "overflow-x-auto" in body


async def test_controls_maintenance_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """maintenance.html: page_header present."""
    client, _ = authed_admin
    resp = await client.get("/controls/maintenance")
    assert resp.status_code == 200
    body = resp.text
    # page_header is sticky
    assert "sticky" in body


async def test_controls_maintenance_empty_or_table(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """maintenance.html: renders either a data table or empty_state markers."""
    client, _ = authed_admin
    resp = await client.get("/controls/maintenance")
    assert resp.status_code == 200
    body = resp.text
    # Either table rows OR empty_state block (rounded-card) OR all-confirmed banner
    has_table = "overflow-x-auto" in body
    has_empty = "rounded-card" in body
    has_confirmed = "All controls up to date" in body or "confirmed" in body.lower()
    assert has_table or has_empty or has_confirmed


async def test_controls_import_is_ungated_with_page_header_and_sticky_bar(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """import.html (mobile tranche 2e): page_header + sticky action bar, and the
    single upload form is no longer device-gated — it renders on phones with no
    only_on_md (`hidden md:block`) wrapper and no "Switch device" block."""
    client, _ = authed_admin
    resp = await client.get("/controls/import")
    assert resp.status_code == 200
    body = resp.text
    # page_header sticky
    assert "sticky" in body
    # Un-gated: no only_on_md device wrapper, no "Switch device" block.
    assert "hidden md:block" not in body
    assert "Switch device" not in body
    # The upload form still renders with its sticky action bar.
    assert "sticky bottom-0" in body
    assert "backdrop-blur" in body
