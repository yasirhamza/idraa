"""Scenario CRUD routes — analyst+ for full CRUD; reviewer view-only.

E5 shipped list + new + create. E6 ships detail/edit/delete. Refresh
calibration (E7) lands in a subsequent task and reuses the
``_calibration_panel.html`` partial established here.

Paranoid-review preamble fold-ins:

- **P4** Routes use ``require_user`` for reads and
  ``require_role(UserRole.ANALYST, UserRole.ADMIN)`` for writes. There
  is NO ``require_csrf`` per-route dependency — CSRF is enforced by
  the global :class:`idraa.middleware.csrf.CSRFMiddleware`
  fail-closed signed double-submit. Adding a route-level CSRF dep
  would duplicate the check (and pull in a name that doesn't exist
  in :mod:`idraa.routes.deps`).
- **P5** ``REVENUE_TIER_CHOICES`` (in
  :mod:`idraa.routes.scenario_form_helpers`) is sourced from
  :mod:`fair_cam.data.iris_2025` so the route's option list stays in
  lockstep with what :class:`idraa.schemas.scenario.ScenarioForm`
  validates. The plan body's hard-coded 4-entry list was wrong; the
  fair_cam dict has 6 keys.
- **P10** ``ip_address=client_ip(request)`` is threaded into every
  service mutation so AuditLog rows carry the originating IP (1.1.6.a
  I2 invariant).

``ScenarioService(db).create(...)`` per the E3.a refactor — db lives
in ``__init__``, methods take only entity-specific kwargs. Mirrors
``OverlayService`` / ``CalibrationOverrideService`` precedent.

Industry choices are restricted to the calibratable subset
(:data:`idraa.services.industry_mapping.V3_TO_FAIR_CAM_INDUSTRY`
keys); all v3 ``IndustryType`` values are present in that map today,
but sourcing from the map rather than the enum directly future-proofs
the form against an enum addition that doesn't have a fair_cam
mapping yet (would land as a follow-up task to add a mapping or
explicit reject).

Transaction commit is owned by the ``get_db`` dependency. Handlers
never call ``await db.commit()`` directly — same pattern as
:mod:`idraa.routes.overlays` /
:mod:`idraa.routes.calibration_overrides`.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from idraa.app import _csrf_token_from_request, templates
from idraa.config import get_settings
from idraa.errors import (
    ConflictError,
    LibraryEntryNotFoundError,
    LibraryEntryStatusError,
    NotFoundError,
    RunBusyError,
    ScenarioInUseError,
    ValidationError,
)
from idraa.models.enums import AssetClass, EntityStatus, ThreatActorType, UserRole
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from idraa.repositories.control_repo import ControlRepo
from idraa.repositories.overlay_repo import OverlayRepo
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.routes.deps import (
    client_ip,
    get_db,
    require_role,
    require_user,
)
from idraa.routes.scenario_form_helpers import (
    ASSET_CLASS_CHOICES,
    ATTACK_VECTOR_CHOICES,
    EFFECT_CHOICES,
    MAX_ATTACK_MAPPINGS,
    THREAT_ACTOR_TYPE_CHOICES,
    THREAT_CATEGORY_CHOICES,
    extract_attack_mapping_ids,
    flatten_validation_errors,
    form_defaults,
    form_from_scenario,
    load_attack_form_context,
    load_overlay_options,
    parse_expected_row_version,
    parse_scenario_form,
    render_scenario_form,
)
from idraa.schemas.scenario import ScenarioForm
from idraa.schemas.wizard_step3 import WizardStep3Submit
from idraa.services import sme_directory
from idraa.services.attack_coverage import build_attack_coverage
from idraa.services.attack_mappings import (
    copy_library_attack_mappings,
    ensure_attack_techniques_addable,
    set_scenario_attack_mappings,
)
from idraa.services.audit import log_bulk_export
from idraa.services.calibration import (
    calibration_context_from_org,
)
from idraa.services.flash import build_flash
from idraa.services.fx_rates import FxRateService, is_selectable_currency
from idraa.services.library_calibration import library_calibrated_pre_fill
from idraa.services.run_executor import _dict_to_fair_distribution
from idraa.services.scenario_control_recommendations import recommended_controls_for
from idraa.services.scenario_currency import convert_loss_inputs_to_usd
from idraa.services.scenario_library import (
    ScenarioLibraryService,
    available_facets,
)
from idraa.services.scenarios import ScenarioService, ScenarioVersionConflictError
from idraa.services.wizard_finalize import (
    _FINALIZE_SEMAPHORE,
    FinalizationError,
    FinalizeBudgetExceededError,
    build_scenario_payload,
    persist_estimates,
    pooling_component_fields,
    process_sme_estimates,
)
from idraa.services.wizard_helpers import (
    _quantile_pair,
    apply_overlay_multipliers,
    iris_baseline_for_form_v2,
)
from idraa.services.wizard_questions import (
    IMPACT_FIELDSETS,
    LIKELIHOOD_FIELDSETS,
    QUESTION_TOOLTIPS,
    ScenarioContext,
    render_question,
)
from idraa.services.wizard_state import (
    WizardDraftConflictError,
    WizardState,
    WizardStateService,
    load_sme_rows,
    seed_wizard_state_from_scenario,
)

router = APIRouter()

# The wizard step-1 library picker renders the FULL curated corpus on one
# page — no pager (a pager "next" would collide with the wizard's own
# "Next step" button). The library is small and curated (dozens of entries),
# so a single generous fetch replaces pagination plumbing — same rationale as
# the dashboard's ``_LIBRARY_REFERENCE_LIMIT``. Kept well above the real
# corpus size so growth doesn't silently truncate the picker.
_WIZARD_LIBRARY_PAGE_SIZE = 1000


# ---- list -------------------------------------------------------------


@router.get("/scenarios", response_class=HTMLResponse)
async def list_scenarios(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    status: EntityStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    deleted: int | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Issue #167 (#154 class): post-delete flash flag. Set to 1 by "
            "the scenario delete POST redirect; rendered as a 'success' "
            "banner here."
        ),
    ),
) -> HTMLResponse:
    """List scenarios for the current user's org, paginated + filterable.

    Filters: ``status`` (EntityStatus enum). Pagination is page-based with
    ``settings.list_page_size``. Industry is now an org-level attribute, not a
    per-scenario column (issue #88) — the ``?industry=`` query param has been
    removed.
    """
    _page_size = get_settings().list_page_size
    rows, total = await ScenarioRepo(db).list_for_org(
        organization_id=user.organization_id,
        status=status,
        limit=_page_size,
        offset=(page - 1) * _page_size,
    )
    # Issue #167: post-delete flash.
    flash = build_flash("Deleted scenario.", "success") if deleted == 1 else None
    return templates.TemplateResponse(
        request,
        "scenarios/list.html",
        {
            "current_user": user,
            "flash": flash,
            "scenarios": rows,
            "total": total,
            "page": page,
            "page_size": _page_size,
            "status_filter": status,
        },
    )


# ---- new + create -----------------------------------------------------


@router.get("/scenarios/new", response_class=HTMLResponse)
async def new_scenario_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> HTMLResponse:
    """Render the create form. Analyst+ only.

    Overlay options are fetched at render time so the
    ``tag — current v{n}`` label reflects the live version. The
    create handler re-resolves the pin at the moment of write.
    """
    overlay_options = await load_overlay_options(db, user.organization_id)
    available_controls = await ControlRepo(db).list_for_org(user.organization_id)
    # Issue #475 T9: no scenario yet on the create form — no submitted rows either.
    attack_ctx = await load_attack_form_context(db)
    organization = await db.get(Organization, user.organization_id)
    if organization is not None:
        ctx = calibration_context_from_org(organization)
        org_industry: str | None = ctx.industry
        org_revenue_tier: str | None = ctx.revenue_tier
    else:
        org_industry = None
        org_revenue_tier = None
    defaults = form_defaults()
    # Multi-currency P2: build the selectable list (USD always first, then rated codes).
    # Cannot use await inside a generator expression; build via explicit async loop.
    from idraa.currency import (
        SELECTABLE_CURRENCIES,
    )

    _fx_svc = FxRateService(db)
    _rated: list[str] = []
    for _c in sorted(SELECTABLE_CURRENCIES):
        if _c != "USD" and await _fx_svc.active_rate(user.organization_id, _c) is not None:
            _rated.append(_c)
    selectable_currencies = ["USD", *_rated]
    return templates.TemplateResponse(
        request,
        "scenarios/form.html",
        {
            "current_user": user,
            "flash": None,
            "scenario": None,
            "form": defaults,
            "overlay_options": overlay_options,
            "available_controls": available_controls,
            "threat_category_choices": THREAT_CATEGORY_CHOICES,
            "threat_actor_type_choices": THREAT_ACTOR_TYPE_CHOICES,
            "asset_class_choices": ASSET_CLASS_CHOICES,
            "attack_vector_choices": ATTACK_VECTOR_CHOICES,
            "effect_choices": EFFECT_CHOICES,
            "attack_technique_groups_json": attack_ctx.groups_json,
            "attack_technique_options": attack_ctx.options,
            "attack_mapping_rows": attack_ctx.rows,
            "org_industry": org_industry,
            "org_revenue_tier": org_revenue_tier,
            "form_action": "/scenarios",
            "form_method": "post",
            "errors": [],
            "selectable_currencies": selectable_currencies,
            "is_edit": False,
        },
    )


@router.post("/scenarios")
async def create_scenario(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """Create a scenario, auto-pinning override + overlays.

    Form parsing follows the ``parse_scenario_form`` precedent (numeric
    fields cast explicitly, optional fields stripped to None on empty).

    Error mapping:
    - :class:`idraa.errors.ValidationError` (includes
      :class:`ScenarioOverlayTagNotFoundError`) → 422 form re-render.
    - :class:`pydantic.ValidationError` → 422 form re-render.
    - :class:`idraa.errors.NotFoundError` → 404 (current handlers
      don't raise this on create, but the catch is forward-compatible
      with E3.a's exception hierarchy).
    """
    form_data = await request.form()
    # Pull repeated checkbox values explicitly via ``getlist`` BEFORE
    # collapsing to dict — ``dict(form_data)`` keeps only the LAST value
    # for repeated keys, which would silently drop control ids after the
    # first checkbox.
    control_ids_list: list[str] = []
    for v in form_data.getlist("mitigating_control_ids"):
        if isinstance(v, str):
            control_ids_list.append(v)
    raw: dict[str, Any] = dict(form_data)
    raw["mitigating_control_ids"] = control_ids_list

    overlay_options = await load_overlay_options(db, user.organization_id)
    available_controls = await ControlRepo(db).list_for_org(user.organization_id)
    create_org = await db.get(Organization, user.organization_id)

    # Arch3-I1 (issue #475 T9): extraction runs in its OWN try, AFTER the
    # org/overlay/controls loads above (the extraction-failure 422 render
    # needs those locals bound) and BEFORE the pre-parse early returns below
    # (entry-currency-not-selectable / rate-disappeared) — an ordinary user
    # mistake there must still re-render the operator's in-flight technique
    # rows, or fix-and-resubmit would silently wipe them.
    try:
        technique_ids = extract_attack_mapping_ids(raw)
    except ValueError as exc:
        # Arch2-N2: extraction itself failing is only reachable via
        # tampering (non-UUID value / too many rows). Create has no
        # persisted mappings to fall back to, so re-render with an empty
        # attack_ctx rather than the unparseable submitted rows.
        return render_scenario_form(
            request,
            user=user,
            org=create_org,
            scenario=None,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=[]),
            errors=[str(exc)],
            status_code=422,
        )

    # ── Multi-currency P2: extract entry_currency BEFORE parse so it does not
    # hit ScenarioForm's extra="forbid" gate. Validate → convert → set-on-row.
    entry_currency = (raw.pop("entry_currency", None) or "USD").strip()
    if not await is_selectable_currency(db, user.organization_id, entry_currency):
        return render_scenario_form(
            request,
            user=user,
            org=create_org,
            scenario=None,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[
                f"Entry currency {entry_currency!r} is not available. Configure an FX rate first."
            ],
            status_code=422,
        )
    entry_rate = None
    if entry_currency != "USD":
        rate_row = await FxRateService(db).active_rate(user.organization_id, entry_currency)
        # rate_row is guaranteed non-None here: is_selectable_currency confirmed it above.
        if rate_row is None:  # defensive — should be unreachable after is_selectable_currency
            return render_scenario_form(
                request,
                user=user,
                org=create_org,
                scenario=None,
                form_raw=raw,
                overlay_options=overlay_options,
                available_controls=available_controls,
                attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
                errors=[f"Entry currency {entry_currency!r} rate disappeared; try again."],
                status_code=422,
            )
        entry_rate = rate_row.usd_rate  # Decimal, already bounds-validated at upsert

    try:
        # convert_loss_inputs_to_usd raises ValueError on non-numeric/non-finite
        # loss values; keeping the call inside this try ensures those errors map
        # to 422 rather than escaping to 500 (Fix B — non-USD CREATE path).
        if entry_currency != "USD" and entry_rate is not None:
            raw = convert_loss_inputs_to_usd(raw, entry_currency, entry_rate)
        form = parse_scenario_form(raw)
    except (PydanticValidationError, KeyError, ValueError) as exc:
        errors = (
            flatten_validation_errors(exc)
            if isinstance(exc, PydanticValidationError)
            else [str(exc)]
        )
        return render_scenario_form(
            request,
            user=user,
            org=create_org,
            scenario=None,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=errors,
            status_code=422,
        )

    parsed_control_ids: list[uuid.UUID] = getattr(form, "_mitigating_control_ids", [])

    # Sec2-I2: pre-validate BEFORE ScenarioService.create — get_db auto-commits
    # on ANY successful handler exit including 422 renders, so rejecting a
    # technique AFTER create succeeds would persist the scenario (+ its
    # create-audit row) while telling the operator creation failed.
    try:
        await ensure_attack_techniques_addable(
            db,
            organization_id=user.organization_id,
            scenario_id=None,
            technique_ids=technique_ids,
        )
    except ValidationError as exc:
        return render_scenario_form(
            request,
            user=user,
            org=create_org,
            scenario=None,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[str(exc)],
            status_code=422,
        )

    try:
        scenario = await ScenarioService(db).create(
            organization_id=user.organization_id,
            form=form,
            current_user=user,
            ip_address=client_ip(request),
        )
    except ValidationError as exc:
        # Catches ScenarioOverlayTagNotFoundError + any other 422-class
        # service-layer validation failure. Re-render the form with the
        # service's message so the analyst can correct the offending tag.
        return render_scenario_form(
            request,
            user=user,
            org=create_org,
            scenario=None,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[str(exc)],
            status_code=422,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Set entry-currency provenance on the row (set-on-row; no service signature change).
    # The stored distributions are the USD source of truth; these are immutable metadata.
    scenario.entry_currency = entry_currency
    scenario.entry_rate = entry_rate

    # PR pi F12: mc_iterations is collected at run-creation time, not on
    # the scenario form. Mitigating-controls join still rides along.
    await ScenarioRepo(db).set_mitigating_controls(
        scenario_id=scenario.id,
        organization_id=user.organization_id,
        control_ids=parsed_control_ids,
    )

    # Issue #475 T9: pre-validation above means this should never raise on
    # user input — the except block is defense-in-depth only.
    try:
        await set_scenario_attack_mappings(
            db,
            scenario_id=scenario.id,
            organization_id=user.organization_id,
            technique_ids=technique_ids,
            actor_id=user.id,
            ip_address=client_ip(request),
        )
    except ValidationError as exc:
        return render_scenario_form(
            request,
            user=user,
            org=create_org,
            scenario=None,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[str(exc)],
            status_code=422,
        )

    return RedirectResponse(url=f"/scenarios/{scenario.id}", status_code=303)


# ---- export ----------------------------------------------------------


@router.get("/scenarios/export")  # B5: MUST be declared before /scenarios/{scenario_id}
async def scenarios_export(
    request: Request,
    format: str = "csv",
    status: EntityStatus | None = Query(default=None),  # honor the list's status filter (I1)
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),  # B3: any authenticated user, not require_role(VIEWER)
) -> Response:
    """Bulk export the org's scenarios (honoring the ?status= filter) — any authenticated user.

    Plan-gate B5/Sec-I1: registered BEFORE /scenarios/{scenario_id} so FastAPI's
    declaration-order match does not route "export" to the UUID parser (→ 422).

    Plan-gate B3/Sec-B1: gated on ``require_user`` (any authenticated user),
    NOT ``require_role(VIEWER)`` — a strict VIEWER allowlist would 403 admins
    and analysts. Export is a read; all authenticated roles may export.

    Plan-gate Sec-3: scoped by org via ``user.organization_id`` — cross-org IDOR
    is not possible because ``list_for_org`` applies the org_id predicate.
    """
    from idraa.services.scenario_export import export_csv_response, export_json_response

    rows_page, _total = await ScenarioRepo(db).list_for_org(
        organization_id=user.organization_id,
        status=status,
        limit=10_000,
    )
    fmt = "json" if format == "json" else "csv"
    # #304: bulk egress audit row (count + format + honored filters + ip).
    await log_bulk_export(
        db,
        organization_id=user.organization_id,
        entity_type="scenario",
        fmt=fmt,
        count=len(rows_page),
        user_id=user.id,
        ip_address=client_ip(request),
        filters={"status": status.value} if status is not None else None,
    )
    if fmt == "json":
        return export_json_response(rows_page, filename="scenarios.json")
    return export_csv_response(rows_page, filename="scenarios.csv")


@router.get("/scenarios/{scenario_id}/export")
async def scenario_export_one(
    scenario_id: uuid.UUID,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),  # B3
) -> Response:
    """Export a single scenario — any authenticated user, org-scoped 404 on cross-org id.

    Cross-org IDs return 404 (NOT 403) so we don't leak existence of scenarios
    owned by other orgs (mirrors view_scenario's B9/B10 precedent).
    """
    from idraa.services.scenario_export import export_csv_response, export_json_response

    scenario = await db.get(Scenario, scenario_id)
    if scenario is None or scenario.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if format == "json":
        return export_json_response([scenario], filename=f"scenario-{scenario_id}.json")
    return export_csv_response([scenario], filename=f"scenario-{scenario_id}.csv")


@router.get("/scenarios/attack-coverage", response_class=HTMLResponse)
async def attack_coverage_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    """ATT&CK technique coverage across the org's ACTIVE scenarios.

    Read-only — any authenticated role (B3 precedent). Registered BEFORE
    /scenarios/{scenario_id} (B5 declaration-order precedent). Coverage
    figures are v3 view-model derivations, not FAIR-grounded.
    """
    vm = await build_attack_coverage(db, organization_id=user.organization_id)
    return templates.TemplateResponse(
        request,
        "scenarios/attack_coverage.html",
        {"current_user": user, "flash": None, "vm": vm},
    )


@router.get("/scenarios/_attack_mapping_row", response_class=HTMLResponse)
async def scenario_attack_mapping_row_partial(
    request: Request,
    # Arch2-I1: bound = the shared cap constant, NOT a magic 100 — a scenario
    # holding 101-200 mappings must still be able to "+ Add". Arch3-N2: this
    # query bound is a sanity bound only; the extractor's cap at submit time
    # is the authoritative gate (sparse indices can exceed the row count).
    index: int = Query(0, ge=0, le=MAX_ATTACK_MAPPINGS),
    # Arch2-I2: the EDIT form passes its scenario id so a new row's hidden
    # <select> includes the scenario's deprecated survivors — without it, a
    # removed-then-re-picked survivor commits into a select with no matching
    # <option>, silently resetting to "" (combobox shows the pick, submit
    # blocks on an sr-only element). New-form pages pass nothing.
    scenario_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
) -> Response:
    """HTMX partial: one blank ATT&CK mapping row for '+ Add technique'.

    Registered BEFORE /scenarios/{scenario_id} (declaration-order matching —
    the B5 precedent above): '_attack_mapping_row' would otherwise be parsed
    as a scenario UUID and 422.
    """
    scenario = None
    if scenario_id is not None:
        scenario = await ScenarioRepo(db).get_for_org(
            organization_id=user.organization_id, scenario_id=scenario_id
        )
        if scenario is None:
            raise HTTPException(status_code=404)  # org-scoped, no existence oracle
    ctx = await load_attack_form_context(db, scenario=scenario)
    return templates.TemplateResponse(
        request,
        "scenarios/_attack_mapping_row.html",
        {
            "index": index,
            "initial_value": "",
            "row_source": "user",
            # Arch-I5: only the flat option list rides the partial — the
            # grouped catalog island already exists on the page.
            "attack_technique_options": ctx.options,
        },
    )


# ---- view / edit / update / delete -----------------------------------
# Routes with /{scenario_id} go LAST so the literal sub-paths
# (/new, etc.) match first. FastAPI uses registration order, so this
# ordering matters (mirrors the overlays / calibration_overrides
# router precedent).


@router.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
async def view_scenario(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    """Render the scenario detail page.

    Org-scoped lookup. Cross-org IDs return None → 404 (NOT 403) so we
    don't leak existence of scenarios owned by other orgs (B9/B10
    paranoid-review precedent). Eager-loads mitigating_controls for the
    detail card (#68 UAT — view page must show configured controls
    without forcing operator into edit mode).
    """
    stmt = (
        select(Scenario)
        .where(
            Scenario.id == scenario_id,
            Scenario.organization_id == user.organization_id,
        )
        .options(
            selectinload(Scenario.mitigating_controls),
            selectinload(Scenario.organization),
        )
    )
    scenario = (await db.execute(stmt)).scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404)

    # P2c §6.3: nudge un-adopted recommended controls from the source library entry.
    # Custom scenarios (no library_pin) get an empty list → the panel renders nothing.
    recommendations: list[Any] = []
    if scenario.library_pin and scenario.library_pin.get("entry_id"):
        src_entry = await ScenarioLibraryRepo(db).get_by_id_version(
            uuid.UUID(scenario.library_pin["entry_id"]),
            int(scenario.library_pin.get("version") or 1),
        )
        if src_entry is not None:
            all_recs = await recommended_controls_for(
                db, entry=src_entry, org_id=user.organization_id
            )
            recommendations = [r for r in all_recs if not r.adopted]  # un-adopted only (§6.3)

    return templates.TemplateResponse(
        request,
        "scenarios/view.html",
        {
            "current_user": user,
            "flash": None,
            "scenario": scenario,
            "recommendations": recommendations,
            "can_adopt": user.role in (UserRole.ADMIN, UserRole.ANALYST),
        },
    )


@router.get("/scenarios/{scenario_id}/edit", response_class=HTMLResponse)
async def edit_scenario_form(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> HTMLResponse:
    """Render the scenario edit form. Analyst+ only.

    The hidden ``expected_row_version`` input is templated from
    ``scenario.row_version`` (P9 — the int row_version is the
    optimistic-lock primitive, NOT the descriptive ``version: str``).
    """
    # Eager-load mitigating_controls so we can surface links to non-ACTIVE
    # controls (issue #217) that the ACTIVE-only available_controls list omits.
    edit_stmt = (
        select(Scenario)
        .where(
            Scenario.id == scenario_id,
            Scenario.organization_id == user.organization_id,
        )
        .options(selectinload(Scenario.mitigating_controls))
    )
    scenario = (await db.execute(edit_stmt)).scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404)
    overlay_options = await load_overlay_options(db, user.organization_id)
    available_controls = await ControlRepo(db).list_for_org(user.organization_id)
    available_ids = {c.id for c in available_controls}
    inactive_linked_controls = [
        c for c in scenario.mitigating_controls if c.id not in available_ids
    ]
    # Issue #475 T9: render the scenario's existing mappings as initial rows.
    attack_ctx = await load_attack_form_context(db, scenario=scenario)
    organization = await db.get(Organization, user.organization_id)
    if organization is not None:
        edit_ctx = calibration_context_from_org(organization)
        edit_org_industry: str | None = edit_ctx.industry
        edit_org_revenue_tier: str | None = edit_ctx.revenue_tier
    else:
        edit_org_industry = None
        edit_org_revenue_tier = None
    return templates.TemplateResponse(
        request,
        "scenarios/form.html",
        {
            "current_user": user,
            "flash": None,
            "scenario": scenario,
            "form": form_from_scenario(scenario),
            "overlay_options": overlay_options,
            "available_controls": available_controls,
            "inactive_linked_controls": inactive_linked_controls,
            "threat_category_choices": THREAT_CATEGORY_CHOICES,
            "threat_actor_type_choices": THREAT_ACTOR_TYPE_CHOICES,
            "asset_class_choices": ASSET_CLASS_CHOICES,
            "attack_vector_choices": ATTACK_VECTOR_CHOICES,
            "effect_choices": EFFECT_CHOICES,
            "attack_technique_groups_json": attack_ctx.groups_json,
            "attack_technique_options": attack_ctx.options,
            "attack_mapping_rows": attack_ctx.rows,
            "org_industry": edit_org_industry,
            "org_revenue_tier": edit_org_revenue_tier,
            "form_action": f"/scenarios/{scenario.id}",
            "form_method": "post",
            "errors": [],
            # Multi-currency P2 (Task 3.5): pass is_edit=True so the template renders
            # entry_currency/entry_rate as read-only provenance (not an editable select).
            # entry_currency/entry_rate are accessed via scenario.entry_currency /
            # scenario.entry_rate in the template (scenario is already in context).
            "is_edit": True,
        },
    )


@router.post("/scenarios/{scenario_id}")
async def update_scenario(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """Apply edits to a scenario.

    P9: ``expected_row_version`` (int) is read from the form, NOT
    ``expected_version`` (which is the descriptive str ``version`` field).
    On :class:`ConflictError` we render the form with status 409 and a
    reload-and-retry message — never 500, never generic 422.

    Pins are read-only on update per spec §5.4 / §6.8.3 — even if the
    operator edits industry or revenue_tier, the pin keeps pointing at
    its original CalibrationOverride row. Refresh-calibration (E7) is
    the analyst's opt-in path to re-resolve pins.
    """
    scenario = await ScenarioRepo(db).get_for_org(
        organization_id=user.organization_id,
        scenario_id=scenario_id,
    )
    if scenario is None:
        raise HTTPException(status_code=404)

    form_data = await request.form()
    control_ids_list: list[str] = []
    for v in form_data.getlist("mitigating_control_ids"):
        if isinstance(v, str):
            control_ids_list.append(v)
    raw: dict[str, Any] = dict(form_data)
    raw["mitigating_control_ids"] = control_ids_list

    overlay_options = await load_overlay_options(db, user.organization_id)
    available_controls = await ControlRepo(db).list_for_org(user.organization_id)
    update_org = await db.get(Organization, user.organization_id)

    # Arch3-I1 (issue #475 T9): extraction runs in its OWN try, AFTER the
    # org/overlay/controls loads above and BEFORE the pre-parse early return
    # below (expected_row_version) — an ordinary optimistic-lock mismatch
    # must still re-render the operator's in-flight technique rows.
    try:
        technique_ids = extract_attack_mapping_ids(raw)
    except ValueError as exc:
        # Arch2-N2: extraction itself failing is only reachable via
        # tampering. Re-render from the scenario's PERSISTED mappings (not
        # an empty list) so a blind fix-and-resubmit can't wipe all mappings.
        return render_scenario_form(
            request,
            user=user,
            org=update_org,
            scenario=scenario,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, scenario=scenario),
            errors=[str(exc)],
            status_code=422,
        )

    # Multi-currency P2: entry_currency is pinned at create and read-only on edit
    # (immutable provenance — mirrors the calibration-pin pattern at scenarios.py:546-549).
    # The edit form displays and stores USD values; re-converting them here would corrupt
    # the distribution by dividing by the rate again. Do NOT read entry_currency from raw
    # and do NOT call convert_loss_inputs_to_usd. The scenario.entry_currency /
    # scenario.entry_rate columns are left untouched by ScenarioService.update() (they
    # are not in ScenarioForm) and are therefore carried forward unchanged automatically.

    # Read expected_row_version explicitly. Missing/non-int → 422 with a
    # template-regression-friendly message (mirrors overlays B8 pattern).
    expected_row_version = parse_expected_row_version(raw.pop("expected_row_version", None))
    if expected_row_version is None:
        return render_scenario_form(
            request,
            user=user,
            org=update_org,
            scenario=scenario,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=["expected_row_version: missing or invalid hidden form field"],
            status_code=422,
        )

    try:
        form = parse_scenario_form(raw)
    except (PydanticValidationError, KeyError, ValueError) as exc:
        errors = (
            flatten_validation_errors(exc)
            if isinstance(exc, PydanticValidationError)
            else [str(exc)]
        )
        return render_scenario_form(
            request,
            user=user,
            org=update_org,
            scenario=scenario,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=errors,
            status_code=422,
        )

    parsed_control_ids = getattr(form, "_mitigating_control_ids", [])

    # Sec2-I2: pre-validate BEFORE ScenarioService.update — get_db auto-commits
    # on ANY successful handler exit including 422 renders, so rejecting a
    # technique AFTER update succeeds would commit the field diff + row_version
    # bump + update-audit while telling the operator the update failed.
    try:
        await ensure_attack_techniques_addable(
            db,
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            technique_ids=technique_ids,
        )
    except ValidationError as exc:
        return render_scenario_form(
            request,
            user=user,
            org=update_org,
            scenario=scenario,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[str(exc)],
            status_code=422,
        )

    try:
        updated = await ScenarioService(db).update(
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            form=form,
            expected_row_version=expected_row_version,
            current_user=user,
            ip_address=client_ip(request),
        )
    except ConflictError as exc:
        return render_scenario_form(
            request,
            user=user,
            org=update_org,
            scenario=scenario,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[
                "Another user updated this scenario — please reload and "
                "retry your edit. " + str(exc)
            ],
            status_code=409,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        return render_scenario_form(
            request,
            user=user,
            org=update_org,
            scenario=scenario,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[str(exc)],
            status_code=422,
        )

    # PR pi F12: mc_iterations dropped from scenario form (run-form now
    # owns it). Mitigating-controls join still applies.
    _ = updated  # consumed via repo update below; service.update returns it for tests
    # Issue #217: the edit form renders checkboxes ONLY for ACTIVE controls
    # (ControlRepo.list_for_org filters to EntityStatus.ACTIVE). A control
    # that became DRAFT/DEPRECATED while still linked has no checkbox, so its
    # id is absent from the submission — a naive full diff-apply would DELETE
    # that link (the reported data loss). Scope removals to the eligible set
    # (the controls the form could actually render) so links to non-ACTIVE
    # controls survive the edit.
    eligible_control_ids = {c.id for c in available_controls}
    await ScenarioRepo(db).set_mitigating_controls(
        scenario_id=scenario_id,
        organization_id=user.organization_id,
        control_ids=parsed_control_ids,
        eligible_control_ids=eligible_control_ids,
    )

    # Issue #475 T9: pre-validation above means this should never raise on
    # user input — the except block is defense-in-depth only. Placed BEFORE
    # the final redirect, mirroring set_mitigating_controls's position.
    try:
        await set_scenario_attack_mappings(
            db,
            scenario_id=scenario_id,
            organization_id=user.organization_id,
            technique_ids=technique_ids,
            actor_id=user.id,
            ip_address=client_ip(request),
        )
    except ValidationError as exc:
        return render_scenario_form(
            request,
            user=user,
            org=update_org,
            scenario=scenario,
            form_raw=raw,
            overlay_options=overlay_options,
            available_controls=available_controls,
            attack_ctx=await load_attack_form_context(db, submitted_ids=technique_ids),
            errors=[str(exc)],
            status_code=422,
        )

    return RedirectResponse(url=f"/scenarios/{scenario_id}", status_code=303)


@router.post("/scenarios/{scenario_id}/delete")
async def delete_scenario(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """Hard-delete a scenario. Analyst+ only.

    P9 optimistic lock on ``expected_row_version`` (int). On
    :class:`ConflictError` we surface 409; on missing/non-int field we
    surface 422 (template regression / hand-crafted POST).
    """
    form_data = await request.form()
    expected_row_version = parse_expected_row_version(form_data.get("expected_row_version"))
    if expected_row_version is None:
        raise HTTPException(
            status_code=422,
            detail="expected_row_version: missing or invalid hidden form field",
        )
    # Two-step cascade confirmation: a scenario with >=1 SINGLE run can't be
    # hard-deleted without taking its runs with it (RESTRICT FK). The first
    # POST (no confirm flag) renders a confirmation page; the confirm form
    # re-POSTs here with confirm_cascade=1.
    confirm_cascade = form_data.get("confirm_cascade") == "1"

    try:
        await ScenarioService(db).delete(
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            expected_row_version=expected_row_version,
            current_user=user,
            cascade_runs=confirm_cascade,
            ip_address=client_ip(request),
        )
    except ScenarioInUseError as exc:
        # Has runs + not yet confirmed: render the cascade-confirmation step
        # (200 HTML, NOT a redirect). Re-read the CURRENT row_version for the
        # confirm form's hidden field so the confirm POST passes the
        # optimistic lock. ScenarioInUseError subclasses ConflictError, so
        # this clause MUST precede the bare ConflictError clause below.
        scenario = await ScenarioRepo(db).get_for_org(
            organization_id=user.organization_id,
            scenario_id=scenario_id,
        )
        if scenario is None:  # raced delete between the two reads
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "scenarios/confirm_delete.html",
            {
                "current_user": user,
                "scenario": scenario,
                "run_count": exc.run_count,
            },
        )
    except RunBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Issue #167 (same class as #154): query-string flash so the list
    # page can confirm the delete.
    return RedirectResponse(url="/scenarios?deleted=1", status_code=303)


# Declaration order vs the /{scenario_id} catch-alls is irrelevant here:
# Starlette never matches a 3-segment request against a 2-segment pattern
# (same precedent as /{scenario_id}/delete above). Only SAME-depth routes
# like /scenarios/export need the declare-before-catch-all ordering.
@router.post("/scenarios/{scenario_id}/confirm-vuln-framing")
async def confirm_vuln_framing(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """Audit-F2: affirm a legacy scenario's vulnerability is already inherent.

    Analyst+ only (reviewer is read-only). CSRF enforced by the global
    middleware. Cross-org / missing ids surface 404 (NOT 403 — no existence
    oracle, plan-gate Sec-F2-I1). Redirect target is fixed (path-derived
    UUID) — no open-redirect surface. No expected_row_version by design:
    the flip is idempotent; see ScenarioService.confirm_vuln_framing.
    """
    try:
        await ScenarioService(db).confirm_vuln_framing(
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            current_user=user,
            ip_address=client_ip(request),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/scenarios/{scenario_id}", status_code=303)


@router.post("/scenarios/{scenario_id}/promote")
async def promote_scenario(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """Epic #34 P1a: promote a DRAFT scenario to ACTIVE after review.

    Analyst+ only (reviewer is read-only). CSRF enforced by the global
    middleware. Cross-org / missing ids surface 404 (NOT 403 — no existence
    oracle, mirrors confirm_vuln_framing's Sec-F2-I1 precedent). Redirect
    target is fixed (path-derived UUID) — no open-redirect surface.
    """
    try:
        await ScenarioService(db).promote(
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            current_user=user,
            ip_address=client_ip(request),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(url=f"/scenarios/{scenario_id}", status_code=303)


@router.post("/scenarios/{scenario_id}/re-estimate")
async def start_reestimate_wizard(
    scenario_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> RedirectResponse:
    """#56: seed a re-estimation wizard draft from an existing scenario.

    Eligibility is universal (any source/status — owner decision): imports
    seed with empty SME rows; wizard-born scenarios rehydrate theirs from
    scenario_sme_estimates. The scenario itself is untouched until
    finalize; Cancel abandons the draft with no effect.

    CSRF enforced by the global CSRFMiddleware (preamble P4) — no
    per-route dependency, matching every sibling POST in this module.
    Cross-org / missing ids surface 404 (NOT 403 — no existence oracle,
    mirrors confirm_vuln_framing's Sec-F2-I1 precedent). Amendment 12:
    ``Scenario.mitigating_controls`` is ``lazy="selectin"`` so the
    attribute access below is already eager-loaded — no extra eager-load
    variant needed.
    """
    scenario = await ScenarioRepo(db).get_for_org(
        organization_id=user.organization_id, scenario_id=scenario_id
    )
    if scenario is None:
        raise HTTPException(404, "scenario not found")
    sme_rows = await load_sme_rows(db, scenario.id, user.organization_id)
    control_ids = [str(c.id) for c in (scenario.mitigating_controls or [])]
    wizard_svc = WizardStateService(db)
    state = await wizard_svc.get_or_create(user_id=user.id, organization_id=user.organization_id)
    seeded = seed_wizard_state_from_scenario(
        scenario,
        sme_estimates=sme_rows,
        mitigating_control_ids=control_ids,
        tx_id=state.tx_id,
    )
    seeded.version_token = state.version_token
    await wizard_svc.advance_step(
        user_id=user.id, organization_id=user.organization_id, state=seeded
    )
    await db.commit()
    return RedirectResponse(url=f"/scenarios/new/wizard/step/2?tx={seeded.tx_id}", status_code=303)


# ---- wizard -----------------------------------------------------------


def _form_str(form: Any, key: str) -> str | None:
    """Return form[key] stripped, or None if blank/missing.

    Wizard step-2 SELECT inputs (industry, revenue_tier, ...) come in as
    empty strings when the user leaves them blank. Convert to None so
    downstream None-checks work.
    """
    val = form.get(key)
    if val is None or val == "":
        return None
    return str(val) if isinstance(val, str) else None


def _build_rendered_questions(state: WizardState) -> dict[str, str]:
    """Render the per-fieldset scenario-context question copy for the FAIR
    pages (step 3 Likelihood + step 4 Impact).

    Built from the WizardState's step-2 fields (threat_actor_type,
    attack_vector, asset_class) per the templates in
    ``services/wizard_questions.QUESTION_TEMPLATES``. Consumed by
    ``_fair_page_context`` so the shared ``_fair_params_form_inner.html``
    partial (which expects ``rendered_questions[fieldset_key]``) renders
    identically on the initial GET and the prefill/apply-overlay HTMX swaps.
    """
    ctx = ScenarioContext(
        threat_actor_type=(
            ThreatActorType(state.threat_actor_type) if state.threat_actor_type else None
        ),
        attack_vector=state.attack_vector,
        asset_class=AssetClass(state.asset_class) if state.asset_class else None,
    )
    return {fs: render_question(fs, ctx) for fs in ("tef", "vuln", "pl", "sl")}


def _round_initial_rows_for_display(
    rows_by_fieldset: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Round ``initial_rows`` low/high values for clean Alpine x-model display.

    PR #247 UAT bug: the T1 quantile-pooling pipeline (and IRIS rescaling)
    can produce honest tiny-floats like ``1.5146e-06`` for the 5th
    percentile of a long-tailed distribution. These round-trip cleanly
    in the maths but render as truncated scientific notation
    ("1.5146025633444114e-0…") inside ``<input type="number">`` once
    Alpine's ``x-model.number`` writes them into the DOM.

    Per fieldset:
      - ``tef`` (rate, events/year): float, 4 decimals
      - ``vuln`` (probability, 0..1): float, 4 decimals
      - ``pl`` / ``sl`` (money, $):  STRING ``"{value:.2f}"`` (2 decimals,
        trailing zeros preserved)

    UAT R2 Bug B fix: PL/SL low/high are emitted as PRE-FORMATTED STRINGS
    rather than Python floats, because Alpine's ``x-model`` binds the
    string directly into the ``<input type="text">`` and any trailing
    zeros survive the round-trip. With ``x-model.number`` on
    ``<input type="number">`` (the pre-fix shape) the value got coerced
    to float on both ends, and HTML number inputs strip trailing zeros
    on display regardless of how the source was formatted — so e.g.
    ``388920.40`` rendered as ``388920.4`` and the user complained about
    inconsistent decimal-place display in the same row.

    The backend ``_parse_sme_rows_subset`` coerces every low/high via
    ``float(...)`` so the string form is parsed back identically;
    Pydantic + ScenarioSMEEstimate constraints (low > 0, high >= low)
    still enforce shape on submit.

    Persisted state (``state.sme_estimates``) is NOT mutated — only the
    display-bound copy returned from this helper. The submitted form
    re-parses values as the analyst typed them, and the pooling pipeline
    re-fits against the raw inputs on the next step.
    """
    import contextlib

    decimals_by_fieldset = {"tef": 4, "vuln": 4, "pl": 2, "sl": 2}
    string_format_fieldsets = {"pl", "sl"}
    out: dict[str, list[dict[str, Any]]] = {}
    for fs, rows in rows_by_fieldset.items():
        nd = decimals_by_fieldset.get(fs, 4)
        as_string = fs in string_format_fieldsets
        rounded_rows: list[dict[str, Any]] = []
        for row in rows:
            new_row = dict(row)
            for key in ("low", "high"):
                if key in new_row and new_row[key] is not None:
                    with contextlib.suppress(TypeError, ValueError):
                        rounded = round(float(new_row[key]), nd)
                        new_row[key] = f"{rounded:.{nd}f}" if as_string else rounded
            rounded_rows.append(new_row)
        out[fs] = rounded_rows
    return out


def _iris_seed_rows(
    iris_form: dict[str, dict[str, float] | None],
    iris_sme_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Build ``state.sme_estimates`` from an IRIS quantile-pair dict.

    MD-7: IRIS pre-fill REPLACES current rows with a single IRIS-attributed
    row per fieldset. Fieldsets where ``iris_form`` returned ``None``
    (missing data, unsupported distribution_type) are omitted from the
    output so the UI renders them empty rather than as ``(0, 0)``.
    """
    return {
        fs: [
            {
                "sme_id": iris_sme_id,
                "low": iris_form[fs]["low"],  # type: ignore[index]
                "high": iris_form[fs]["high"],  # type: ignore[index]
            }
        ]
        for fs in ("tef", "vuln", "pl", "sl")
        if iris_form.get(fs)
    }


def _library_seed_rows(
    state: WizardState,
    library_sme_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Build ``state.sme_estimates`` from a library entry's CURATED distributions
    (#wizard-library-prefill).

    Mirrors ``_iris_seed_rows`` but sources each fieldset's {low, high} p5/p95
    pair from the entry's own distribution (``state.threat_event_frequency`` /
    ``vulnerability`` / ``primary_loss`` / ``secondary_loss``, seeded by
    ``_seed_state_from_library_entry``) via the SAME analytic ``_quantile_pair``
    extractor the IRIS path uses — so a library-derived scenario carries the
    archetype's threat-specific values instead of the threat-blind IRIS baseline.

    Fieldsets whose curated dict is empty/None, or whose distribution_type
    ``_quantile_pair`` cannot handle, are omitted (render-empty contract,
    matching ``_iris_seed_rows`` + its ``_safe`` swallow)."""
    fieldset_dists: list[tuple[str, dict[str, Any] | None]] = [
        ("tef", state.threat_event_frequency),
        ("vuln", state.vulnerability),
        ("pl", state.primary_loss),
        ("sl", state.secondary_loss),
    ]
    rows: dict[str, list[dict[str, Any]]] = {}
    for fs, dist_dict in fieldset_dists:
        if not dist_dict:
            continue
        try:
            pair = _quantile_pair(_dict_to_fair_distribution(dist_dict))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            # Malformed/degenerate curated dist (bad type, None value, or an
            # OverflowError from a pathological lognormal) → omit the fieldset,
            # matching the IRIS `_safe` render-empty contract + the #306
            # finite-guard philosophy. Unreachable for real (finite-validated)
            # library entries; defense-in-depth.
            continue
        rows[fs] = [{"sme_id": library_sme_id, "low": pair["low"], "high": pair["high"]}]
    return rows


async def _resolve_tx(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    tx_str: str | None,
) -> uuid.UUID | None:
    """Resolve the wizard tx UUID for this request.

    If ``tx_str`` is provided, parse and return it.
    Otherwise fall back to the most-recent draft for (user_id, organization_id)
    so back-button navigation without ?tx= still finds the user's session.

    Defense-in-depth: filter by organization_id too. v3 is single-org-per-user
    today, but if a user is ever moved between orgs, their old draft must NOT
    surface under a new org context — the wizard's pin scopes (override_id,
    library_entry_id) are org-scoped, so cross-org reuse would attach wrong pins.
    """
    if tx_str:
        return uuid.UUID(tx_str)
    stmt = (
        select(WizardDraft.tx_id)
        .where(
            WizardDraft.user_id == user_id,
            WizardDraft.organization_id == organization_id,
        )
        .order_by(WizardDraft.updated_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _seed_state_from_library_entry(
    db: AsyncSession,
    state: WizardState,
    entry_id: uuid.UUID,
    org_row: Organization,
) -> str:
    """Shared seeder: resolve entry, calibrate FAIR params, stamp scalar fields.

    Called from BOTH the GET deep-link handler AND the POST step-1 handler so
    the two paths produce byte-identical state seeds.  This is a mechanical
    extraction of the inline block that previously lived only in
    ``post_wizard_step_1`` (~lines 969-1011); the calibration math and field
    assignment order are unchanged.

    Returns the resolved entry's name so callers can use it for name-update
    logic (e.g. the POST path prepends it to the scenario name).

    The caller is responsible for:
    - raising HTTP 404 if the entry doesn't exist / isn't published (done
      differently in GET vs POST callers — GET degrades gracefully, POST
      raises HTTPException 404).
    - persisting state (advance_step + db.commit).

    Raises LibraryEntryNotFoundError / LibraryEntryStatusError so callers can
    translate to the appropriate HTTP response.
    """
    svc = ScenarioLibraryService(db)
    resolved = await svc.resolve_for_clone(
        entry_id=entry_id,
        organization_id=org_row.id,
    )

    state.library_entry_id = str(resolved.entry.id)
    state.library_entry_version = resolved.entry.version
    state.override_id = str(resolved.override.id) if resolved.override else None
    state.override_version = resolved.override.version if resolved.override else None

    # Org revenue-tier loss scaling was removed 2026-07-07 — the IRIS sector
    # envelope IS the calibration; PL/SL are entry-absolute here. TEF/Vuln stay
    # archetype-curated; controls modulate risk at MC time. No calibration
    # metadata is computed or stashed (no banner).
    form_dict, _calibration_metadata = library_calibrated_pre_fill(
        resolved.entry, resolved.override
    )
    state.threat_event_frequency = form_dict["tef"]
    state.vulnerability = form_dict["vuln"]
    state.primary_loss = form_dict["pl"]
    state.secondary_loss = form_dict["sl"]

    # Pre-fill step-2 scalar fields from canonical entry.
    state.threat_category = resolved.entry.threat_event_type.value
    state.threat_actor_type = resolved.entry.threat_actor_type.value
    state.asset_class = resolved.entry.asset_class.value
    state.attack_vector = resolved.entry.attack_vector

    # Milestone B (#loss-pert-overhaul): seed the scenario-level loss shape
    # from the entry's curated class; the analyst can override via the step-4
    # toggle.
    state.loss_shape = resolved.entry.loss_shape

    return resolved.entry.name


@router.get("/scenarios/new/wizard", response_class=HTMLResponse)
async def get_wizard_step_1(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
    library_entry_id: uuid.UUID | None = None,  # deep-link from /library/entries/{id}
) -> HTMLResponse:
    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
    )
    organization = await db.get(Organization, user.organization_id)
    if library_entry_id is not None:
        # Deep-link: seed library_entry_id AND all FAIR/scalar fields so
        # step-2 pre-fills identically to the POST step-1 path (WS4 fix).
        # Always re-seeds unconditionally on every GET deep-link request,
        # mirroring the POST step-1 path which re-seeds on every form submit.
        assert organization is not None  # noqa: S101
        # Deep-link to a missing/non-published entry: degrade gracefully
        # (redirect to plain wizard) rather than surfacing a 404. Stale
        # deep-links (e.g. deprecated entries) are the common case.
        with contextlib.suppress(LibraryEntryNotFoundError, LibraryEntryStatusError):
            await _seed_state_from_library_entry(db, state, library_entry_id, organization)
        await wiz.advance_step(
            user_id=user.id,
            organization_id=user.organization_id,
            state=state,
        )
    # Pre-fill the scenario name with a timestamp default so the analyst
    # never has to type a name to satisfy required-field validation.
    # User feedback: "scenario name generation is an important feature."
    if not state.name:
        from datetime import datetime

        state.name = f"Scenario {datetime.now():%Y-%m-%d %H:%M}"
    from idraa.routes.library import _parse_browse_filters

    svc = ScenarioLibraryService(db)
    lib_entries: list[Any] = []
    filters = _parse_browse_filters(request)
    facets: dict[str, Any] = {}
    if organization is not None:
        page = await svc.list_browseable(
            filters=filters,
            page=1,
            page_size=_WIZARD_LIBRARY_PAGE_SIZE,
        )
        lib_entries = page.entries
        facets = await available_facets(db)
    await db.commit()
    return templates.TemplateResponse(
        request,
        "scenarios/wizard/step_1_library.html",
        {
            "current_user": user,
            "flash": None,
            "state": state,
            "step": 1,
            "library_entries": lib_entries,
            "organization": organization,
            "filters": filters,
            "facets": facets,
        },
    )


@router.post("/scenarios/new/wizard/step/1", response_class=HTMLResponse)
async def post_wizard_step_1(
    request: Request,
    library_entry_id: str = Form(""),
    skip_library: str = Form("0"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
    )
    if skip_library == "1":
        state.library_entry_id = None
        state.library_entry_version = None
        state.override_id = None
        state.override_version = None
    elif library_entry_id:
        # Authenticated user implies FK-enforced org row exists; assert rather
        # than silently degrade to raw entry values (which would hide bugs).
        org_row = await db.get(Organization, user.organization_id)
        assert org_row is not None, (  # noqa: S101
            f"FK invariant: authenticated user {user.id} has no Organization row "
            f"for organization_id={user.organization_id}"
        )
        try:
            entry_name = await _seed_state_from_library_entry(
                db, state, uuid.UUID(library_entry_id), org_row
            )
        except (LibraryEntryNotFoundError, LibraryEntryStatusError) as exc:
            # Existence-oracle protection: respond identically to "not found"
            # for unknown UUIDs AND draft/deprecated entries — the 500-vs-404
            # differential would itself leak existence. Constant detail string;
            # do NOT use str(exc) (would embed the status name).
            raise HTTPException(status_code=404, detail="Library entry not available") from exc

        # UAT 2026-05-21: regenerate the auto-default scenario name with the
        # library entry name prepended for clarity (e.g. "" or "Scenario
        # 2026-05-21 05:09" → "Ransomware on Virtualization 2026-05-21
        # 05:09"). Covers two cases:
        #   1. Empty state.name — the typical first-pass case. The GET-side
        #      "Scenario YYYY-MM-DD HH:MM" default is set in-memory AFTER
        #      `advance_step` persists state_json, so the DB row's
        #      state_json.name is empty when the POST handler reads it.
        #   2. Persisted "Scenario YYYY-MM-DD HH:MM" default — fires when
        #      the user reached step 2 (which persists the name) without
        #      typing a custom name, then went back to step 1 and switched
        #      library entry.
        # Custom names typed by the operator (don't match the default
        # placeholder regex) are preserved.
        if not state.name or re.match(r"^Scenario \d{4}-\d{2}-\d{2} \d{2}:\d{2}$", state.name):
            from datetime import datetime

            state.name = f"{entry_name} {datetime.now():%Y-%m-%d %H:%M}"
    state.current_step = 2
    await wiz.advance_step(
        user_id=user.id,
        organization_id=user.organization_id,
        state=state,
    )
    await db.commit()
    return RedirectResponse(
        url=f"/scenarios/new/wizard/step/2?tx={state.tx_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/scenarios/new/wizard/_partials/library_cards", response_class=HTMLResponse)
async def get_wizard_library_cards_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> HTMLResponse:
    """HTMX hx-get target: returns the picker card list only (no shell) for
    search/filter changes on wizard step-1.  Mirrors /library/_partials/cards.
    """
    from idraa.routes.library import _parse_browse_filters

    filters = _parse_browse_filters(request)
    svc = ScenarioLibraryService(db)
    page = await svc.list_browseable(filters=filters, page=1, page_size=_WIZARD_LIBRARY_PAGE_SIZE)
    # Re-read the tx/state so the partial knows which entry is currently selected.
    resolved_tx = await _resolve_tx(
        db, user_id=user.id, organization_id=user.organization_id, tx_str=None
    )
    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=resolved_tx,
    )
    return templates.TemplateResponse(
        request,
        "scenarios/wizard/_step_1_library_cards.html",
        {
            "current_user": user,
            "library_entries": page.entries,
            "state": state,
        },
    )


@router.get("/scenarios/new/wizard/step/{n}", response_class=HTMLResponse)
async def get_wizard_step(
    n: int,
    request: Request,
    tx: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> HTMLResponse:
    wiz = WizardStateService(db)
    resolved_tx = await _resolve_tx(
        db, user_id=user.id, organization_id=user.organization_id, tx_str=tx
    )
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=resolved_tx,
    )
    await db.commit()
    if n < 1 or n > 6:
        raise HTTPException(status_code=400, detail="invalid step number")
    # Defensive: ensure state has a placeholder name. Step 1 GET sets this on
    # state creation, but a draft created before that patch might be missing it.
    if not state.name:
        from datetime import datetime

        state.name = f"Scenario {datetime.now():%Y-%m-%d %H:%M}"
    template = (
        f"scenarios/wizard/step_{n}_"
        f"{['library', 'basic', 'likelihood', 'impact', 'controls', 'review'][n - 1]}.html"
    )
    extra_ctx: dict[str, Any] = {}
    if n == 1:
        # Back-nav into step 1 (the _shell.html "Back" link from step 2) hits
        # this {n} handler, NOT get_wizard_step_1. Populate the picker cards +
        # filter facets here too, else the library grid renders empty on
        # back-nav. Same full-corpus fetch (no pager) as get_wizard_step_1.
        from idraa.routes.library import _parse_browse_filters

        step1_filters = _parse_browse_filters(request)
        step1_org = await db.get(Organization, user.organization_id)
        step1_svc = ScenarioLibraryService(db)
        extra_ctx["filters"] = step1_filters
        if step1_org is not None:
            step1_page = await step1_svc.list_browseable(
                filters=step1_filters,
                page=1,
                page_size=_WIZARD_LIBRARY_PAGE_SIZE,
            )
            extra_ctx["library_entries"] = step1_page.entries
            extra_ctx["facets"] = await available_facets(db)
        else:
            extra_ctx["library_entries"] = []
            extra_ctx["facets"] = {}
    if n == 2:
        # Step 2 renders threat_category / threat_actor_type / asset_class /
        # attack_vector dropdowns. Industry + revenue_tier are shown as read-only
        # chips sourced live from the org (issue #88 Task 8 — no longer stored
        # on WizardState).
        extra_ctx["attack_vector_choices"] = ATTACK_VECTOR_CHOICES
        step2_org = await db.get(Organization, user.organization_id)
        if step2_org is not None:
            step2_ctx = calibration_context_from_org(step2_org)
            extra_ctx["org_industry"] = step2_ctx.industry
            extra_ctx["org_revenue_tier"] = step2_ctx.revenue_tier
        else:
            extra_ctx["org_industry"] = None
            extra_ctx["org_revenue_tier"] = None
    if n == 2 and state.library_entry_id is not None:
        # Pre-fill: show selected library entry name if one was picked in step 1.
        entry_row = (
            await db.execute(
                select(ScenarioLibraryEntry).where(
                    ScenarioLibraryEntry.id == uuid.UUID(state.library_entry_id)
                )
            )
        ).scalar_one_or_none()
        if entry_row is not None:
            extra_ctx["selected_library_entry_name"] = entry_row.name
    if n in (3, 4):
        # Steps 3 (Likelihood: TEF+Vuln) and 4 (Impact: PL+SL) are evaluator-
        # style SME-row elicitation pages sharing _fair_params_form_inner.html.
        # On first visit to EITHER page (no rows yet), eager-seed ALL four
        # fieldsets from the IRIS industry baseline as a single row per fieldset
        # attributed to the per-org system-owned IRIS SME (lazy-created via
        # ``sme_directory.get_or_create_iris_sme``). The seed runs once on
        # whichever FAIR page is visited first (normally step 3), so a direct
        # entry to step 4 still renders a populated Impact page. The button-
        # driven POST/HTMX endpoints handle subsequent re-applications.
        step3_org = await db.get(Organization, user.organization_id)
        step3_ctx = calibration_context_from_org(step3_org) if step3_org is not None else None
        org_industry = step3_ctx.industry if step3_ctx is not None else None
        org_revenue_tier = step3_ctx.revenue_tier if step3_ctx is not None else None
        if not state.sme_estimates:
            # #wizard-library-prefill: a library-derived scenario seeds the SME
            # rows from the entry's CURATED distributions (threat-specific),
            # NOT the threat-blind IRIS industry baseline. From-scratch scenarios
            # keep the IRIS seed. The explicit "Reset to baseline" button (below)
            # is unchanged — it remains the deliberate reset-to-org-baseline path.
            seeded: dict[str, list[dict[str, Any]]] = {}
            if state.library_entry_id and state.threat_event_frequency:
                lib_sme, _ = await sme_directory.get_or_create_library_sme(
                    db,
                    user.organization_id,
                )
                seeded = _library_seed_rows(state, str(lib_sme.id))
            else:
                # Issue #88: ctx is org-derived, not snapshot. Always reflects
                # current tier — bug-fix for stale revenue_tier snapshot.
                iris_form = iris_baseline_for_form_v2(step3_ctx) if step3_ctx is not None else None
                if iris_form:
                    iris_sme, _ = await sme_directory.get_or_create_iris_sme(
                        db,
                        user.organization_id,
                    )
                    seeded = _iris_seed_rows(iris_form, str(iris_sme.id))
            if seeded:
                state.sme_estimates = seeded
                await wiz.advance_step(
                    user_id=user.id,
                    organization_id=user.organization_id,
                    state=state,
                )
                await db.commit()
        available_overlays = await OverlayRepo(db).list_active(
            organization_id=user.organization_id,
        )
        sme_dir = await sme_directory.list_for_dropdown(
            db,
            user.organization_id,
        )
        # _fair_page_context scopes fieldsets to this page, filters no-op
        # overlays, gates the calibration banner to Impact (PL/SL), and supplies
        # the (i) tooltips + rendered questions + rounded initial rows.
        extra_ctx.update(
            _fair_page_context(
                request=request,
                user=user,
                state=state,
                step=n,
                org_industry=org_industry,
                org_revenue_tier=org_revenue_tier,
                available_overlays=available_overlays,
                sme_directory_for_dropdown=sme_dir,
            )
        )
        # Milestone B (#loss-pert-overhaul): %-of-revenue display hint on the
        # capped pl/sl high inputs. Gated to n == 4 ONLY — step 3 shares
        # _fair_params_form_inner.html but its form has no page-level Alpine
        # scope, so exposing the value there would arm an x-text reading an
        # undefined annualRevenue (plan-gate A-N2). Display-only; no scaling.
        extra_ctx["org_annual_revenue"] = (
            float(step3_org.annual_revenue)
            if n == 4 and step3_org is not None and step3_org.annual_revenue is not None
            else None
        )
    if n == 5:
        # Pass org controls for the multi-select checklist.
        extra_ctx["org_controls"] = await ControlRepo(db).list_for_org(user.organization_id)
        # P2c §6.2: surface curated recommendations from the started-from library
        # entry. Render-only pre-check (NO GET state mutation) — the step-5 GET
        # commits early, so mutating state.mitigating_control_ids here would be an
        # unreliable GET side-effect. Pass a render-only precheck_control_ids set;
        # the POST still captures the user's real getlist("control_ids"), so
        # unchecking a pre-checked box is honored (pre-check is display-only).
        extra_ctx["recommendations"] = []
        extra_ctx["precheck_control_ids"] = set()
        if state.library_entry_id:
            src_entry = await ScenarioLibraryRepo(db).get_by_id_version(
                uuid.UUID(state.library_entry_id),
                state.library_entry_version or 1,
            )
            if src_entry is not None:
                recs = await recommended_controls_for(
                    db, entry=src_entry, org_id=user.organization_id
                )
                extra_ctx["recommendations"] = recs
                extra_ctx["precheck_control_ids"] = {
                    str(r.adopted_control_id)
                    for r in recs
                    if r.adopted and r.adopted_control_id is not None
                }
        extra_ctx["tx"] = state.tx_id  # for the from_wizard_tx hidden field
    if n == 6 and state.library_entry_id:
        # Resolve display name for the "Started from library: NAME vN" banner.
        repo = ScenarioLibraryRepo(db)
        entry = await repo.get_by_id_version(
            uuid.UUID(state.library_entry_id),
            state.library_entry_version or 1,
        )
        extra_ctx["library_entry_name"] = entry.name if entry else "(deleted)"
    if n == 6:
        # UAT 2026-05-21: the review template renders raw UUIDs for
        # `state.mitigating_control_ids` under "Mitigating controls",
        # which is unreadable. Build a {id_str: name} map so the
        # template can render human-readable names with a graceful
        # fallback to the UUID when a control row has been deleted
        # between the wizard step 5 controls pick and the step 6 review.
        step6_controls = await ControlRepo(db).list_for_org(user.organization_id)
        extra_ctx["control_name_by_id"] = {str(c.id): c.name for c in step6_controls}
        # F7: the evaluator-style wizard persists FAIR estimates into
        # state.sme_estimates (the old PERT-dist fields stay empty until
        # finalize). Build a per-fieldset Source + low/high display structure so
        # the review page renders the entered rows instead of a dash.
        review_sme_dir = await sme_directory.list_for_dropdown(db, user.organization_id)
        extra_ctx["review_fair_rows"] = _review_fair_rows(state.sme_estimates, review_sme_dir)
    return templates.TemplateResponse(
        request,
        template,
        {
            "current_user": user,
            "flash": None,
            "state": state,
            "step": n,
            **extra_ctx,
        },
    )


@router.post("/scenarios/new/wizard/step/{n}")
async def post_wizard_step(
    n: int,
    request: Request,
    tx: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    if n not in (2, 3, 4, 5):
        raise HTTPException(status_code=400, detail="invalid step number")
    wiz = WizardStateService(db)
    resolved_tx = await _resolve_tx(
        db, user_id=user.id, organization_id=user.organization_id, tx_str=tx
    )
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=resolved_tx,
    )
    form = await request.form()

    if n == 2:
        state.name = _form_str(form, "name")
        state.description = _form_str(form, "description")
        state.threat_category = _form_str(form, "threat_category")
        state.threat_actor_type = _form_str(form, "threat_actor_type")
        state.asset_class = _form_str(form, "asset_class")
        state.attack_vector = _form_str(form, "attack_vector")
    elif n in (3, 4):
        # Per-page SME-row persistence (2026-05-28 step-3 split). Step 3
        # (Likelihood) submits TEF+Vuln rows; step 4 (Impact) submits PL+SL.
        # Each POST persists ONLY its half, merged into state.sme_estimates so
        # the other half is preserved (merge-doesn't-clobber). Validation runs
        # via the existing Pydantic fieldset models; a rejected POST re-renders
        # the page with a flash at 422 and leaves state.sme_estimates UNCHANGED
        # (the merge happens strictly inside the validate-success path).
        page_fieldsets = ("tef", "vuln") if n == 3 else ("pl", "sl")
        try:
            # Issue #261: _parse_sme_rows_subset must run INSIDE the try — a
            # non-numeric low/high (float() ValueError) or a present-low /
            # missing-high pair (direct-subscript KeyError) would otherwise
            # escape as an uncaught 500 instead of the intended 422 flash.
            page_rows = _parse_sme_rows_subset(form, page_fieldsets)
            _validate_page_rows(page_rows)
        except (PydanticValidationError, ValueError, KeyError) as exc:
            return await _render_fair_page_with_flash(
                request,
                db,
                user,
                uuid.UUID(state.tx_id),
                step=n,
                message=_step3_flash_message(exc),
            )
        # Merge: update only this page's fieldsets, preserving the other half.
        merged = dict(state.sme_estimates)
        merged.update(page_rows)
        state.sme_estimates = merged
        if n == 4:
            # Milestone B (#loss-pert-overhaul): the step-4 catastrophic toggle.
            # Unchecked checkbox is absent from the form -> capped (the bounded
            # default). Only the step-4 full-form POST carries it; the HTMX
            # prefill/overlay endpoints mutate sme_estimates only and never
            # touch loss_shape.
            state.loss_shape = "catastrophic" if form.get("loss_catastrophic") else "capped"
    elif n == 5:
        # form.getlist() → list[str | UploadFile]; filter to str values only.
        state.mitigating_control_ids = [
            str(v) for v in form.getlist("control_ids") if isinstance(v, str)
        ]

    state.current_step = n + 1
    await wiz.advance_step(
        user_id=user.id,
        organization_id=user.organization_id,
        state=state,
    )
    await db.commit()
    return RedirectResponse(
        url=f"/scenarios/new/wizard/step/{n + 1}?tx={state.tx_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# T11: map wizard fieldset payload keys -> ScenarioForm column names (Arch-10 PR1).
# Step-3 form posts indexed fields like `tef_low_0` whose fieldset prefix
# (tef/vuln/pl/sl) does NOT match the ScenarioForm columns. The rename lives
# here as a tight constant so the route handler stays mechanical.
_PAYLOAD_TO_FORM = {
    "tef": "threat_event_frequency",
    "vuln": "vulnerability",
    "pl": "primary_loss",
    "sl": "secondary_loss",
}


def _str_or_none(value: Any) -> str | None:
    """Coerce a FormData value to a non-empty str, or None.

    Empty strings (which the wizard's hidden inputs emit when the field is
    not in use) collapse to None so Pydantic's XOR validator sees the
    "absent" semantics correctly.
    """
    if value is None:
        return None
    if not isinstance(value, str):  # pragma: no cover - defensive
        return None
    return value or None


def _parse_sme_rows_subset(
    form: Any, fieldsets: tuple[str, ...]
) -> dict[str, list[dict[str, Any]]]:
    """Parse indexed SME-row form fields (e.g. ``tef_low_0``) for the given
    fieldsets into the ``state.sme_estimates`` shape.

    Reused by the per-page step-3 (Likelihood: TEF+Vuln) / step-4 (Impact:
    PL+SL) POST handlers, which submit only their half. Strips comma grouping
    from money inputs before ``float()`` (PL/SL render "388,920.40" on blur per
    the UAT R3 fix). Does NOT construct WizardStep3Submit — callers validate via
    :func:`_validate_page_rows` as needed.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for fieldset in fieldsets:
        rows: list[dict[str, Any]] = []
        idx = 0
        while True:
            low_key = f"{fieldset}_low_{idx}"
            if low_key not in form:
                break
            sme_id_str = _str_or_none(form.get(f"{fieldset}_sme_id_{idx}"))
            sme_name_str = _str_or_none(form.get(f"{fieldset}_sme_name_{idx}"))
            low = float(str(form[low_key]).replace(",", ""))
            high = float(str(form[f"{fieldset}_high_{idx}"]).replace(",", ""))
            rows.append(
                {
                    "sme_id": sme_id_str or None,
                    "sme_name": sme_name_str or None,
                    "low": low,
                    "high": high,
                }
            )
            idx += 1
        out[fieldset] = rows
    return out


def _validate_page_rows(page_rows: dict[str, list[dict[str, Any]]]) -> None:
    """Validate a page's SME rows via the existing Pydantic fieldset models.

    Raises :class:`pydantic.ValidationError` on cap overflow, vuln high>1.0,
    low<=0, high<low, or sme_id/sme_name XOR violation — surfaced as a flash by
    the caller. Only validates the fieldsets present in ``page_rows``.
    """
    from idraa.schemas.wizard_step3 import FieldsetRows, VulnFieldsetRows

    for fs, rows in page_rows.items():
        if fs == "vuln":
            VulnFieldsetRows(rows=rows)  # type: ignore[arg-type]
        else:
            FieldsetRows(rows=rows)  # type: ignore[arg-type]


def _assert_finalizable(sme_estimates: dict[str, list[dict[str, Any]]]) -> None:
    """Defensively re-validate persisted SME rows before the finalize fit.

    2026-05-28 step-3 split (D6): finalize is state-sourced — SME rows come
    from ``state.sme_estimates`` (persisted by steps 3+4), not the POST body.
    Re-assemble a :class:`WizardStep3Submit` and let Pydantic enforce the
    per-fieldset caps, the vuln upper-bound, ``low > 0``, ``high >= low``, and
    the sme_id/sme_name XOR — so a hand-tampered draft surfaces a readable
    review-page flash rather than a raw 500 deeper in the pipeline.

    Plan-gate S-I1: ``WizardStep3Submit`` accepts EMPTY ``rows`` lists, so an
    empty draft (empty finalize body + a valid version_token) would otherwise
    pass shape validation and only blow up later in ``process_sme_estimates``
    as a raw-JSON 422. Assert each required fieldset (tef/vuln/pl per
    ``REQUIRED_FIELDSETS``) has >=1 row HERE so the empty-draft case routes to a
    readable review-page flash. ``sl`` is optional (dropped when empty).
    """
    from idraa.services.wizard_finalize import REQUIRED_FIELDSETS

    for fs in REQUIRED_FIELDSETS:  # ("tef", "vuln", "pl")
        if not sme_estimates.get(fs):
            raise ValueError(f"Need at least one SME estimate for {fs} before saving.")
    payload: dict[str, Any] = {
        "tef": {"rows": sme_estimates.get("tef", [])},
        "vuln": {"rows": sme_estimates.get("vuln", [])},
        "pl": {"rows": sme_estimates.get("pl", [])},
        "version_token": 0,  # placeholder; the real CAS token is read off the form
    }
    sl_rows = sme_estimates.get("sl") or []
    if sl_rows:
        payload["sl"] = {"rows": sl_rows}
    WizardStep3Submit(**payload)


# UAT R2 Bug E fix: human-readable flash message for a malformed finalize
# POST. Pydantic ValidationError carries a list of error dicts with ``loc``
# tuples like ``("tef", "rows", 1, "low")`` — we collapse those to a one-line
# summary so the operator sees "Primary loss row 1: low must be > 0" instead
# of a raw JSON dump. KeyError/ValueError get a generic surface — they would
# only arise from a hand-crafted POST anyway.
_FIELDSET_LABELS = {
    "tef": "Threat event frequency",
    "vuln": "Vulnerability",
    "pl": "Primary loss",
    "sl": "Secondary loss",
}


def _step3_flash_message(exc: Exception) -> str:
    """Format a step-3 parse failure into a banner-friendly message.

    Pydantic ``ValidationError`` errors carry ``loc`` and ``msg`` per
    issue; we lift the first few into a single line so the analyst gets
    an actionable hint without paging through a stack trace. KeyError /
    ValueError fall back to a generic "Please review your inputs" line
    (these only arise from hand-crafted POSTs in practice).
    """
    if isinstance(exc, PydanticValidationError):
        parts: list[str] = []
        for err in exc.errors()[:3]:  # cap at 3 to keep the flash banner short
            loc = err.get("loc", ())
            msg = err.get("msg", "invalid")
            # Common shape: ("tef", "rows", <idx>, "low") — convert to
            # "Threat event frequency row 1: low: <msg>". ``loc`` entries
            # are ``int | str``; isinstance narrows for the str-keyed
            # _FIELDSET_LABELS dict lookup so mypy is happy.
            if (
                len(loc) >= 4
                and isinstance(loc[0], str)
                and loc[0] in _FIELDSET_LABELS
                and loc[1] == "rows"
                and isinstance(loc[2], int)
            ):
                fs_label = _FIELDSET_LABELS[loc[0]]
                row_n = loc[2] + 1
                field = str(loc[3])
                parts.append(f"{fs_label} row {row_n} ({field}): {msg}")
            elif loc and isinstance(loc[0], str) and loc[0] in _FIELDSET_LABELS:
                parts.append(f"{_FIELDSET_LABELS[loc[0]]}: {msg}")
            else:
                parts.append(f"{'.'.join(str(x) for x in loc) or 'form'}: {msg}")
        more = "" if len(exc.errors()) <= 3 else f" (+{len(exc.errors()) - 3} more)"
        return "Please review your inputs — " + "; ".join(parts) + more
    if isinstance(exc, KeyError):
        return f"A required field is missing: {exc}. Please try again."
    return f"Invalid input: {exc}. Please try again."


async def _render_fair_page_with_flash(
    request: Request,
    db: AsyncSession,
    user: User,
    tx: uuid.UUID,
    *,
    step: int,
    message: str,
) -> HTMLResponse:
    """Re-render a FAIR-param page (step 3 Likelihood or step 4 Impact) with a
    flash banner at HTTP 422. Used by the per-page step-3/step-4 POST handlers
    when ``_validate_page_rows`` rejects the submitted SME rows.

    Rebuilds the same template-context the GET-side handler uses (via
    :func:`_fair_page_context`), drops a flash in, and returns 422 — the analyst
    lands back on the wizard with a readable error instead of a raw JSON dump.

    The persisted ``WizardState`` is read fresh from the DB (no
    ``with_for_update`` — read-only render path). In-flight (not-yet-persisted)
    edits from the failed POST are NOT preserved in the re-rendered rows; the
    template shows the last-persisted state. A rejected POST does NOT mutate
    ``state.sme_estimates`` (the merge happens only on validate-success), so the
    re-render reflects the prior good state.
    """
    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=tx,
    )
    organization = await db.get(Organization, user.organization_id)
    org_industry: str | None = None
    org_revenue_tier: str | None = None
    if organization is not None:
        cctx = calibration_context_from_org(organization)
        org_industry = cctx.industry
        org_revenue_tier = cctx.revenue_tier
    available_overlays = await OverlayRepo(db).list_active(
        organization_id=user.organization_id,
    )
    sme_dir = await sme_directory.list_for_dropdown(
        db,
        user.organization_id,
    )
    ctx_dict = _fair_page_context(
        request=request,
        user=user,
        state=state,
        step=step,
        org_industry=org_industry,
        org_revenue_tier=org_revenue_tier,
        available_overlays=available_overlays,
        sme_directory_for_dropdown=sme_dir,
    )
    ctx_dict["flash"] = build_flash(message, "error")
    template = (
        "scenarios/wizard/step_3_likelihood.html"
        if step == 3
        else "scenarios/wizard/step_4_impact.html"
    )
    return templates.TemplateResponse(
        request,
        template,
        ctx_dict,
        status_code=422,
    )


def _review_fair_rows(
    sme_estimates: dict[str, list[dict[str, Any]]],
    sme_dir: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build review-page display rows from persisted SME estimates.

    Returns one entry per fieldset: ``{label, fmt, rows:[{source, low, high}]}``.
    ``fmt`` ("rate" / "probability" / "money") drives the template's per-class
    number formatting. ``source`` resolves the row's ``sme_id`` against the SME
    directory dropdown list: ``is_system_owned`` rows render "Baseline"; FK rows
    render the SME name; free-text rows render the row's ``sme_name`` (fallback
    "SME").

    2026-05-28 step-3 split (D6): the evaluator-style wizard leaves the old
    PERT-distribution fields (``state.threat_event_frequency`` etc.) empty until
    finalize, so the review page must summarise ``state.sme_estimates`` instead.
    """
    by_id = {s["id"]: s for s in sme_dir}
    spec = [
        ("Threat event frequency", "tef", "rate"),
        ("Vulnerability", "vuln", "probability"),
        ("Primary loss", "pl", "money"),
        ("Secondary loss", "sl", "money"),
    ]
    out: list[dict[str, Any]] = []
    for label, key, fmt in spec:
        rows: list[dict[str, Any]] = []
        for r in sme_estimates.get(key, []):
            sid = r.get("sme_id")
            entry = by_id.get(sid) if sid else None
            if entry and entry.get("is_system_owned"):
                source = "Baseline"
            elif entry:
                source = entry["name"]
            else:
                source = r.get("sme_name") or "SME"
            rows.append({"source": source, "low": r["low"], "high": r["high"]})
        out.append({"label": label, "fmt": fmt, "rows": rows})
    return out


async def _render_review_with_flash(
    request: Request,
    db: AsyncSession,
    user: User,
    tx: uuid.UUID,
    *,
    message: str,
) -> HTMLResponse:
    """Re-render the step-6 review page with a flash banner at HTTP 422.

    2026-05-28 step-3 split (D6): finalize is state-sourced; a malformed /
    incomplete draft (e.g. an empty required fieldset) routes here instead of
    emitting FastAPI's raw 422 JSON dump. The operator lands back on the review
    page with a readable error.

    Plan-gate S-N1: the ``state`` is read FRESH from the DB so
    ``state.version_token`` reflects the current column value, and the review
    template emits BOTH ``csrf_field()`` (via the request-scoped global) and the
    ``version_token`` hidden input. Because ``_assert_finalizable`` runs BEFORE
    ``advance_step`` (the flash-rejected finalize never bumps the token), the
    re-rendered token is immediately re-submittable — no CSRF-403 / stale-409
    retry loop.
    """
    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=tx,
    )
    extra_ctx: dict[str, Any] = {}
    # Mirror the GET-side review context (n==6) so the template renders the
    # library banner + human-readable mitigating-control names identically.
    if state.library_entry_id:
        repo = ScenarioLibraryRepo(db)
        entry = await repo.get_by_id_version(
            uuid.UUID(state.library_entry_id),
            state.library_entry_version or 1,
        )
        extra_ctx["library_entry_name"] = entry.name if entry else "(deleted)"
    review_controls = await ControlRepo(db).list_for_org(user.organization_id)
    extra_ctx["control_name_by_id"] = {str(c.id): c.name for c in review_controls}
    # F7: the review template loops `review_fair_rows`; the flash path MUST supply
    # the same key as the n==6 GET context or the re-render crashes on an
    # undefined variable. Build it identically so both paths render the same.
    flash_sme_dir = await sme_directory.list_for_dropdown(db, user.organization_id)
    extra_ctx["review_fair_rows"] = _review_fair_rows(state.sme_estimates, flash_sme_dir)
    return templates.TemplateResponse(
        request,
        "scenarios/wizard/step_6_review.html",
        {
            "current_user": user,
            "flash": build_flash(message, "error"),
            "state": state,
            "step": 6,
            **extra_ctx,
        },
        status_code=422,
    )


@router.post("/scenarios/new/wizard/finalize")
async def finalize_wizard(
    request: Request,
    tx: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """T11 evaluator-style finalize: parse SME rows -> fit/pool/collapse -> Scenario.

    Sec-21 PR3: per-worker serialisation via ``_FINALIZE_SEMAPHORE`` so the
    synchronous scipy.optimize loop cannot saturate the worker when multiple
    analysts hit finalize concurrently.

    Sec-20 PR3: ``run_in_threadpool(process_sme_estimates, state)`` keeps the
    scipy loop off the event loop.

    Sec-4 PR1 + r2 BLOCKER 13: ``.with_for_update()`` on the wizard_drafts
    row (Postgres serialises concurrent finalize POSTs; SQLite no-op).

    Sec-18 PR2: ``version_token`` atomic CAS via
    ``WizardStateService.advance_step(expected_version_token=...)`` -> 409
    on conflict.

    Spec-E PR3: merge ``submit.{tef,vuln,pl,sl}.rows`` into
    ``state.sme_estimates`` BEFORE ``advance_step`` so analyst edits are
    processed (not the stale IRIS prefill).

    Arch-10 PR1: rename payload fieldset keys via ``_PAYLOAD_TO_FORM`` to
    match ScenarioForm column names.

    r2 BLOCKER 13 ordering: ``db.delete(draft)`` BEFORE ``db.commit()``,
    inside the FOR UPDATE-locked transaction.

    2026-05-28 step-3 split (D6): finalize is now STATE-SOURCED. Steps 3
    (Likelihood: TEF+Vuln) and 4 (Impact: PL+SL) each persist their half of
    the SME rows into ``state.sme_estimates`` via their per-page POSTs, so the
    review-page Save form posts ONLY ``_csrf`` + ``version_token`` — no SME
    rows in the body. The SME rows come from ``state.sme_estimates``; the
    optimistic-lock CAS token is read from the dedicated ``version_token``
    hidden field on the review form.

    UAT R2 Bug E fix (retained): a malformed / incomplete draft must surface a
    readable review-page flash, not FastAPI's raw 422 JSON dump
    (``{"detail":[{"type":"model_attributes_type",...}]}``).
    ``_assert_finalizable`` defensively re-validates the persisted rows BEFORE
    ``advance_step`` runs (Plan-gate A-I3: a flash-rejected finalize must not
    consume / bump the CAS token, so the same token is immediately
    re-submittable after the operator fixes the draft).
    """
    # version_token from the review-page Save form (the CAS source). The SME
    # rows are NO LONGER in the body — they were persisted by steps 3+4.
    review_form = await request.form()
    try:
        version_token = int(str(review_form["version_token"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, "Missing or invalid version_token") from exc

    async with _FINALIZE_SEMAPHORE:
        wizard_svc = WizardStateService(db)
        # Spec-4 PR1: FOR UPDATE row-lock per r2 BLOCKER 13.
        draft = (
            await db.execute(
                select(WizardDraft)
                .where(
                    WizardDraft.user_id == user.id,
                    WizardDraft.tx_id == tx,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if draft is None:
            raise HTTPException(404, "Wizard draft not found or expired")
        # r3 BLOCKER 7 — mid-wizard re-org / cookie reuse: cross-org draft
        # access is forbidden, clear the draft, return 403. Runs BEFORE any
        # state read so a cross-org draft leaks nothing into the response.
        if draft.organization_id != user.organization_id:
            await db.delete(draft)
            await db.commit()
            raise HTTPException(403, "Wizard org mismatch - restart wizard.")
        state = await wizard_svc.get_or_create(
            user_id=user.id,
            organization_id=user.organization_id,
            tx_id=tx,
        )
        # State-sourced (D6): SME rows were persisted by steps 3+4. Defensively
        # re-validate the full submit shape (and assert each required fieldset
        # is non-empty per Plan-gate S-I1) before the scipy fit so a malformed /
        # incomplete draft surfaces a readable review-page flash, not a 500 / raw
        # 422 JSON. Runs BEFORE advance_step so a rejected finalize does NOT bump
        # the CAS token (A-I3 — re-submittable after the operator fixes it).
        try:
            _assert_finalizable(state.sme_estimates)
        except (PydanticValidationError, ValueError) as exc:
            return await _render_review_with_flash(
                request, db, user, tx, message=_step3_flash_message(exc)
            )
        try:
            await wizard_svc.advance_step(
                user_id=user.id,
                organization_id=user.organization_id,
                state=state,
                expected_version_token=version_token,
            )
        except WizardDraftConflictError as exc:
            raise HTTPException(409, "Draft modified in another session; reload.") from exc
        # Sec-20 PR3: offload the scipy.optimize loop off the event loop.
        try:
            results = await run_in_threadpool(process_sme_estimates, state)
        except FinalizeBudgetExceededError as e:
            # Narrower subclass first; dispatch on class rather than sniffing
            # the aggregate_timeout flag on the parent (kept for back-compat).
            raise HTTPException(422, str(e)) from e
        except FinalizationError as e:
            raise HTTPException(422, detail={"field_errors": e.field_errors}) from e
        payload = build_scenario_payload(results, state)
        # Arch-10 PR1: rename payload keys -> ScenarioForm column names.
        form_kwargs = {_PAYLOAD_TO_FORM[fs]: payload[fs] for fs in payload}
        # T5: state.basic_fields() exposes step-2 fields (name, threat_*,
        # asset_class, attack_vector, library_entry_id) as a ScenarioForm-
        # splattable dict.
        # #56: a targeted draft (target_scenario_id set) finalizes into an
        # UPDATE of that scenario instead of a CREATE. The wizard never
        # collects status / effect / scenario_type / the descriptive version
        # label, so those are pulled from the live row and spliced onto the
        # form the wizard DID collect.
        is_reestimate = state.target_scenario_id is not None
        target: Scenario | None = None
        if is_reestimate:
            target = await ScenarioRepo(db).get_for_org(
                organization_id=user.organization_id,
                scenario_id=uuid.UUID(state.target_scenario_id),
            )
            if target is None:
                # Deleted while the wizard was in flight: keep the draft so
                # the operator can see their entered data, surface a flash.
                return await _render_review_with_flash(
                    request,
                    db,
                    user,
                    tx,
                    message="This scenario no longer exists — it was deleted "
                    "while you were estimating. Cancel to discard this draft.",
                )
            form = ScenarioForm(
                **form_kwargs,
                **state.basic_fields(),
                status=target.status,
                version=target.version,
                effect=getattr(target.effect, "value", target.effect),
                scenario_type=getattr(target.scenario_type, "value", target.scenario_type),
            )
        else:
            form = ScenarioForm(**form_kwargs, **state.basic_fields())
        # issue #27 Task 5 (routes/scenarios.py:2311-2314 fix): r.pooled is now
        # always a LognormMixture/NormMixture (T1), never a bare fit with a
        # scalar .meanlog/.sdlog/.mean/.sd attribute — the old
        # getattr(r.pooled, "meanlog", None) style silently returned None for
        # every fieldset, degrading the audit trail exactly when it matters
        # most (multi-SME pooling). pooling_component_fields is the SAME
        # helper build_scenario_payload uses for its sidecar, so the audit
        # summary and the stored sidecar report identical component shapes.
        summary = {
            fs: {
                "n_smes": len(r.rows),
                "weights": list(r.pooled.weights),
                **pooling_component_fields(r),
                "mode_boundary_clamped": r.mode_clamp_reason is not None,
            }
            for fs, r in results.items()
        }
        library_pin: dict[str, Any] | None = None
        if state.library_entry_id is not None:
            library_pin = {
                "entry_id": state.library_entry_id,
                "version": state.library_entry_version,
                "override_id": state.override_id,
                "override_version": state.override_version,
            }
        try:
            if is_reestimate:
                if state.target_expected_row_version is None:
                    # Impossible state (amendment 9 / Arch-N4 / Sec-N2): the
                    # seed function always captures row_version. Fail loud
                    # rather than silently coalescing to a value that could
                    # forge an optimistic-lock pass.
                    raise HTTPException(500, "re-estimate draft missing its row-version capture")
                scenario = await ScenarioService(db).update_from_wizard(
                    organization_id=user.organization_id,
                    scenario_id=uuid.UUID(state.target_scenario_id),
                    form=form,
                    expected_row_version=state.target_expected_row_version,
                    actor=user,
                    ip_address=client_ip(request),
                    per_fieldset_pooling_summary=summary,
                )
            else:
                scenario = await ScenarioService(db).create_from_wizard(
                    organization_id=user.organization_id,
                    form=form,
                    library_pin=library_pin,
                    actor=user,
                    ip_address=client_ip(request),
                    per_fieldset_pooling_summary=summary,
                )
        except (ScenarioVersionConflictError, NotFoundError) as exc:
            # amendment 5 / Spec-I2 + Arch-N2: the conflict path uses the
            # finalize-error 422 flash idiom (NOT 409 — 409 is reserved for
            # the version_token CAS above). Roll back first so advance_step's
            # token bump is unwound and the draft survives untouched.
            await db.rollback()
            message = (
                str(exc)
                if isinstance(exc, ScenarioVersionConflictError)
                else "This scenario no longer exists — it was deleted while "
                "you were estimating. Cancel to discard this draft."
            )  # Sec-R2-N1: never surface the raw NotFoundError message.
            return await _render_review_with_flash(request, db, user, tx, message=message)
        except ValidationError as exc:
            # FAIR-distribution validation (validate_fair_distributions, via
            # _stamp_new_scenario) rejects unstorable distributions: non-finite
            # tef/pl/sl params, an out-of-[0,1] vulnerability, or a lognormal
            # sigma outside (0, 10] (the Sec-I2 OOM/DoS storage guard). Surface a
            # readable review-page flash instead of letting it escape as a 500
            # (the regular form-create path already catches ValidationError -> 422;
            # this closes the same gap on the wizard-finalize path).
            #
            # advance_step (above) bumped the CAS version_token in this still-
            # uncommitted transaction; roll it back so a rejected finalize does
            # NOT consume the token (A-I3) — the same token stays re-submittable
            # after the operator narrows the offending range. (For the headline
            # FAIRCAMValidationError case the validator runs BEFORE any row write,
            # so the rollback's real job is unwinding the advance_step flush; it
            # also covers any ValidationError subclass that raises post-flush.)
            await db.rollback()
            return await _render_review_with_flash(
                request, db, user, tx, message=_step3_flash_message(exc)
            )
        if not is_reestimate:
            # Wizard authors in USD only (P2); native-currency entry is the
            # expert form's path. Explicit stamp (not just the column
            # default) so a future wizard change can't silently inherit a
            # non-USD value. Tracked follow-up: wizard native entry.
            # #56 amendment 15: the re-estimate path stamps USD INSIDE
            # update_from_wizard instead, so a non-USD scenario's currency
            # flip lands in that call's audit diff — stamping here too would
            # double-stamp with no audit trail for the flip.
            scenario.entry_currency = "USD"
            scenario.entry_rate = None
        # UAT 2026-05-21 carryover: persist the mitigating controls picked
        # in wizard step 4 alongside the new evaluator-style finalize.
        if is_reestimate:
            # #56 amendment 2 / Arch-I1: unconditional (an empty selection
            # must clear existing links) AND scoped to the ACTIVE set the
            # step-5 picker actually rendered, mirroring the #217 edit-path
            # fix — links to DRAFT/DEPRECATED controls the picker never
            # showed a checkbox for survive re-estimation.
            mitigating_uuids = [uuid.UUID(s) for s in state.mitigating_control_ids]
            eligible_control_ids = {
                c.id for c in await ControlRepo(db).list_for_org(user.organization_id)
            }
            await ScenarioRepo(db).set_mitigating_controls(
                scenario_id=scenario.id,
                organization_id=user.organization_id,
                control_ids=mitigating_uuids,
                eligible_control_ids=eligible_control_ids,
            )
        elif state.mitigating_control_ids:
            mitigating_uuids = [uuid.UUID(s) for s in state.mitigating_control_ids]
            await ScenarioRepo(db).set_mitigating_controls(
                scenario_id=scenario.id,
                organization_id=user.organization_id,
                control_ids=mitigating_uuids,
            )
        # Issue #475: copy the pinned library entry's curated ATT&CK technique
        # mappings onto the new scenario (copy-on-clone, same convention as
        # distributions — the org rows are independent of the canonical layer).
        # #56: re-estimation never copies ATT&CK mappings (existing mappings
        # on the target are untouched by design). seed_wizard_state_from_scenario
        # never sets library fields, so library_pin is always None on this
        # path in practice — the is_reestimate guard is defensive.
        if library_pin is not None and not is_reestimate:
            await copy_library_attack_mappings(
                db,
                scenario_id=scenario.id,
                organization_id=user.organization_id,
                entry_id=uuid.UUID(str(library_pin["entry_id"])),
                entry_version=int(library_pin.get("version") or 1),
            )
        if is_reestimate:
            # #56: SME rows are replace-all on re-estimation — the target's
            # prior estimates no longer reflect the re-elicited values. Scoped
            # to this org (defense in depth; scenario is already org-checked).
            await db.execute(
                delete(ScenarioSMEEstimate).where(
                    ScenarioSMEEstimate.scenario_id == scenario.id,
                    ScenarioSMEEstimate.organization_id == user.organization_id,
                )
            )
        await persist_estimates(
            db,
            scenario.id,
            results=results,
            actor_id=user.id,
            organization_id=user.organization_id,
        )
        # r2 BLOCKER 13 ordering: delete BEFORE commit, inside the FOR UPDATE.
        await db.delete(draft)
        await db.commit()
        return RedirectResponse(
            url=f"/scenarios/{scenario.id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/scenarios/new/wizard/cancel")
async def cancel_wizard(
    request: Request,
    tx: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    # r3 MAJOR (architect #11) — short-circuit when tx is None: there's no
    # draft to cancel, so don't materialise a new one just to delete it.
    if tx is None:
        return RedirectResponse(url="/scenarios", status_code=status.HTTP_303_SEE_OTHER)

    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=uuid.UUID(tx),
    )
    await wiz.clear(user_id=user.id, tx_id=uuid.UUID(state.tx_id))
    await db.commit()
    return RedirectResponse(url="/scenarios", status_code=status.HTTP_303_SEE_OTHER)


def _fair_page_context(
    request: Request,
    user: User,
    state: WizardState,
    step: int,
    org_industry: str | None,
    org_revenue_tier: str | None,
    available_overlays: list[Any],
    sme_directory_for_dropdown: list[dict[str, Any]],
) -> dict[str, Any]:
    """Context for a split FAIR-param page (step 3 Likelihood / step 4 Impact)
    and its HTMX swap fragment (``_fair_params_form_inner.html``).

    Scopes fieldsets to the page, filters no-op overlays (D4), and gates the
    calibration/override banner to the Impact page (PL/SL is the only calibrated
    half). The GET handler, both HTMX endpoints, and the flash re-render path
    ALL build context here so the partial renders identically regardless of
    swap source (Sec-25 PR2 single-source guard — omitting e.g. ``csrf_token``
    after an outerHTML swap would break the next POST).

    Note: ``request`` is NOT returned in the dict. The caller passes ``request``
    as the first positional arg to ``templates.TemplateResponse`` so the
    project's ``_csrf_token_context_processor`` injects ``csrf_token``; we also
    pass it explicitly as belt-and-suspenders against context-processor
    regressions.
    """
    page = "likelihood" if step == 3 else "impact"
    fieldsets_on_page = LIKELIHOOD_FIELDSETS if page == "likelihood" else IMPACT_FIELDSETS
    fieldset_keys = [k for k, _ in fieldsets_on_page]
    # No-op overlay filtering (D4): only overlays that materially affect THIS
    # page's fieldsets. Likelihood scales TEF by frequency_multiplier; Impact
    # scales PL/SL by magnitude_multiplier. An overlay whose relevant multiplier
    # is exactly 1.0 would be a no-op button, so hide it.
    if page == "likelihood":
        overlays = [o for o in available_overlays if o.frequency_multiplier != 1.0]
    else:
        overlays = [o for o in available_overlays if o.magnitude_multiplier != 1.0]
    # initial_rows is scoped to this page's fieldsets only — the partial's
    # ``initial_rows[fieldset_key]`` lookup only iterates fieldsets_on_page.
    # PR #247 UAT bug: round low/high values for clean Alpine x-model display.
    initial_rows = _round_initial_rows_for_display(
        {fs: state.sme_estimates.get(fs, []) for fs in fieldset_keys}
    )
    return {
        "current_user": user,
        "flash": None,
        "state": state,
        "step": step,
        "page": page,
        "fieldsets_on_page": fieldsets_on_page,
        "fieldset_tooltips": QUESTION_TOOLTIPS,
        "csrf_token": _csrf_token_from_request(request),
        "org_industry": org_industry,
        "org_revenue_tier": org_revenue_tier,
        "rendered_questions": _build_rendered_questions(state),
        "initial_rows": initial_rows,
        "sme_directory_for_dropdown": sme_directory_for_dropdown,
        "available_overlays": overlays,
        # The PL/SL info note lives INSIDE the HTMX swap target. It applies only
        # to LIBRARY-DERIVED scenarios (whose PL/SL are the curated sector loss);
        # a from-scratch scenario must show no note. Gated to the Impact page AND
        # library_entry_id present.
        "show_calibration_banner": page == "impact" and state.library_entry_id is not None,
        "override_active": state.override_id is not None,
    }


def _validate_page(page: str) -> str:
    """Plan-gate S-I2: guard the HTMX ``page`` form param to the allowlist.

    Anything outside {likelihood, impact} is a malformed/hand-crafted POST and
    must 422 rather than silently scope to "impact" and mangle the wrong
    fieldset half.
    """
    if page not in ("likelihood", "impact"):
        raise HTTPException(status_code=422, detail="invalid page")
    return page


@router.post("/scenarios/wizard/prefill-from-industry", response_class=HTMLResponse)
async def wizard_prefill_from_industry(
    request: Request,
    tx: uuid.UUID = Form(...),
    page: str = Form("likelihood"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> HTMLResponse:
    """Reset a FAIR page's SME-estimate rows to the IRIS industry baseline.

    POST (not GET) because this mutates ``wizard_drafts.state_json`` via
    ``WizardStateService.advance_step`` — CSRF middleware gates POST. HTMX
    swaps the rendered ``_fair_params_form_inner.html`` fragment outerHTML into
    the current page's form region.

    Page-scoped (2026-05-28 split): ``page`` selects which fieldsets reset —
    Likelihood resets TEF+Vuln, Impact resets PL+SL — merged so the other half
    is preserved. Each reset writes a single per-fieldset ``(low, high)`` row
    attributed to the per-org IRIS system-owned SME (lazy-created via
    ``sme_directory.get_or_create_iris_sme``).
    """
    _validate_page(page)  # Plan-gate S-I2
    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=tx,
    )
    # Issue #88: org-derived ctx — always reflects current tier, not stale snapshot.
    organization = await db.get(Organization, user.organization_id)
    org_industry: str | None = None
    org_revenue_tier: str | None = None
    iris_form: dict[str, dict[str, float] | None] | None = None
    if organization is not None:
        ctx = calibration_context_from_org(organization)
        org_industry = ctx.industry
        org_revenue_tier = ctx.revenue_tier
        iris_form = iris_baseline_for_form_v2(ctx)
    if iris_form is not None:
        iris_sme, _ = await sme_directory.get_or_create_iris_sme(
            db,
            user.organization_id,
        )
        # Page-scoped REPLACE: only this page's fieldsets reset to IRIS; the
        # other half stays. _iris_seed_rows omits fieldsets whose IRIS baseline
        # is None, so a page with no SL baseline does NOT get a fabricated sl
        # key (plan-gate A-N2).
        seeded = _iris_seed_rows(iris_form, str(iris_sme.id))
        page_fieldsets = ("tef", "vuln") if page == "likelihood" else ("pl", "sl")
        merged = dict(state.sme_estimates)
        merged.update({fs: seeded[fs] for fs in page_fieldsets if fs in seeded})
        state.sme_estimates = merged
        await wiz.advance_step(
            user_id=user.id,
            organization_id=user.organization_id,
            state=state,
        )
        await db.commit()
    available_overlays = await OverlayRepo(db).list_active(
        organization_id=user.organization_id,
    )
    sme_dir = await sme_directory.list_for_dropdown(
        db,
        user.organization_id,
    )
    # Render the form-inner PARTIAL (not the page-extending shell): the HTMX
    # swap target is ``#fair-params-inner`` with ``outerHTML``. _fair_page_context
    # scopes the fieldsets to the page being reset.
    return templates.TemplateResponse(
        request,
        "scenarios/wizard/_fair_params_form_inner.html",
        _fair_page_context(
            request=request,
            user=user,
            state=state,
            step=3 if page == "likelihood" else 4,
            org_industry=org_industry,
            org_revenue_tier=org_revenue_tier,
            available_overlays=available_overlays,
            sme_directory_for_dropdown=sme_dir,
        ),
    )


@router.post("/scenarios/wizard/apply-overlay", response_class=HTMLResponse)
async def wizard_apply_overlay(
    request: Request,
    tx: uuid.UUID = Form(...),
    overlay_id: uuid.UUID = Form(...),
    page: str = Form("likelihood"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> HTMLResponse:
    """Apply overlay multipliers to a FAIR page's SME-estimate rows.

    POST (not GET): mutates ``wizard_drafts.state_json`` via
    ``WizardStateService.advance_step`` — CSRF middleware gates POST.

    Returns 404 if the overlay does not belong to the user's org OR is
    inactive (soft-deleted). The 404 closes the existence oracle for
    cross-org overlay UUIDs (B9/B10 pattern from ``OverlayRepo.get_for_org``);
    the explicit ``is_active`` check defends against soft-deleted overlays
    leaking through after a UUID was previously surfaced to the analyst.

    Page-scoped (2026-05-28 split): ``page`` selects which fieldsets are scaled
    and re-rendered — Likelihood scales TEF (freq_mult), Impact scales PL/SL
    (mag_mult); VULN rows pass through unchanged (vulnerability is a probability
    per the FAIR Standard). Only the page's half is scaled, merged so the other
    half is preserved. Reads current rows from persisted ``state.sme_estimates``
    rather than the inbound POST body.
    """
    overlay = await OverlayRepo(db).get_for_org(
        overlay_id=overlay_id,
        organization_id=user.organization_id,
    )
    if overlay is None or not overlay.is_active:
        raise HTTPException(status_code=404, detail="Overlay not found or inactive")

    _validate_page(page)  # Plan-gate S-I2 (after the overlay 404 check)
    page_fieldsets = ("tef", "vuln") if page == "likelihood" else ("pl", "sl")

    wiz = WizardStateService(db)
    state = await wiz.get_or_create(
        user_id=user.id,
        organization_id=user.organization_id,
        tx_id=tx,
    )
    # Scale only this page's fieldsets; merge to preserve the other half.
    subset = {fs: state.sme_estimates.get(fs, []) for fs in page_fieldsets}
    scaled = apply_overlay_multipliers(
        subset,
        overlay_freq_mult=overlay.frequency_multiplier,
        overlay_mag_mult=overlay.magnitude_multiplier,
    )
    merged = dict(state.sme_estimates)
    merged.update(scaled)
    state.sme_estimates = merged
    await wiz.advance_step(
        user_id=user.id,
        organization_id=user.organization_id,
        state=state,
    )
    await db.commit()

    # Re-fetch org chip values for the swap; render the page-scoped partial.
    organization = await db.get(Organization, user.organization_id)
    org_industry: str | None = None
    org_revenue_tier: str | None = None
    if organization is not None:
        ctx = calibration_context_from_org(organization)
        org_industry = ctx.industry
        org_revenue_tier = ctx.revenue_tier
    available_overlays = await OverlayRepo(db).list_active(
        organization_id=user.organization_id,
    )
    sme_dir = await sme_directory.list_for_dropdown(
        db,
        user.organization_id,
    )
    # Render the form-inner PARTIAL (not the page-extending shell): the HTMX
    # swap target is ``#fair-params-inner`` with ``outerHTML``.
    return templates.TemplateResponse(
        request,
        "scenarios/wizard/_fair_params_form_inner.html",
        _fair_page_context(
            request=request,
            user=user,
            state=state,
            step=3 if page == "likelihood" else 4,
            org_industry=org_industry,
            org_revenue_tier=org_revenue_tier,
            available_overlays=available_overlays,
            sme_directory_for_dropdown=sme_dir,
        ),
    )
