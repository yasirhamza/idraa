"""Render-smoke tests for controls/maintenance.html.

Spec §B-NEW1: template renders without error; HTMX-swap target shape present.
2 tests.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient


async def test_maintenance_page_renders_empty_state(
    authed_admin: tuple[AsyncClient, object],
) -> None:
    """GET /controls/maintenance with no unconfirmed assignments → renders empty state."""
    client, _ = authed_admin

    r = await client.get("/controls/maintenance")
    assert r.status_code == 200
    assert "Controls Maintenance" in r.text
    assert "confirmed" in r.text.lower()  # empty state mentions confirmed somewhere


async def test_maintenance_page_htmx_swap_target_present(
    authed_admin: tuple[AsyncClient, object],
) -> None:
    """HTMX confirm buttons use hx-target='#assignment-row-{id}' shape."""
    client, _ = authed_admin

    r = await client.get("/controls/maintenance")
    assert r.status_code == 200

    # Verify HTMX-swap target pattern is in the template source
    template_src = Path("src/idraa/templates/controls/maintenance.html").read_text()
    assert 'hx-target="#assignment-row-' in template_src
    assert 'hx-swap="outerHTML"' in template_src


async def test_multidomain_control_assignments_not_cross_posted(
    authed_analyst: tuple[AsyncClient, object],
    db_session,
) -> None:
    """Each assignment of a multi-domain control appears under its OWN sub-function's
    domain section only -- NOT cross-posted into every domain the control spans.

    Regression: the prior grouping (`for d in a.control.domains`) appended each
    assignment to every domain bucket, duplicating rows across the LEC/VMC/DSC
    tables for multi-domain controls."""
    from decimal import Decimal

    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction

    client, org_id = authed_analyst
    ctrl = Control(
        organization_id=org_id,
        name="MultiDomain Regression Control",
        description="spans LEC + VMC",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=Decimal("1000"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db_session.add(ctrl)
    await db_session.flush()
    lec = ControlFunctionAssignment(
        control_id=ctrl.id,
        organization_id=org_id,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.8,
        confirmed_by_user_at=None,
    )
    vmc = ControlFunctionAssignment(
        control_id=ctrl.id,
        organization_id=org_id,
        sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.8,
        confirmed_by_user_at=None,
    )
    db_session.add_all([lec, vmc])
    await db_session.commit()
    await db_session.close()

    r = await client.get("/controls/maintenance")
    assert r.status_code == 200
    body = r.text
    vmc_hdr = body.index("Variance Management Controls")
    lec_region, vmc_region = body[:vmc_hdr], body[vmc_hdr:]
    lec_marker, vmc_marker = f"assignment-row-{lec.id}", f"assignment-row-{vmc.id}"
    assert lec_marker in lec_region
    assert lec_marker not in vmc_region, "LEC assignment cross-posted into VMC section"
    assert vmc_marker in vmc_region
    assert vmc_marker not in lec_region, "VMC assignment cross-posted into LEC section"
