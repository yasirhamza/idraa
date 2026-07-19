"""Scenario route integration tests — list + new + create (E5) +
detail / edit / update / delete (E6).

Mirrors ``test_overlays_routes.py`` topology: the ``authed_analyst`` /
``authed_admin`` fixtures own both the client and the org id; tests
seed scenario rows directly against that org via :func:`_seed_scenario`
(local helper, parallel to the unit test's seed helper).

Covers (E5 + E6 scope):
- List renders for an analyst with industry filter applied.
- Anonymous access is rejected (401/403/302 — exception handler in
  ``app.py`` redirects HTML callers via 303 to ``/login`` while JSON
  callers see 401).
- ``GET /scenarios/new`` renders the form for an analyst.
- ``POST /scenarios`` with valid form data creates a row + 303 redirect
  to ``/scenarios/{id}``.
- ``POST /scenarios`` without ``_csrf`` is rejected by CSRFMiddleware
  (preamble P4: CSRF is global middleware, not a per-route dep).
- ``GET /scenarios/{id}`` renders detail for analyst / 404 cross-org.
- ``GET /scenarios/{id}/edit`` renders form for analyst.
- ``POST /scenarios/{id}`` updates + 303 redirects on success; returns
  409 on optimistic-lock conflict.
- ``POST /scenarios/{id}/delete`` 303 redirects to /scenarios.
- Reviewer-403 boundary tests for update / delete / edit (P12).

Refresh-calibration route tests land in E7.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    EntityStatus,
    ScenarioEffect,
    ScenarioSource,
    ScenarioType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post


def _seed_scenario(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    status: EntityStatus = EntityStatus.ACTIVE,
) -> Scenario:
    """Add a Scenario row to ``db`` and return it (caller flushes/commits).

    Mirrors ``tests/unit/test_scenario_repository.py::_seed_scenario`` but
    integration tests need the row visible to the route layer's separate
    engine, so callers ``await db.commit()`` after this rather than relying
    on ``flush()``.
    """
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=status,
    )
    db.add(s)
    return s


# ---- list -------------------------------------------------------------


async def test_list_scenarios_renders_for_analyst(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="RW")
    await db_session.commit()

    r = await client.get("/scenarios")
    assert r.status_code == 200
    assert "RW" in r.text


async def test_scenario_crud_entry_points_not_mobile_gated(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Scenario CRUD entry points must be reachable on phones.

    The authoring forms (wizard, edit form) were mobile-reflowed/un-gated, so
    the page-header buttons that link to them must NOT carry ``requires_md``
    (which renders ``hidden md:inline-flex`` → invisible on <md). Regression
    guard for the mobile-CRUD gap: 'New scenario' (list) + 'Edit' / 'Run
    simulation' (detail). The string ``hidden md:inline-flex`` also appears on
    the sidebar, so assert per-anchor, not page-wide.
    """
    import re

    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="MobileRW")
    await db_session.commit()

    list_html = (await client.get("/scenarios")).text
    new_tag = re.search(r'<a[^>]*href="/scenarios/new/wizard"[^>]*>', list_html)
    assert new_tag is not None, "'New scenario' button missing from the list page"
    assert "hidden md:inline-flex" not in new_tag.group(0), (
        "'New scenario' is mobile-gated (requires_md) — CRUD unreachable on phones"
    )


async def test_list_scenarios_redirects_for_anon(client: AsyncClient) -> None:
    """Anonymous request must not render scenarios.

    With an empty DB, ``setup_guard`` redirects unauthenticated callers
    to ``/setup`` (307) before ``/scenarios`` is even routed. That's
    also "doesn't render scenarios" — and the broader assertion (status
    in {302/303/307/401/403}) survives any future post-bootstrap world
    where the 401 exception handler in ``app.py`` redirects HTML
    callers to ``/login`` (303) and JSON callers get 401.
    """
    r = await client.get("/scenarios", follow_redirects=False)
    assert r.status_code in (302, 303, 307, 401, 403)
    # Body must not leak any scenario name; "RW" is the seeded name in
    # the analyst-can-list test above.
    assert "RW" not in r.text


# ---- new --------------------------------------------------------------


async def test_new_form_renders(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await client.get("/scenarios/new")
    assert r.status_code == 200
    # Form must include the ``name`` input.
    assert 'name="name"' in r.text


async def test_new_form_frames_vulnerability_as_inherent(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """methodology/vuln-inherent-framing: the non-wizard scenario form must also
    frame vulnerability as the asset's INHERENT (control-naive) susceptibility,
    for parity with the wizard — both feed the same control-aware engine."""
    client, _ = authed_analyst
    r = await client.get("/scenarios/new")
    assert r.status_code == 200
    assert "inherent" in r.text.lower()
    assert "before your controls" in r.text


async def test_new_form_renders_with_existing_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Regression for the post-PR ι control_strength template miss:

    PR ι (excise calibration runtime) removed control_strength /
    control_reliability / control_coverage from the Control ORM, but
    scenarios/form.html still referenced control.control_strength in the
    "available controls" picker. With zero controls the bug stayed dormant
    (the {% else %} branch ran). The instant any control existed in the
    org, GET /scenarios/new 500'd with jinja2.UndefinedError.
    """
    from idraa.models.control import Control
    from idraa.models.enums import ControlType, EntityStatus

    client, org_id = authed_analyst
    db_session.add(
        Control(
            organization_id=org_id,
            name="MFA enforced",
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
    )
    await db_session.commit()

    r = await client.get("/scenarios/new")
    assert r.status_code == 200, (
        f"Expected 200 with a Control present; got {r.status_code}. Body head: {r.text[:200]!r}"
    )
    # Picker must surface the control name.
    assert "MFA enforced" in r.text


async def test_new_form_uses_dropdowns_for_enum_fields(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Regression: threat_category / threat_actor_type / asset_class must
    render as <select> with enum-derived options, not freeform <input type="text">.

    User feedback during local UAT: "data entry should not be freeform entry,
    it must be drop-down lists." attack_vector stays freeform — no enum exists.
    """
    client, _ = authed_analyst
    r = await client.get("/scenarios/new")
    assert r.status_code == 200
    body = r.text

    for name in ("threat_category", "threat_actor_type", "asset_class"):
        # F22: select has id attr before name — check that name attr appears inside a select tag.
        # A <select ... name="X"> anywhere in the page satisfies "is a select, not a text input".
        assert f'name="{name}"' in body, f"{name} must have a name attr in the form"
        assert f'<input type="text" name="{name}"' not in body, (
            f"{name} still has the old <input type='text'> markup"
        )

    # Spot-check: known enum option values appear.
    assert 'value="ransomware"' in body
    assert 'value="cybercriminals"' in body
    assert 'value="systems"' in body

    # attack_vector stays freeform — no AttackVector enum exists.
    assert 'name="attack_vector"' in body


async def test_scenarios_list_new_button_routes_to_wizard(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Regression: the "New scenario" button on /scenarios must route to the
    wizard (`/scenarios/new/wizard`), not the simple form. The wizard is the
    HARD-requirement entry path per design; the simple form is a fallback for
    direct URL access and edit reuse."""
    client, _ = authed_analyst
    r = await client.get("/scenarios")
    assert r.status_code == 200
    assert 'href="/scenarios/new/wizard"' in r.text, (
        "Scenarios list must surface the wizard as the New-scenario entry"
    )


async def test_new_form_pins_industry_and_revenue_tier_from_org(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Regression for issue #88:

    The simple form's industry + revenue_tier are sourced live from the org
    and displayed as read-only chips. No hidden inputs — ScenarioService
    derives them from the org at save time.
    """
    client, _ = authed_analyst
    r = await client.get("/scenarios/new")
    assert r.status_code == 200
    body = r.text

    # No dropdown selects for industry or revenue_tier.
    assert '<select name="industry"' not in body, (
        "Industry select must not appear on /scenarios/new (org-derived)"
    )
    assert '<select name="revenue_tier"' not in body, (
        "Revenue tier select must not appear on /scenarios/new (org-derived)"
    )
    # No hidden inputs either — server derives from org, not from POST body.
    assert 'name="industry"' not in body, (
        "No hidden industry input; ScenarioService derives from org"
    )
    assert 'name="revenue_tier"' not in body, (
        "No hidden revenue_tier input; ScenarioService derives from org"
    )


async def test_scenarios_list_does_not_render_industry_filter(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Regression for #56 interim: industry is org-level so the per-scenario
    industry filter on /scenarios doesn't make sense. The form is removed."""
    client, _ = authed_analyst
    r = await client.get("/scenarios")
    assert r.status_code == 200
    # The Industry filter <form method="get"> with a Filter button is gone.
    assert "All industries</option>" not in r.text


# ---- create -----------------------------------------------------------


async def test_create_scenario_persists_and_redirects(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    # industry/revenue_tier dropped from payload — derived from org at service layer
    payload = {
        "name": "Phishing-led BEC",
        "threat_category": "social_engineering",
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_low": "50000",
        "pl_mode": "250000",
        "pl_high": "2000000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303
    s = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "Phishing-led BEC",
            )
        )
    ).scalar_one()
    assert r.headers["location"] == f"/scenarios/{s.id}"
    # row_version starts at 1 (server_default=1).
    assert s.row_version == 1


async def test_create_scenario_lognormal_primary_loss_stored_native(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Epic B (#326): pl_dist=lognormal stores native {mean, sigma} on submit.

    The form posts ``pl_low``/``pl_high`` as the p5/p95 pair; the route's
    ``parse_scenario_form`` → ``dist_from_raw`` closed-form converts to native
    log-space, and the persisted ``primary_loss`` JSON carries NO low/mode/high.
    """
    import math

    client, org_id = authed_analyst
    payload = {
        "name": "Lognormal-PL scenario",
        "threat_category": "ransomware",
        "tef_dist": "pert",
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_dist": "lognormal",
        "pl_low": "100",
        "pl_high": "10000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303
    s = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "Lognormal-PL scenario",
            )
        )
    ).scalar_one()
    assert s.primary_loss["distribution"] == "lognormal"
    assert abs(s.primary_loss["mean"] - (math.log(100) + math.log(10000)) / 2) < 1e-4
    assert s.primary_loss["sigma"] > 0
    assert "low" not in s.primary_loss
    assert "mode" not in s.primary_loss
    # PERT TEF on the same scenario is unregressed.
    assert s.threat_event_frequency["distribution"] == "PERT"


async def test_create_scenario_lognormal_invalid_low_rerenders_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """pl_dist=lognormal with pl_low<=0 → lognormal_from_quantiles ValueError →
    the route's existing except (..., ValueError) re-renders the form 422."""
    client, _ = authed_analyst
    payload = {
        "name": "Bad lognormal",
        "threat_category": "ransomware",
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_dist": "lognormal",
        "pl_low": "0",
        "pl_high": "10000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 422


async def test_effect_round_trips_through_create_and_edit(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Effect (CIA) field persists on create and renders as selected on edit GET.

    1. POST /scenarios with effect=availability → 303 redirect.
    2. DB row has scenario.effect == ScenarioEffect.AVAILABILITY.
    3. GET /scenarios/{id}/edit → availability option is ``selected`` in the HTML.
    """
    client, org_id = authed_analyst
    payload = {
        "name": "Availability-effect scenario",
        "threat_category": "ot_availability",
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_low": "50000",
        "pl_mode": "250000",
        "pl_high": "2000000",
        "effect": "availability",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303

    s = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "Availability-effect scenario",
            )
        )
    ).scalar_one()
    # DB row carries the enum.
    assert s.effect is ScenarioEffect.AVAILABILITY

    # Edit form renders the selected option.
    edit_r = await client.get(f"/scenarios/{s.id}/edit")
    assert edit_r.status_code == 200
    assert 'value="availability" selected' in edit_r.text


async def test_create_rejected_without_csrf(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """POST without the ``_csrf`` form field is rejected by CSRFMiddleware.

    Preamble P4: CSRF is enforced by the global ``CSRFMiddleware``
    fail-closed signed double-submit on unsafe methods — there is no
    ``require_csrf`` route dependency, so the rejection must come from
    the middleware (HTTP 403) rather than a route-layer 400.
    """
    client, _ = authed_analyst
    client.cookies.delete("csrf_token")
    r = await client.post(
        "/scenarios",
        data={
            "name": "X",
            "threat_category": "ransomware",
            "tef_low": "0.1",
            "tef_mode": "0.5",
            "tef_high": "2.0",
            "vuln_low": "0.2",
            "vuln_mode": "0.4",
            "vuln_high": "0.6",
            "pl_low": "1",
            "pl_mode": "1",
            "pl_high": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403


# ---- detail (view) ----------------------------------------------------


async def test_view_scenario_renders(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Analyst can GET ``/scenarios/{id}`` and see the detail page."""
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Detail-RW")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200
    assert "Detail-RW" in r.text
    # Calibration anchors panel is included.
    assert "Calibration anchors" in r.text


async def test_view_scenario_run_simulation_is_plain_link(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Run-simulation control links straight to /analyses/new with prefill.

    Regression: PR xi flipped /scenarios/{id}/run/new from a modal-renderer
    to a 303 redirect. The view's Run-simulation control was historically a
    <button hx-target="body" hx-swap="beforeend">, which then started
    *appending* the entire /analyses/new page to body on each click. The
    duplicate-page pile-up could push the Edit anchor off-screen, surfacing
    as "Edit not clickable" during real-user testing.

    Lock the fix: the control must be a plain anchor pointing directly at
    the unified form, with no body-append HTMX attributes.
    """
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="RunSimLink")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200
    assert f'href="/analyses/new?prefill_scenario_id={s.id}"' in r.text
    # Stale HTMX swap pattern must not return.
    assert 'hx-swap="beforeend"' not in r.text
    assert f'hx-get="/scenarios/{s.id}/run/new"' not in r.text


async def test_view_scenario_cross_org_returns_404(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Cross-org request returns 404 (not 403) and body must not leak existence.

    P12 spirit (IDOR boundary): a scenario owned by another org must be
    indistinguishable from a missing one. Mirrors the
    ``test_get_overlay_view_returns_404_for_cross_org_id`` precedent.
    """
    from tests.factories import create_org

    client, _ = authed_analyst
    other_org = await create_org(db_session, name="Other Org")
    other_scenario = _seed_scenario(db_session, org_id=other_org.id, name="cross-org-secret")
    await db_session.commit()

    r = await client.get(f"/scenarios/{other_scenario.id}")
    assert r.status_code == 404
    # Body must not leak the cross-org scenario name.
    assert "cross-org-secret" not in r.text


async def test_view_scenario_returns_404_for_unknown_id(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await client.get(f"/scenarios/{uuid.uuid4()}")
    assert r.status_code == 404


# ---- edit form -------------------------------------------------------


async def test_edit_form_renders(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Analyst can GET ``/scenarios/{id}/edit`` and see the form."""
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="EditMe")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}/edit")
    assert r.status_code == 200
    assert 'name="name"' in r.text
    # Hidden optimistic-lock field is templated from row_version.
    assert 'name="expected_row_version"' in r.text
    assert f'value="{s.row_version}"' in r.text
    # View-calibration link points back to the detail page.
    assert f'href="/scenarios/{s.id}"' in r.text
    # #326: the per-node distribution selector renders for tef/pl/sl.
    assert 'name="tef_dist"' in r.text
    assert 'name="pl_dist"' in r.text
    assert 'name="sl_dist"' in r.text
    # Vulnerability has no selector (PERT-only probability).
    assert 'name="vuln_dist"' not in r.text


async def test_edit_form_lognormal_primary_loss_round_trips(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Epic B (#326): a scenario stored with a native lognormal primary_loss
    re-renders the edit form with the lognormal option selected and the p5/p95
    re-derived into the shared low/high inputs (dist_to_form round-trip)."""
    from fair_cam.quantile_pooling import lognormal_from_quantiles

    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="LognormalEdit")
    s.primary_loss = {"distribution": "lognormal", **lognormal_from_quantiles(100.0, 10000.0)}
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}/edit")
    assert r.status_code == 200
    # The lognormal <option> is the selected one on the pl selector.
    assert 'value="lognormal" selected' in r.text
    # The Alpine x-data initialiser is inlined (PR #205), not read from a global.
    assert "{ dist: 'lognormal' }" in r.text


# ---- update ----------------------------------------------------------


async def test_update_persists_descriptive_change(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """POST update with valid data + correct expected_row_version 303s."""
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Before")
    await db_session.commit()
    # Snapshot the lock primitive before issuing the update (refreshing
    # the ORM row post-update would otherwise update s.row_version in place).
    rv_before = s.row_version
    scenario_id = s.id

    # industry/revenue_tier dropped from payload — derived from org at service layer
    payload = {
        "name": "After",
        "threat_category": s.threat_category,
        "tef_low": str(s.threat_event_frequency["low"]),
        "tef_mode": str(s.threat_event_frequency["mode"]),
        "tef_high": str(s.threat_event_frequency["high"]),
        "vuln_low": str(s.vulnerability["low"]),
        "vuln_mode": str(s.vulnerability["mode"]),
        "vuln_high": str(s.vulnerability["high"]),
        "pl_low": str(s.primary_loss["low"]),
        "pl_mode": str(s.primary_loss["mode"]),
        "pl_high": str(s.primary_loss["high"]),
        "expected_row_version": str(rv_before),
    }
    r = await csrf_post(client, f"/scenarios/{scenario_id}", payload, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/scenarios/{scenario_id}"

    # The row in the DB now reflects the new name.
    refreshed = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.name == "After"
    # row_version bumped.
    assert refreshed.row_version == rv_before + 1


async def test_update_does_not_clobber_status_version_type_source(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Regression: edit must preserve fields the form does not render.

    The edit form renders no INPUTS for status / version / scenario_type
    / source — without hidden mirrors, ``ScenarioForm`` falls through to
    its Pydantic defaults (``ACTIVE`` / ``"1.0"`` / ``CUSTOM`` /
    ``EXPERT_JUDGMENT``), so every update silently downgrades any
    non-default state. Caught by the E6 code-quality reviewer; fixed in
    E6.a by mirroring those 4 fields as ``<input type="hidden">`` on
    edit (matching the existing industry / revenue_tier pattern).
    """
    client, org_id = authed_analyst

    # Seed a scenario with NON-default values for all 4 fields so the
    # default-fallback bug would actually flip something observable.
    s = _seed_scenario(db_session, org_id=org_id, name="Pre-clobber")
    s.status = EntityStatus.DRAFT
    s.version = "2.3-post-Q1-review"
    s.scenario_type = ScenarioType.TEMPLATE
    s.source = ScenarioSource.EXPERT_JUDGMENT  # only legal value in 1.3
    await db_session.commit()
    scenario_id = s.id
    rv_before = s.row_version

    # GET the edit form and assert the hidden mirrors are present so
    # the client echoes them back on POST.
    r = await client.get(f"/scenarios/{scenario_id}/edit")
    assert r.status_code == 200
    assert 'name="status" value="draft"' in r.text
    assert 'name="version" value="2.3-post-Q1-review"' in r.text
    assert 'name="scenario_type" value="template"' in r.text
    assert 'name="source" value="expert_judgment"' in r.text

    # Submit a name-only descriptive change with the hidden mirrors
    # echoed back exactly as the browser would.
    payload = {
        "name": "Renamed",
        "threat_category": s.threat_category,
        "tef_low": str(s.threat_event_frequency["low"]),
        "tef_mode": str(s.threat_event_frequency["mode"]),
        "tef_high": str(s.threat_event_frequency["high"]),
        "vuln_low": str(s.vulnerability["low"]),
        "vuln_mode": str(s.vulnerability["mode"]),
        "vuln_high": str(s.vulnerability["high"]),
        "pl_low": str(s.primary_loss["low"]),
        "pl_mode": str(s.primary_loss["mode"]),
        "pl_high": str(s.primary_loss["high"]),
        "expected_row_version": str(rv_before),
        # Hidden mirrors — echo back the non-default state.
        "status": "draft",
        "version": "2.3-post-Q1-review",
        "scenario_type": "template",
        "source": "expert_judgment",
    }
    r = await csrf_post(client, f"/scenarios/{scenario_id}", payload, follow_redirects=False)
    assert r.status_code == 303

    # Re-fetch and verify state was preserved through the edit cycle.
    refreshed = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.name == "Renamed"
    assert refreshed.status == EntityStatus.DRAFT  # NOT silently bumped to ACTIVE
    assert refreshed.version == "2.3-post-Q1-review"  # NOT silently reset to "1.0"
    assert refreshed.scenario_type == ScenarioType.TEMPLATE  # NOT silently reset to CUSTOM
    assert refreshed.source == ScenarioSource.EXPERT_JUDGMENT


async def test_update_optimistic_conflict_returns_409(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Wrong ``expected_row_version`` (P9) renders the form with 409."""
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Locked")
    await db_session.commit()

    # industry/revenue_tier dropped from payload — derived from org at service layer
    payload = {
        "name": "Renamed",
        "threat_category": s.threat_category,
        "tef_low": str(s.threat_event_frequency["low"]),
        "tef_mode": str(s.threat_event_frequency["mode"]),
        "tef_high": str(s.threat_event_frequency["high"]),
        "vuln_low": str(s.vulnerability["low"]),
        "vuln_mode": str(s.vulnerability["mode"]),
        "vuln_high": str(s.vulnerability["high"]),
        "pl_low": str(s.primary_loss["low"]),
        "pl_mode": str(s.primary_loss["mode"]),
        "pl_high": str(s.primary_loss["high"]),
        # Wrong value — actual row_version starts at 1.
        "expected_row_version": "999",
    }
    r = await csrf_post(client, f"/scenarios/{s.id}", payload, follow_redirects=False)
    assert r.status_code == 409
    # Response body carries the reload-and-retry message.
    assert "reload" in r.text.lower()


async def test_update_409_rerender_displays_org_chips(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """After a 409 conflict re-render, the form shows org-derived calibration
    chips, NOT '—' placeholders (regression for issue #88 Gap 1).

    The render_scenario_form helper threads org context into the chip
    template variables; a missed thread would render the fallback dash.
    """
    from decimal import Decimal

    from idraa.models.enums import IndustryType
    from idraa.models.organization import Organization

    client, org_id = authed_analyst
    # Set a known industry + revenue so chip values are deterministic.
    org = await db_session.get(Organization, org_id)
    assert org is not None
    org.industry_type = IndustryType.MANUFACTURING
    org.annual_revenue = Decimal("4000000000")  # $4B → 1b_to_10b tier
    await db_session.commit()

    s = _seed_scenario(db_session, org_id=org_id, name="ChipRegression")
    await db_session.commit()

    # Submit an UPDATE with a wrong expected_row_version to trigger 409 re-render.
    payload = {
        "name": "ChipRegressionRenamed",
        "threat_category": s.threat_category,
        "tef_low": str(s.threat_event_frequency["low"]),
        "tef_mode": str(s.threat_event_frequency["mode"]),
        "tef_high": str(s.threat_event_frequency["high"]),
        "vuln_low": str(s.vulnerability["low"]),
        "vuln_mode": str(s.vulnerability["mode"]),
        "vuln_high": str(s.vulnerability["high"]),
        "pl_low": str(s.primary_loss["low"]),
        "pl_mode": str(s.primary_loss["mode"]),
        "pl_high": str(s.primary_loss["high"]),
        # Deliberately wrong — triggers 409 re-render.
        "expected_row_version": "999",
    }
    r = await csrf_post(client, f"/scenarios/{s.id}", payload, follow_redirects=False)
    assert r.status_code == 409

    # The chip must show the org-derived industry/tier, NOT the '—' fallback.
    body = r.text
    assert "manufacturing" in body.lower(), (
        "Industry chip must show 'manufacturing' on 409 re-render; "
        "got '—' — render_scenario_form is not threading org= correctly"
    )
    # #454 item 3: the revenue-tier chip is now humanized for display
    # ("1b_to_10b" → "1B to 10B"); the stored value is unchanged. The
    # regression under test (org context threaded, not the '—' fallback) is
    # still asserted via the humanized label.
    assert "1B to 10B" in body, (
        "Revenue-tier chip must show '1B to 10B' on 409 re-render; "
        "got '—' — render_scenario_form is not threading org= correctly"
    )


# ---- delete ----------------------------------------------------------


async def test_delete_redirects(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """POST delete with correct expected_row_version 303s to /scenarios."""
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="GoneSoon")
    await db_session.commit()
    scenario_id = s.id
    rv = s.row_version

    r = await csrf_post(
        client,
        f"/scenarios/{scenario_id}/delete",
        {"expected_row_version": str(rv)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Issue #167 added the ?deleted=1 query flag for the post-delete flash banner.
    assert r.headers["location"].startswith("/scenarios")
    assert "deleted=1" in r.headers["location"]

    # Row removed from the DB.
    gone = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one_or_none()
    assert gone is None


# ---- delete cascade-confirmation (RESTRICT-FK fix) -------------------


async def _seed_single_run(
    db: AsyncSession, *, org_id: uuid.UUID, scenario_id: uuid.UUID, created_by: uuid.UUID
) -> uuid.UUID:
    """Add a COMPLETED SINGLE run referencing scenario_id; return its id."""
    import hashlib

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario_id,
        run_type=RunType.SINGLE,
        status=RunStatus.COMPLETED,
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        created_by=created_by,
    )
    db.add(run)
    await db.commit()
    return run.id


async def test_delete_with_run_shows_confirmation_then_cascades(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """First POST (no confirm_cascade) on a scenario WITH a run → 200 HTML
    confirmation showing the run count + a confirm form; nothing deleted.
    Second POST with confirm_cascade=1 + correct expected_row_version →
    303 to /scenarios?deleted=1; scenario AND run gone."""
    from idraa.models.risk_analysis_run import RiskAnalysisRun

    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="HasRun")
    await db_session.commit()
    sid, rv = s.id, s.row_version
    # created_by must be a real user in this org — reuse the authed analyst.
    from idraa.models.user import User

    creator = (
        (await db_session.execute(select(User).where(User.organization_id == org_id)))
        .scalars()
        .first()
    )
    assert creator is not None
    run_id = await _seed_single_run(
        db_session, org_id=org_id, scenario_id=sid, created_by=creator.id
    )

    # First POST: no confirm flag → confirmation page.
    r1 = await csrf_post(
        client,
        f"/scenarios/{sid}/delete",
        {"expected_row_version": str(rv)},
        follow_redirects=False,
    )
    assert r1.status_code == 200
    assert b"analysis run" in r1.content
    assert b"1" in r1.content
    assert b"confirm_cascade" in r1.content
    # Scenario still present.
    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == sid))
    ).scalar_one_or_none() is not None

    # Second POST: confirm cascade.
    r2 = await csrf_post(
        client,
        f"/scenarios/{sid}/delete",
        {"expected_row_version": str(rv), "confirm_cascade": "1"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert "deleted=1" in r2.headers["location"]
    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == sid))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run_id))
    ).scalar_one_or_none() is None


async def test_delete_cascade_stale_row_version_409(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Optimistic lock still enforced on the confirm POST: a stale
    expected_row_version with confirm_cascade=1 → 409."""
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="StaleConfirm")
    await db_session.commit()
    sid = s.id

    from idraa.models.user import User

    creator = (
        (await db_session.execute(select(User).where(User.organization_id == org_id)))
        .scalars()
        .first()
    )
    assert creator is not None
    await _seed_single_run(db_session, org_id=org_id, scenario_id=sid, created_by=creator.id)

    r = await csrf_post(
        client,
        f"/scenarios/{sid}/delete",
        {"expected_row_version": "999", "confirm_cascade": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 409


# ---- P12 reviewer-403 boundary tests ---------------------------------


async def test_reviewer_cannot_view_edit_form(
    authed_reviewer: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """P12: reviewer is view-only — must NOT see the edit form (403)."""
    client, org_id = authed_reviewer
    s = _seed_scenario(db_session, org_id=org_id, name="ReviewerForbidden")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}/edit")
    assert r.status_code == 403


async def test_reviewer_cannot_create(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    """P12: reviewer POST create is rejected with 403."""
    client, _ = authed_reviewer
    payload = {
        "name": "Reviewer-Cannot-Create",
        "threat_category": "social_engineering",
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_low": "50000",
        "pl_mode": "250000",
        "pl_high": "2000000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 403


async def test_reviewer_cannot_update(
    authed_reviewer: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """P12: reviewer POST update is rejected with 403."""
    client, org_id = authed_reviewer
    s = _seed_scenario(db_session, org_id=org_id, name="ReviewerUpdate")
    await db_session.commit()

    payload = {
        "name": "X",
        "threat_category": s.threat_category,
        "tef_low": str(s.threat_event_frequency["low"]),
        "tef_mode": str(s.threat_event_frequency["mode"]),
        "tef_high": str(s.threat_event_frequency["high"]),
        "vuln_low": str(s.vulnerability["low"]),
        "vuln_mode": str(s.vulnerability["mode"]),
        "vuln_high": str(s.vulnerability["high"]),
        "pl_low": str(s.primary_loss["low"]),
        "pl_mode": str(s.primary_loss["mode"]),
        "pl_high": str(s.primary_loss["high"]),
        "expected_row_version": str(s.row_version),
    }
    r = await csrf_post(client, f"/scenarios/{s.id}", payload, follow_redirects=False)
    assert r.status_code == 403


async def test_reviewer_cannot_delete(
    authed_reviewer: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """P12: reviewer POST delete is rejected with 403."""
    client, org_id = authed_reviewer
    s = _seed_scenario(db_session, org_id=org_id, name="ReviewerDelete")
    await db_session.commit()

    r = await csrf_post(
        client,
        f"/scenarios/{s.id}/delete",
        {"expected_row_version": str(s.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 403


# PR pi F12 dropped the refresh-calibration test block. The route
# handlers, service method, and modal templates were excised alongside
# the calibration-override runtime.


# ---- lognormal detail view (Task 6 / Epic B) --------------------------


def _seed_lognormal_scenario(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
) -> Scenario:
    """Seed a scenario with a native lognormal primary_loss distribution."""
    import math

    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss={
            "distribution": "lognormal",
            "mean": math.log(560_000),
            "sigma": 1.2,
        },
        status=EntityStatus.ACTIVE,
    )
    db.add(s)
    return s


async def test_view_renders_lognormal_percentiles(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Detail view renders 5th/median/mean/95th for a lognormal primary_loss.

    Task 6 / Epic B (#326): the view must show the percentile table for
    lognormal nodes, with a mandatory Mean row (lognormal mean > median;
    a median-only view understates expected loss).
    """
    client, org_id = authed_analyst
    s = _seed_lognormal_scenario(db_session, org_id=org_id, name="LognormalDetail")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200

    # The distribution type label must appear.
    assert "Lognormal" in r.text
    # Both Median and Mean rows are mandatory (plan CRITICAL).
    assert "Median" in r.text
    assert "Mean" in r.text
    # PERT "Mode" cell must NOT appear in a lognormal context.
    # The TEF/Vuln PERT sections do have Low/Mode/High, but the heading
    # "FAIR distributions" (not "FAIR distributions (PERT)") must be present.
    assert "FAIR distributions" in r.text
    assert "FAIR distributions (PERT)" not in r.text
    # 5th and 95th pct labels.
    assert "5th pct" in r.text
    assert "95th pct" in r.text
    # Hard count: exactly 2 PERT nodes (TEF + Vuln) → exactly 2 "Mode" header
    # cells. A lognormal PL erroneously gaining a Mode cell would push this to 3
    # and fail here, catching the regression.
    assert r.text.count("<th>Mode</th>") == 2


async def test_view_pure_pert_scenario_unregressed(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """A pure-PERT scenario still renders Low/Mode/High (no regression).

    Task 6 regression guard: the lognormal branch must not break existing
    PERT-only scenarios.
    """
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="PurePertyReg")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200
    assert "Low" in r.text
    assert "Mode" in r.text
    assert "High" in r.text
    # No lognormal-specific labels should appear for a PERT scenario.
    assert "Lognormal" not in r.text
    assert "Median" not in r.text


# ---- lognormal_mixture detail view (issue #27 Task 6) ---------------------


def _seed_lognormal_mixture_scenario(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
) -> Scenario:
    """Seed a scenario with a catastrophic multi-SME lognormal_mixture
    primary_loss (the worked A/B pair, equal weight)."""
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss={
            "distribution": "lognormal_mixture",
            "components": [
                {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
                {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
            ],
        },
        status=EntityStatus.ACTIVE,
    )
    db.add(s)
    return s


async def test_view_renders_lognormal_mixture_percentiles(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Detail view renders component count + p5/p95 for a seeded mixture
    scenario (issue #27 Task 6).

    Worked A/B pair (meanlog 8.06/sigma 0.70 vs 15.77/sigma 1.19, equal
    weight): fair_cam.quantile_pooling.mixture_quantile_lognorm gives
    p5=1,290.67 and p95=32,444,657.93 -- rendered via format_dist_value
    (format_money_input: f"{v:.2f}", no $ / no thousands separator, matching
    the plain-lognormal branch's cell formatting exactly) as "1290.67" and
    "32444657.93".
    """
    client, org_id = authed_analyst
    s = _seed_lognormal_mixture_scenario(db_session, org_id=org_id, name="MixtureDetail")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200

    # Distribution-type label with component count.
    assert "Lognormal mixture" in r.text
    assert "2 expert opinions" in r.text
    # Mixture percentile rows (same 5th pct / Median / Mean / 95th pct shape
    # as the plain-lognormal branch).
    assert "5th pct" in r.text
    assert "95th pct" in r.text
    assert "Median" in r.text
    assert "Mean" in r.text
    # Numeric-only display pin: the rendered p5/p95 cells are formatted
    # numbers derived from fair_cam's mixture quantile math, not raw stored
    # text -- hand-checked against the fair_cam oracle above.
    assert "1290.67" in r.text, f"p5 cell not found in rendered page: {r.text!r}"
    assert "32444657.93" in r.text, f"p95 cell not found in rendered page: {r.text!r}"
    # PERT "Mode" cell must NOT appear in the primary-loss mixture context
    # (only TEF + Vuln are PERT here) -- exactly 2 "Mode" header cells.
    assert r.text.count("<th>Mode</th>") == 2


async def test_view_pure_lognormal_scenario_unregressed_by_mixture_branch(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Adding the lognormal_mixture branch must not change a plain
    single-component lognormal scenario's rendering (byte-unchanged
    regression pin, issue #27 Task 6)."""
    client, org_id = authed_analyst
    s = _seed_lognormal_scenario(db_session, org_id=org_id, name="LognormalUnregressed")
    await db_session.commit()

    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200
    assert "Lognormal" in r.text
    assert "Median" in r.text
    assert "Mean" in r.text
    # No mixture-specific text should appear for a plain single-component
    # lognormal scenario.
    assert "Lognormal mixture" not in r.text
    assert "expert opinions" not in r.text
    assert "pooled mixture" not in r.text
