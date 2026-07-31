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
import logging
import re
import uuid
from typing import Any, Literal

from fair_cam.quantile_pooling import (
    lognormal_from_quantiles,
    lognormal_quantiles,
)
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
    FAIRCAMValidationError,
    LibraryEntryNotFoundError,
    LibraryEntryStatusError,
    NotFoundError,
    RunBusyError,
    ScenarioInUseError,
    ValidationError,
)
from idraa.models.enums import (
    AssetClass,
    EntityStatus,
    StepUpCategory,
    ThreatActorType,
    UserRole,
)
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
    require_step_up,
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
from idraa.routes.scenario_loss_pin import (
    _SIGMA_TOL,
    _cap_remint_disclosure,
    _expert_loss_readout_cfgs,
    _loss_sigma_display,
    _loss_stale_wide,
    _loss_was_recalibrated,
    _max_tripwire_sigma,
    _parse_pin_quantile,
    _pin_panel_context,
)
from idraa.routes.scenario_wizard_seeding import (
    _iris_seed_rows,
    _library_seed_rows,
    _seed_state_from_library_entry,
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
from idraa.services.audit import AuditWriter
from idraa.services.calibration import (
    SIGMA_WARN_THRESHOLD,
    WITHIN_SCENARIO_SIGMA_DEFAULT,
    calibration_context_from_org,
)
from idraa.services.capacity_bound_copy import (
    D17_HINT_REVENUE_UNSET,
    D18_REVENUE_MESSAGE,
    D19_FLOOR_MARKER,
    d17_capacity_hint_revenue_set,
    wrap_d19_floor_message,
)
from idraa.services.flash import build_flash
from idraa.services.fx_rates import FxRateService, is_selectable_currency
from idraa.services.loss_capacity import capacity_max_for_org
from idraa.services.loss_pinning import (
    pin_loss,
    preview_loss_refresh,
    refresh_loss_from_library,
    unpin_loss,
)
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
    _dedup_latest_per_sme,
    build_scenario_payload,
    persist_estimates,
    pooling_component_fields,
    process_sme_estimates,
)
from idraa.services.wizard_helpers import (
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

logger = logging.getLogger(__name__)

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
    draft_expired: int | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Drafts-surfaced T4b (DQ-14): set to 1 by get_wizard_step's "
            "dead-tx redirect; rendered as a 'warning' banner here so the "
            "friendly copy cannot silently no-op."
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
    # Issue #167: post-delete flash. Drafts-surfaced T4b (DQ-14): a second,
    # mutually-exclusive flash flag for get_wizard_step's dead-tx redirect —
    # mirrors the ?deleted=1 mechanics one-for-one.
    if deleted == 1:
        flash = build_flash("Deleted scenario.", "success")
    elif draft_expired == 1:
        flash = build_flash(
            "That draft no longer exists — it may have been discarded or expired.",
            "warning",
        )
    else:
        flash = None

    # Drafts-surfaced T3 (spec §1): current user's in-progress wizard drafts,
    # org-scoped (DA-2) + user-scoped, newest-first, NO SQL limit (DA-9 — a
    # limit-then-filter window would let a burst of step-1 ghosts evict a
    # real draft from view before the step-1 filter below runs). Per-user
    # org-scoped rows are TTL-bounded, so the unbounded fetch is
    # production-scale safe.
    draft_rows = (
        (
            await db.execute(
                select(WizardDraft)
                .where(
                    WizardDraft.user_id == user.id,
                    WizardDraft.organization_id == user.organization_id,
                )
                .order_by(WizardDraft.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    wizard_drafts: list[dict[str, Any]] = []
    for d in draft_rows:
        sj = d.state_json or {}
        # DA-1: never-advanced (mint-on-GET) drafts are re-creatable noise —
        # excluded from the strip entirely.
        current_step = int(sj.get("current_step", 1))
        if current_step < 2:
            continue
        wizard_drafts.append(
            {
                "tx_id": str(d.tx_id),
                # DQ-8: name/current_step/target_scenario_id from state_json;
                # tx_id/updated_at from the ORM row.
                "name": sj.get("name") or "New scenario",
                "step": min(current_step, 6),  # upper-clamp only (DQ-1/DA-7)
                "reestimating": bool(sj.get("target_scenario_id")),
                "updated_at": d.updated_at,
            }
        )
        if len(wizard_drafts) >= 20:  # cap the MAPPED list at 20 for display
            break

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
            "wizard_drafts": wizard_drafts,
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
    # PR2 D17 (Task 4c): the mint PREVIEW only — never a submitted value. The
    # pl_max/sl_max fields render BLANK below (form_defaults()); this is
    # guidance text shown beside them. db.get(Organization, ...) above is the
    # TARGET org lookup — never get_sole_org/require_sole_org.
    capacity_max = (
        capacity_max_for_org(organization.annual_revenue, get_settings().capacity_k)
        if organization is not None
        else None
    )
    capacity_hint = (
        d17_capacity_hint_revenue_set(capacity_max)
        if capacity_max is not None
        else D17_HINT_REVENUE_UNSET
    )
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
            "capacity_max": capacity_max,
            "capacity_hint": capacity_hint,
            "readout_cfg": _expert_loss_readout_cfgs(defaults, capacity_max),
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
        # PR2 D17 (Task 4c): resolve the per-loss-component capacity ceiling
        # from the TARGET org's OWN revenue (create_org, fetched via db.get
        # above — never get_sole_org/require_sole_org). A blank pl_max/sl_max
        # mints this value; a typed value is bound ABOVE by it (D13). None
        # when the org's revenue is unset/non-positive (D14) — a typed cap is
        # then accepted with no ceiling (the D18 escape hatch for this org).
        capacity_max = (
            capacity_max_for_org(create_org.annual_revenue, get_settings().capacity_k)
            if create_org is not None
            else None
        )
        form = parse_scenario_form(raw, capacity_max=capacity_max)
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
        #
        # PR2 D19 (Task 4c): a minted/typed capacity `max` at or below the
        # distribution's p95 surfaces here as a FAIRCAMValidationError
        # carrying the floor marker (Task 3b's _validate_capacity_floor,
        # raised inside _stamp_new_scenario's validate_fair_distributions
        # call) — wrap its FACTUAL p95-vs-cap string with the three operator
        # remedies instead of the raw message (mirrors the wizard finalize
        # handling at post_wizard_step's sibling finalize_wizard).
        message = (
            wrap_d19_floor_message(exc)
            if isinstance(exc, FAIRCAMValidationError) and D19_FLOOR_MARKER in str(exc)
            else str(exc)
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
            errors=[message],
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


# ---- attack coverage / mapping partial --------------------------------
# (bulk + single-scenario export moved to routes/scenario_export_routes.py
# under issue #119 — see app.py's include_router ordering comment for the
# same B5 declaration-order precedent that governed their position here.)


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


async def _view_scenario_context(
    db: AsyncSession, user: User, scenario_id: uuid.UUID
) -> dict[str, Any] | None:
    """Shared context builder for ``scenarios/view.html`` -- everything
    EXCEPT ``flash`` (the one field that legitimately differs between a
    plain GET, which derives it from query flags, and
    ``_render_view_action_failure`` below, which sets it to a failure
    message). Mirrors the ``_edit_form_context``/``_load_scenario_for_edit``
    split (T3.a, SPEC B-1) one level up: a pin/refresh failure re-renders
    the SAME page instead of surfacing a raw ``HTTPException`` JSON body.

    Org-scoped lookup. Returns ``None`` on a missing/wrong-org scenario id
    (NOT 403 — no existence oracle, B9/B10 paranoid-review precedent) so
    callers can raise 404 themselves.
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
        return None

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

    # Drafts-surfaced T4 (spec §2, DA-5): newest current-user draft targeting
    # THIS scenario, queried by target in SQL (never "the 20 newest,
    # filtered" — that would inherit the strip's display cap and could miss
    # a real targeting draft beyond it).
    reestimate_draft: dict[str, Any] | None = None
    draft_row = (
        await db.execute(
            select(WizardDraft)
            .where(
                WizardDraft.user_id == user.id,
                WizardDraft.organization_id == user.organization_id,
                WizardDraft.state_json["target_scenario_id"].as_string() == scenario.id.hex,
            )
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if draft_row is not None:
        draft_sj = draft_row.state_json or {}
        draft_step = int(draft_sj.get("current_step", 1))
        if draft_step >= 2:  # DA-1: same never-advanced filter as the strip
            reestimate_draft = {
                "tx_id": str(draft_row.tx_id),
                "name": draft_sj.get("name") or "New scenario",
                "step": min(draft_step, 6),
                "reestimating": True,
                "updated_at": draft_row.updated_at,
            }

    return {
        "current_user": user,
        "scenario": scenario,
        "recommendations": recommendations,
        "can_adopt": user.role in (UserRole.ADMIN, UserRole.ANALYST),
        "reestimate_draft": reestimate_draft,
        "loss_recalibrated": _loss_was_recalibrated(scenario),
        # PR3 T4 (D23): standing stale-copy tripwire -- unconditional on
        # EVERY GET (unlike ?loss_wide=1's one-shot redirect flash below),
        # since a wide, unstamped field should keep surfacing until the
        # analyst pins/refreshes/re-authors it, not just on the render right
        # after finalize.
        "loss_stale_wide": _loss_stale_wide(scenario),
        # T4.a gate fix (METH I-2): a top-level context key so the
        # stale-wide banner never reads `pin_panels.pl.sigma_default` --
        # that key is ABSENT on `pin_panels["pl"]` whenever primary_loss is
        # non-dict (None, or the literal JSON text "null"), which raised a
        # Jinja UndefinedError (500) on a wide, non-dict-PL scenario
        # (executed). This key is present unconditionally.
        "sigma_default": WITHIN_SCENARIO_SIGMA_DEFAULT,
        # PR3 T3: pin-state chip + unpin button per field. capacity_max
        # is None here — the view page shows no readout mount, only the
        # chip/unpin affordance, so _pin_panel_context's readout_cfg
        # field goes unused (reusing the edit-form helper rather than
        # hand-duplicating the pin-detection walk).
        "pin_panels": _pin_panel_context(scenario, None),
    }


async def _render_view_action_failure(
    request: Request,
    db: AsyncSession,
    user: User,
    scenario_id: uuid.UUID,
    *,
    message: str,
    status_code: int,
) -> HTMLResponse:
    """PR3 T4: render the scenario VIEW page with an alert flash on a
    refresh failure, instead of letting a raw ``HTTPException`` propagate as
    a JSON ``{"detail": ...}`` body -- the SAME idiom as T3.a's
    ``_render_loss_action_failure``, retargeted at ``view.html`` because
    refresh is triggered from the view page's tripwire banner, not the edit
    form (pin/unpin's failure target). A missing/wrong-org scenario still
    raises a plain 404 (no page to re-render for a scenario that doesn't
    exist for this org).
    """
    ctx = await _view_scenario_context(db, user, scenario_id)
    if ctx is None:
        raise HTTPException(status_code=404)
    ctx["flash"] = build_flash(message, "error")
    return templates.TemplateResponse(request, "scenarios/view.html", ctx, status_code=status_code)


@router.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
async def view_scenario(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    loss_wide: int = Query(
        default=0,
        ge=0,
        le=1,
        description=(
            "Finalize-advisory flash flag (Task 5, plan "
            "2026-07-25-sigma-recal-pr1): set to 1 by the wizard finalize "
            "POST's redirect when a stored PL/SL sigma exceeds the "
            "within-scenario default. Mirrors the ?deleted=1 mechanics "
            "(routes/scenarios.py:220-226) -- the value itself is NOT "
            "smuggled through the URL; this handler re-derives it from the "
            "scenario's own stored dicts."
        ),
    ),
    pinned: int = Query(
        default=0,
        ge=0,
        le=1,
        description=(
            "PR3 T3 (D20/D21): analyst-pin POST redirect flash flag. "
            "Mirrors the ?deleted=1 mechanics (routes/scenarios.py:220-226) "
            "-- generic confirmation text only, no field name or sigma "
            "value smuggled through the URL (the pin route intentionally "
            "does not know which field a caller will read this as)."
        ),
    ),
    unpinned: int = Query(
        default=0,
        ge=0,
        le=1,
        description="PR3 T3: analyst-unpin POST redirect flash flag. See ``pinned`` above.",
    ),
    loss_refreshed: int = Query(
        default=0,
        ge=0,
        le=1,
        description="PR3 T4 (D23): library-refresh POST redirect flash flag. See ``pinned`` above.",
    ),
) -> HTMLResponse:
    """Render the scenario detail page.

    Org-scoped lookup. Cross-org IDs return None → 404 (NOT 403) so we
    don't leak existence of scenarios owned by other orgs (B9/B10
    paranoid-review precedent). Eager-loads mitigating_controls for the
    detail card (#68 UAT — view page must show configured controls
    without forcing operator into edit mode).
    """
    ctx = await _view_scenario_context(db, user, scenario_id)
    if ctx is None:
        raise HTTPException(status_code=404)
    scenario = ctx["scenario"]

    # Task 5 finalize advisory (plan 2026-07-25-sigma-recal-pr1): re-derive
    # the sigma reading from the scenario's own live dicts -- the
    # ?loss_wide=1 flag carries no value, only a "check" instruction, so a
    # scenario later edited narrower does not keep flashing a stale figure.
    # Methodology re-gate finding: re-derive the CONDITION too, not just the
    # value -- revisiting ?loss_wide=1 (back-button/bookmark) on a scenario
    # since narrowed to sigma <= the default must NOT render a false,
    # self-contradictory advisory. Mirrors the redirect's own gate exactly.
    flash = None
    if loss_wide == 1:
        # T4.b (confirmation-gate I-2): FIRING basis, not display max — a
        # matched-default divergent mixture must not flash "wider than the
        # default" here while the standing banner correctly stays quiet.
        wide_sigma = _max_tripwire_sigma(scenario)
        # T4.a gate fix (METH I-1): toleranced like every other sigma-vs-
        # default comparison in the codebase (_SIGMA_TOL = 1e-5, imported
        # from scenario_loss_pin) -- a stored
        # component sigma of exactly 1.7 that drifts to
        # 1.7000000000000004 via dollar round-trips must NOT flash "wider
        # than the default" here, mirroring _loss_stale_wide's own guard.
        if wide_sigma is not None and wide_sigma > WITHIN_SCENARIO_SIGMA_DEFAULT + _SIGMA_TOL:
            flash = build_flash(
                f"This scenario's loss dispersion (sigma={wide_sigma:.2f}) is "
                f"wider than the within-scenario default "
                f"({WITHIN_SCENARIO_SIGMA_DEFAULT:g}) — see the calibration "
                "reference.",
                "warning",
            )
    # PR3 T3/T4: pin/unpin/refresh success flashes. Generic confirmation
    # text only — no field name or sigma value is smuggled through the query
    # string (same "flag, not a value" idiom as ?deleted=1 above; there is
    # nothing per-field to re-derive here since the text names no field or
    # number).
    if flash is None and pinned == 1:
        flash = build_flash("Loss dispersion pinned for this field.", "success")
    elif flash is None and unpinned == 1:
        flash = build_flash("Loss dispersion unpinned for this field.", "success")
    elif flash is None and loss_refreshed == 1:
        flash = build_flash("Loss dispersion refreshed from the library entry.", "success")

    ctx["flash"] = flash
    return templates.TemplateResponse(request, "scenarios/view.html", ctx)


async def _edit_form_context(db: AsyncSession, user: User, scenario: Scenario) -> dict[str, Any]:
    """Shared context builder for ``scenarios/form.html`` in EDIT mode.

    T3.a gate fix (SPEC B-1): factored out of ``edit_scenario_form`` so a
    pin/unpin failure can re-render the SAME page (via
    ``_render_loss_action_failure`` below) instead of surfacing a raw
    ``HTTPException`` JSON body that wipes the analyst's in-flight pin-panel
    input on the hx-boost 4xx force-swap. ``scenario`` must already have
    ``mitigating_controls`` eager-loaded (``selectinload`` — both call sites
    load it the same way). Callers set ``"flash"`` themselves (the one field
    that legitimately differs between a plain GET and a failure re-render).
    """
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
    # PR3 T3 (disclosed side effect, plan Step 5(b)): capacity_max now
    # reaches the edit context too, mirroring new_scenario_form:340-344 —
    # closes a pre-existing gap (the D17 capacity hint previously rendered
    # on create only) and feeds both the pin panel's readout mount and the
    # live form readout mounts' cap line.
    capacity_max = (
        capacity_max_for_org(organization.annual_revenue, get_settings().capacity_k)
        if organization is not None
        else None
    )
    capacity_hint = (
        d17_capacity_hint_revenue_set(capacity_max)
        if capacity_max is not None
        else D17_HINT_REVENUE_UNSET
    )
    form = form_from_scenario(scenario)
    return {
        "current_user": user,
        "scenario": scenario,
        "form": form,
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
        "capacity_max": capacity_max,
        "capacity_hint": capacity_hint,
        "readout_cfg": _expert_loss_readout_cfgs(form, capacity_max),
        "pin_panels": _pin_panel_context(scenario, capacity_max),
    }


async def _load_scenario_for_edit(
    db: AsyncSession, user: User, scenario_id: uuid.UUID
) -> Scenario | None:
    """Org-scoped scenario lookup with ``mitigating_controls`` eager-loaded —
    the exact query both ``edit_scenario_form`` and
    ``_render_loss_action_failure`` need before building ``_edit_form_context``.
    """
    edit_stmt = (
        select(Scenario)
        .where(
            Scenario.id == scenario_id,
            Scenario.organization_id == user.organization_id,
        )
        .options(selectinload(Scenario.mitigating_controls))
    )
    return (await db.execute(edit_stmt)).scalar_one_or_none()


async def _render_loss_action_failure(
    request: Request,
    db: AsyncSession,
    user: User,
    scenario_id: uuid.UUID,
    *,
    field: Literal["primary", "secondary"],
    message: str,
    status_code: int,
    submitted_p50: str | None = None,
    submitted_p95: str | None = None,
) -> HTMLResponse:
    """T3.a gate fix (SPEC B-1): render the EDIT form with an alert banner
    on a pin/unpin failure, instead of letting a raw ``HTTPException``
    propagate as a JSON ``{"detail": ...}`` body.

    base.html's hx-boost 4xx force-swap replaces the ENTIRE page with that
    JSON body on any non-2xx response — the analyst's whole in-flight edit
    (technique rows, mitigating-control checkboxes, unsaved field tweaks)
    was lost for a failure that touches only the pin panel. Mirrors
    ``update_scenario``'s own ConflictError/ValidationError re-render idiom
    (``render_scenario_form``) — same "re-render the page instead of
    raising" shape, applied here to the edit-form context builder above
    since the pin panel only exists in edit mode.

    The FAILED field's own pin-panel p50/p95 inputs are overridden with the
    analyst's just-submitted raw strings (cheap to preserve — they are
    already FastAPI Form params in hand) when supplied; unpin failures pass
    neither (there is nothing to preserve). No error text is smuggled
    through the query string (the value-smuggling bar) — the message is
    rendered directly into this response's flash.

    A missing/wrong-org scenario still raises a plain 404 ``HTTPException``
    (SPEC B-1's scope is 422/409 form-state loss; there is no edit-form page
    to re-render for a scenario that doesn't exist for this org).
    """
    scenario = await _load_scenario_for_edit(db, user, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404)
    ctx = await _edit_form_context(db, user, scenario)
    field_key = "pl" if field == "primary" else "sl"
    panel = ctx["pin_panels"].get(field_key)
    if panel is not None:
        if submitted_p50 is not None:
            panel["prefill_p50"] = submitted_p50
        if submitted_p95 is not None:
            panel["prefill_p95"] = submitted_p95
    ctx["flash"] = build_flash(message, "error")
    return templates.TemplateResponse(request, "scenarios/form.html", ctx, status_code=status_code)


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
    scenario = await _load_scenario_for_edit(db, user, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404)
    ctx = await _edit_form_context(db, user, scenario)
    ctx["flash"] = None
    return templates.TemplateResponse(request, "scenarios/form.html", ctx)


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
        # PR2 D17 (Task 4c): same ceiling resolution as create — from the
        # TARGET org's OWN revenue (update_org, db.get above). A blank
        # pl_max/sl_max mints (e.g. a legacy uncapped row gains a cap on its
        # next edit); a typed value is bound ABOVE by it (D13).
        capacity_max = (
            capacity_max_for_org(update_org.annual_revenue, get_settings().capacity_k)
            if update_org is not None
            else None
        )
        form = parse_scenario_form(raw, capacity_max=capacity_max)
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
        # PR2 D19 (Task 4c): same wrap as create — a minted/typed capacity
        # `max` at or below the distribution's p95 surfaces here as a
        # FAIRCAMValidationError carrying the floor marker (ScenarioService
        # .update calls validate_fair_distributions before applying the
        # form's fields).
        message = (
            wrap_d19_floor_message(exc)
            if isinstance(exc, FAIRCAMValidationError) and D19_FLOOR_MARKER in str(exc)
            else str(exc)
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
            errors=[message],
            status_code=422,
        )

    # PR pi F12: mc_iterations dropped from scenario form (run-form now
    # owns it). Mitigating-controls join still applies.
    # Issue #217: the edit form renders checkboxes ONLY for ACTIVE controls
    # (ControlRepo.list_for_org filters to EntityStatus.ACTIVE). A control
    # that became DRAFT/DEPRECATED while still linked has no checkbox, so its
    # id is absent from the submission — a naive full diff-apply would DELETE
    # that link (the reported data loss). Scope removals to the eligible set
    # (the controls the form could actually render) so links to non-ACTIVE
    # controls survive the edit.
    eligible_control_ids = {c.id for c in available_controls}
    control_change = await ScenarioRepo(db).set_mitigating_controls(
        scenario_id=scenario_id,
        organization_id=user.organization_id,
        control_ids=parsed_control_ids,
        eligible_control_ids=eligible_control_ids,
    )
    # Issue #79 L6: ScenarioService.update() is a silent no-op (no audit, no
    # row_version bump) when only the control set changed — the descriptive
    # field diff it computes never sees ScenarioControl join rows. Emit the
    # audit here (repo stays audit-agnostic, mirrors ScenarioService owning
    # audit) and close the lost-update window: if update() didn't already
    # bump row_version (i.e. it took the no-op-fields path), bump it now so a
    # concurrent field edit can't silently clobber this control change.
    if control_change.changed:
        if updated.row_version == expected_row_version:
            updated.row_version += 1
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="scenario",
            entity_id=scenario_id,
            action="scenario.controls_changed",
            changes={
                "mitigating_controls": [
                    sorted(str(c) for c in control_change.before_ids),
                    sorted(str(c) for c in control_change.after_ids),
                ]
            },
            user_id=user.id,
            ip_address=client_ip(request),
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


@router.post(
    "/scenarios/{scenario_id}/delete",
    dependencies=[Depends(require_step_up(StepUpCategory.DESTRUCTIVE))],
)
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


@router.post("/scenarios/{scenario_id}/loss/pin")
async def pin_scenario_loss(
    request: Request,
    scenario_id: uuid.UUID,
    field: Literal["primary", "secondary"] = Form(...),
    pin_p50: str = Form(...),
    pin_p95: str = Form(...),
    expected_row_version: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """PR3 T3 (D20/D21): pin ``field``'s loss dispersion to an analyst-typed
    (p50, p95) dollar pair.

    Analyst+ only — ``Depends(require_role(UserRole.ANALYST,
    UserRole.ADMIN))``, the update/delete/confirm_vuln_framing precedent
    (Sec-I4: ``can_adopt`` at routes/library.py:267 is a template button
    flag, NOT route enforcement — never hand-roll an inline role check).
    ``field`` is a ``Literal["primary", "secondary"]`` Form param — FastAPI
    422s on any other value automatically (Sec-I3), no service-level branch
    needed. CSRF enforced by the global CSRFMiddleware, same as every other
    POST in this module.

    T3.a gate fix (SPEC B-1): every 422/409 failure branch below
    RE-RENDERS the edit form (``_render_loss_action_failure``) with an
    alert banner and the just-typed p50/p95 preserved, instead of raising
    a raw ``HTTPException`` — base.html's hx-boost 4xx force-swap would
    otherwise replace the analyst's whole page with a JSON
    ``{"detail": ...}`` body. Only a genuinely missing/wrong-org scenario
    (``NotFoundError``) still raises a plain 404 — there is no edit page to
    re-render for a scenario that doesn't exist for this org.
    """
    row_version = parse_expected_row_version(expected_row_version)
    if row_version is None:
        return await _render_loss_action_failure(
            request,
            db,
            user,
            scenario_id,
            field=field,
            message="expected_row_version: missing or invalid hidden form field",
            status_code=422,
            submitted_p50=pin_p50,
            submitted_p95=pin_p95,
        )
    try:
        p50 = _parse_pin_quantile(pin_p50, field_name="pin_p50")
        p95 = _parse_pin_quantile(pin_p95, field_name="pin_p95")
    except ValueError as exc:
        return await _render_loss_action_failure(
            request,
            db,
            user,
            scenario_id,
            field=field,
            message=str(exc),
            status_code=422,
            submitted_p50=pin_p50,
            submitted_p95=pin_p95,
        )
    try:
        await pin_loss(
            db,
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            field=field,
            p50=p50,
            p95=p95,
            expected_row_version=row_version,
            actor=user,
            ip_address=client_ip(request),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        return await _render_loss_action_failure(
            request,
            db,
            user,
            scenario_id,
            field=field,
            message=(
                "Another user updated this scenario — please reload and "
                "retry your edit. " + str(exc)
            ),
            status_code=409,
            submitted_p50=pin_p50,
            submitted_p95=pin_p95,
        )
    except ValidationError as exc:
        # Mirrors update_scenario's D19 wrap exactly — validate_fair_
        # distributions raises FAIRCAMValidationError directly (not
        # re-wrapped by the service; see loss_pinning.LossPinError's
        # docstring), so this except catches BOTH that and LossPinError's
        # own domain-validation failures via the shared ValidationError base.
        message = (
            wrap_d19_floor_message(exc)
            if isinstance(exc, FAIRCAMValidationError) and D19_FLOOR_MARKER in str(exc)
            else str(exc)
        )
        return await _render_loss_action_failure(
            request,
            db,
            user,
            scenario_id,
            field=field,
            message=message,
            status_code=422,
            submitted_p50=pin_p50,
            submitted_p95=pin_p95,
        )
    return RedirectResponse(url=f"/scenarios/{scenario_id}?pinned=1", status_code=303)


@router.post("/scenarios/{scenario_id}/loss/unpin")
async def unpin_scenario_loss(
    request: Request,
    scenario_id: uuid.UUID,
    field: Literal["primary", "secondary"] = Form(...),
    expected_row_version: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """PR3 T3 (D20/D21): remove the ``analyst_pin`` stamp from ``field``,
    restoring sweep/banner eligibility (D20). See ``pin_scenario_loss`` for
    the shared RBAC/CSRF/Literal-validation posture, and for the T3.a
    (SPEC B-1) rendered-failure-path rationale — there is no p50/p95 to
    preserve here (unpin has no quantile inputs), so
    ``_render_loss_action_failure`` is called without ``submitted_p50``/
    ``submitted_p95``.
    """
    row_version = parse_expected_row_version(expected_row_version)
    if row_version is None:
        return await _render_loss_action_failure(
            request,
            db,
            user,
            scenario_id,
            field=field,
            message="expected_row_version: missing or invalid hidden form field",
            status_code=422,
        )
    try:
        await unpin_loss(
            db,
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            field=field,
            expected_row_version=row_version,
            actor=user,
            ip_address=client_ip(request),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        return await _render_loss_action_failure(
            request,
            db,
            user,
            scenario_id,
            field=field,
            message=(
                "Another user updated this scenario — please reload and "
                "retry your edit. " + str(exc)
            ),
            status_code=409,
        )
    except ValidationError as exc:
        return await _render_loss_action_failure(
            request,
            db,
            user,
            scenario_id,
            field=field,
            message=str(exc),
            status_code=422,
        )
    return RedirectResponse(url=f"/scenarios/{scenario_id}?unpinned=1", status_code=303)


@router.post("/scenarios/{scenario_id}/loss/refresh")
async def refresh_scenario_loss(
    request: Request,
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """PR3 T4 (D23): refresh a scenario's PL/SL from its pinned library
    entry's current published version.

    Two-step confirm, mirroring ``delete_scenario``'s cascade-confirmation
    shape (:1338 + confirm_delete.html) rather than the mixture-flatten
    "informed replacement" advisory-text-only shape (that shape is for a
    save the analyst is already committing to; this is a distinct,
    reversible-only-by-re-refreshing action that deserves its own
    look-before-you-leap step). The first POST (no ``confirm_refresh``)
    renders ``confirm_loss_refresh.html`` with a current-vs-entry
    comparison built READ-ONLY by ``loss_pinning.preview_loss_refresh`` --
    no mutation, no row lock, so a scenario that can't actually be
    refreshed (no linkage, a pinned field, an unresolvable entry) surfaces
    its error on this first POST via ``_render_view_action_failure``
    (T3.a's rendered-failure idiom, retargeted at view.html — never a raw
    JSON 4xx body) rather than rendering a confirm page for an action that
    can only fail on confirm. The second POST (``confirm_refresh=1``)
    performs the write via ``loss_pinning.refresh_loss_from_library``,
    which independently RE-validates under a row lock — no TOCTOU trust in
    the preview step; another edit/pin/refresh can land between the two
    requests.

    Analyst+ only, same RBAC/CSRF posture as pin/unpin (Sec-I4: never a
    hand-rolled inline role check). NO step-up (Arch-N4, decided): parity
    with ``update_scenario``, which can equally overwrite PL/SL without
    ``StepUpCategory.DESTRUCTIVE`` — delete remains the only destructive
    step-up action on this router.
    """
    form_data = await request.form()
    row_version = parse_expected_row_version(form_data.get("expected_row_version"))
    if row_version is None:
        return await _render_view_action_failure(
            request,
            db,
            user,
            scenario_id,
            message="expected_row_version: missing or invalid hidden form field",
            status_code=422,
        )
    confirm_refresh = form_data.get("confirm_refresh") == "1"

    if not confirm_refresh:
        try:
            plan = await preview_loss_refresh(
                db,
                organization_id=user.organization_id,
                scenario_id=scenario_id,
                expected_row_version=row_version,
            )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            return await _render_view_action_failure(
                request,
                db,
                user,
                scenario_id,
                message=(
                    "Another user updated this scenario — please reload and "
                    "retry your edit. " + str(exc)
                ),
                status_code=409,
            )
        except ValidationError as exc:
            return await _render_view_action_failure(
                request, db, user, scenario_id, message=str(exc), status_code=422
            )
        return templates.TemplateResponse(
            request,
            "scenarios/confirm_loss_refresh.html",
            {
                "current_user": user,
                "scenario": plan.scenario,
                "entry": plan.resolved.entry,
                "current_primary_loss": plan.scenario.primary_loss,
                "current_secondary_loss": plan.scenario.secondary_loss,
                "entry_primary_loss": plan.new_primary_loss,
                "entry_secondary_loss": plan.new_secondary_loss,
                # T4.a gate fix (METH I-4): basis-labeled dicts (kind +
                # pooled/display sigma + mixture max_component_sigma), not a
                # bare float -- the confirm page's sigma lines now render an
                # honest basis ("parent-lognormal basis" for lognormal/PERT,
                # the pooled+component label for a mixture) via the SAME
                # macros/chart.html::sigma_basis_line macro the stale-wide
                # banner uses.
                "current_primary_loss_sigma": _loss_sigma_display(plan.scenario.primary_loss),
                "current_secondary_loss_sigma": _loss_sigma_display(plan.scenario.secondary_loss),
                "entry_primary_loss_sigma": _loss_sigma_display(plan.new_primary_loss),
                "entry_secondary_loss_sigma": _loss_sigma_display(plan.new_secondary_loss),
                # T4.a gate fix (NTH, meth N-2): disclose when the refresh's
                # freshly-minted capacity cap differs from the field's PRIOR
                # stored max -- the executed example silently loosened an
                # existing cap ~200x with no confirm-page disclosure.
                "primary_cap_remint": _cap_remint_disclosure(
                    plan.scenario.primary_loss, plan.new_primary_loss
                ),
                "secondary_cap_remint": _cap_remint_disclosure(
                    plan.scenario.secondary_loss, plan.new_secondary_loss
                ),
            },
        )

    try:
        await refresh_loss_from_library(
            db,
            organization_id=user.organization_id,
            scenario_id=scenario_id,
            expected_row_version=row_version,
            actor=user,
            ip_address=client_ip(request),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        return await _render_view_action_failure(
            request,
            db,
            user,
            scenario_id,
            message=(
                "Another user updated this scenario — please reload and "
                "retry your edit. " + str(exc)
            ),
            status_code=409,
        )
    except ValidationError as exc:
        message = (
            wrap_d19_floor_message(exc)
            if isinstance(exc, FAIRCAMValidationError) and D19_FLOOR_MARKER in str(exc)
            else str(exc)
        )
        return await _render_view_action_failure(
            request, db, user, scenario_id, message=message, status_code=422
        )
    return RedirectResponse(url=f"/scenarios/{scenario_id}?loss_refreshed=1", status_code=303)


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
) -> Response:
    wiz = WizardStateService(db)
    existing: WizardState | None = None
    if tx is not None:
        # Drafts-surfaced T4b (DA-4/DQ-10): an EXPLICITLY-provided tx that
        # doesn't resolve to a live draft (swept/discarded/malformed/
        # bookmarked) must NOT mint a phantom draft via get_or_create below
        # — redirect with a friendly flash instead. Guarded BEFORE
        # _resolve_tx/get_or_create run. The no-tx entry path (back-button /
        # bare "+ New scenario") is untouched — it falls through unchanged.
        try:
            parsed_tx = uuid.UUID(tx)
        except ValueError:
            return RedirectResponse(url="/scenarios", status_code=status.HTTP_303_SEE_OTHER)
        existing = await wiz.get(user_id=user.id, tx_id=parsed_tx)
        if existing is None:
            return RedirectResponse(
                url="/scenarios?draft_expired=1", status_code=status.HTTP_303_SEE_OTHER
            )
    if existing is not None:
        # F-4: the guard above already loaded this exact (user_id, tx_id)
        # row via wiz.get() and nothing awaits/writes to it in between —
        # get_or_create's tx_id-provided branch would only re-fetch the same
        # row via another wiz.get() call. Reuse it instead of re-querying.
        state = existing
    else:
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
            await _fair_page_context(
                request=request,
                user=user,
                state=state,
                step=n,
                org_industry=org_industry,
                org_revenue_tier=org_revenue_tier,
                available_overlays=available_overlays,
                sme_directory_for_dropdown=sme_dir,
                db=db,
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


# PR2 D13/D18: capacity-bound epic (docs/superpowers/specs/2026-07-25-
# capacity-bound-design.md). D18's pinned copy now lives in
# services/capacity_bound_copy.py (Task 4b extracted it there so the
# scenario importer reuses the SAME string instead of re-typing it -- drift
# risk). Re-exported under the module-local name so every existing
# reference below is unchanged.
_D18_REVENUE_MESSAGE = D18_REVENUE_MESSAGE
_ORG_SETTINGS_HREF = "/organization"
_ORG_SETTINGS_HREF_TEXT = "Open organization settings"


async def _capacity_max_for_org(db: AsyncSession, organization_id: uuid.UUID) -> float | None:
    """D13/D18: mint the per-loss-component capacity cap from the TARGET
    org's OWN revenue.

    Reads via ``db.get(Organization, organization_id)`` -- NEVER
    ``get_sole_org``/``require_sole_org`` (``services/org.py``) or any
    ``ORDER BY id LIMIT 1`` shape, both forbidden on this path (Phase 1 is
    single-org so this is latent, not live, but ``max`` is the first value to
    hard-code a *financial* property of one org into another org's row).
    Returns ``None`` (D14: never an invented number) when the org row is
    missing or its ``annual_revenue`` is unset/non-positive -- the caller is
    responsible for treating ``None`` as the D18 precondition failure.
    """
    organization = await db.get(Organization, organization_id)
    if organization is None:
        return None
    return capacity_max_for_org(organization.annual_revenue, get_settings().capacity_k)


def _existing_capacity_max(scenario: Scenario) -> float | None:
    """Preserve-existing (D13 "snapshot-frozen at author time"): read a
    previously-minted/authored PR2 capacity cap off the TARGET scenario's
    OWN stored loss dicts, so a wizard re-estimate never silently replaces an
    analyst's explicit cap with ``k * CURRENT revenue`` -- exactly the
    silent-strip class the wizard already refuses to commit for
    ``legacy_residual``.

    PL and SL share ONE minted cap by construction (``loss_capacity.py``: the
    per-loss-component cap is applied identically to both fields at mint
    time), so either field's stored ``"max"`` is authoritative for the
    scenario; ``primary_loss`` is checked first, ``secondary_loss`` as a
    fallback (defensive -- covers a hand-authored asymmetric cap from the
    expert form, D17/Task 4c, which is out of this task's scope but not one
    to silently mis-read here).
    """
    for dist in (scenario.primary_loss, scenario.secondary_loss):
        if isinstance(dist, dict):
            existing = dist.get("max")
            if isinstance(existing, (int, float)) and not isinstance(existing, bool):
                return float(existing)
    return None


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
        # PR2 D18 (round-6-fixed): the toggle stays ENABLED; block ONLY a
        # SUBMITTED catastrophic choice when the org's annual revenue is
        # unset. An unchecked submission is an honest analyst downgrade to
        # "capped" and is NEVER gated here (see the loss_shape assignment
        # below, reached only when this block does not return) -- gating on
        # the *incoming* state instead would trap the legitimate unchecked ->
        # capped choice on the 11 catastrophic library seeds. Mirrors the
        # validation-failure branch above: a blocked POST leaves BOTH
        # state.sme_estimates and state.loss_shape UNCHANGED (the merge below
        # never runs). `and` short-circuits, so the org lookup only runs once
        # n==4 and the checkbox was actually submitted.
        if (
            n == 4
            and form.get("loss_catastrophic")
            and await _capacity_max_for_org(db, user.organization_id) is None
        ):
            return await _render_fair_page_with_flash(
                request,
                db,
                user,
                uuid.UUID(state.tx_id),
                step=4,
                message=_D18_REVENUE_MESSAGE,
                href=_ORG_SETTINGS_HREF,
                href_text=_ORG_SETTINGS_HREF_TEXT,
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


# PR2 D19: services/capacity_bound_copy.py owns the marker + wrap function
# (Task 4b extracted them so the scenario importer reuses the SAME pinned
# copy instead of re-typing it). Re-exported under the module-local names so
# every existing reference below is unchanged.
_D19_FLOOR_MARKER = D19_FLOOR_MARKER
_wrap_d19_floor_message = wrap_d19_floor_message


async def _render_fair_page_with_flash(
    request: Request,
    db: AsyncSession,
    user: User,
    tx: uuid.UUID,
    *,
    step: int,
    message: str,
    href: str | None = None,
    href_text: str | None = None,
) -> HTMLResponse:
    """Re-render a FAIR-param page (step 3 Likelihood or step 4 Impact) with a
    flash banner at HTTP 422. Used by the per-page step-3/step-4 POST handlers
    when ``_validate_page_rows`` rejects the submitted SME rows, and (PR2 D18)
    by the step-4 catastrophic-without-revenue gate, which passes ``href`` to
    deep-link the flash to Organization settings.

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
    ctx_dict = await _fair_page_context(
        request=request,
        user=user,
        state=state,
        step=step,
        org_industry=org_industry,
        org_revenue_tier=org_revenue_tier,
        available_overlays=available_overlays,
        sme_directory_for_dropdown=sme_dir,
        db=db,
    )
    ctx_dict["flash"] = build_flash(message, "error", href=href, href_text=href_text)
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
    href: str | None = None,
    href_text: str | None = None,
) -> HTMLResponse:
    """Re-render the step-6 review page with a flash banner at HTTP 422.

    2026-05-28 step-3 split (D6): finalize is state-sourced; a malformed /
    incomplete draft (e.g. an empty required fieldset) routes here instead of
    emitting FastAPI's raw 422 JSON dump. The operator lands back on the review
    page with a readable error. ``href``/``href_text`` (PR2 D18) deep-link the
    flash to Organization settings for the revenue-precondition backstop.

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
            "flash": build_flash(message, "error", href=href, href_text=href_text),
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

        # #56: a targeted draft (target_scenario_id set) finalizes into an
        # UPDATE of that scenario instead of a CREATE. Hoisted here (PR2
        # D13/D18, PLAN-GATE ordering note) -- BEFORE build_scenario_payload
        # -- so preserve-existing (below) can peek at the target's already-
        # stored capacity `max`. This is the FIRST (and only) locked read of
        # the target row; it is reused again below for status / effect /
        # scenario_type / the descriptive version label (the wizard never
        # collects those, so they're pulled from the live row and spliced
        # onto the form the wizard DID collect) -- do not add a second,
        # unlocked read.
        is_reestimate = state.target_scenario_id is not None
        target: Scenario | None = None
        if is_reestimate:
            # lock=True at the FIRST read (PR-gate Arch finding): the later
            # update_from_wizard re-resolve returns this same identity-mapped
            # instance WITHOUT refreshing row_version, so the optimistic-lock
            # check compares against the value captured here. Locking here
            # closes the stale-read window on multi-worker/Postgres deploys.
            target = await ScenarioRepo(db).get_for_org(
                organization_id=user.organization_id,
                scenario_id=uuid.UUID(state.target_scenario_id),
                lock=True,
            )
            if target is None:
                # Deleted while the wizard was in flight: keep the draft so
                # the operator can see their entered data, surface a flash.
                # Rollback first so the advance_step token bump doesn't
                # commit (symmetry with the conflict path below).
                await db.rollback()
                return await _render_review_with_flash(
                    request,
                    db,
                    user,
                    tx,
                    message="This scenario no longer exists — it was deleted "
                    "while you were estimating. Cancel to discard this draft.",
                )

        # PR2 D13/D17/D18/D19: mint (or preserve) the catastrophic-loss
        # capacity cap BEFORE running the scipy fit, so a blocked finalize
        # never runs it needlessly.
        #   - Preserve-existing is the DEFAULT on a re-estimate: an already-
        #     minted/authored cap on the target (D13 "snapshot-frozen at
        #     author time") is carried forward untouched -- a re-estimate
        #     must not silently replace an analyst's explicit cap with
        #     k * CURRENT revenue (a revenue edit is exactly how that could
        #     happen). Only a FRESH create, or a re-estimate with no prior
        #     cap (switching capped -> catastrophic just now), mints anew.
        #   - D18 finalize backstop (TOCTOU): a stale catastrophic draft
        #     (create OR re-estimate) whose org has since lost its revenue
        #     blocks HERE with the same D18 copy -- a 422 re-render, never a
        #     silently-uncapped scenario.
        capacity_max: float | None = None
        if state.loss_shape == "catastrophic":
            capacity_max = _existing_capacity_max(target) if target is not None else None
            if capacity_max is None:
                capacity_max = await _capacity_max_for_org(db, user.organization_id)
                if capacity_max is None:
                    await db.rollback()
                    return await _render_review_with_flash(
                        request,
                        db,
                        user,
                        tx,
                        message=_D18_REVENUE_MESSAGE,
                        href=_ORG_SETTINGS_HREF,
                        href_text=_ORG_SETTINGS_HREF_TEXT,
                    )

        # Sec-20 PR3: offload the scipy.optimize loop off the event loop.
        try:
            results = await run_in_threadpool(process_sme_estimates, state)
        except FinalizeBudgetExceededError as e:
            # Narrower subclass first; dispatch on class rather than sniffing
            # the aggregate_timeout flag on the parent (kept for back-compat).
            raise HTTPException(422, str(e)) from e
        except FinalizationError as e:
            raise HTTPException(422, detail={"field_errors": e.field_errors}) from e
        payload = build_scenario_payload(results, state, capacity_max=capacity_max)
        # Arch-10 PR1: rename payload keys -> ScenarioForm column names.
        form_kwargs = {_PAYLOAD_TO_FORM[fs]: payload[fs] for fs in payload}
        # T5: state.basic_fields() exposes step-2 fields (name, threat_*,
        # asset_class, attack_vector, library_entry_id) as a ScenarioForm-
        # splattable dict.
        if is_reestimate:
            if target is None:
                # Unreachable: the hoisted locked read above already returned
                # a 404 flash when target was None. Re-checked here (not a
                # bare `assert`, which -O strips and this repo's ruff config
                # bans in application code) purely so mypy narrows
                # `target: Scenario` for the attribute access below.
                raise AssertionError("unreachable: target resolved above when is_reestimate")
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
            # PR2 D19: a minted/preserved `capacity_max` that sits at or below
            # the distribution's p95 is ALSO rejected here, as a
            # FAIRCAMValidationError carrying the D19 floor marker (Task 3b's
            # _validate_capacity_floor) -- wrap its FACTUAL p95-vs-cap string
            # with the three operator remedies the design pins, instead of the
            # generic _step3_flash_message treatment every other
            # ValidationError gets.
            #
            # advance_step (above) bumped the CAS version_token in this still-
            # uncommitted transaction; roll it back so a rejected finalize does
            # NOT consume the token (A-I3) — the same token stays re-submittable
            # after the operator narrows the offending range. (For the headline
            # FAIRCAMValidationError case the validator runs BEFORE any row write,
            # so the rollback's real job is unwinding the advance_step flush; it
            # also covers any ValidationError subclass that raises post-flush.)
            await db.rollback()
            message = (
                _wrap_d19_floor_message(exc)
                if isinstance(exc, FAIRCAMValidationError) and _D19_FLOOR_MARKER in str(exc)
                else _step3_flash_message(exc)
            )
            return await _render_review_with_flash(request, db, user, tx, message=message)
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
            control_change = await ScenarioRepo(db).set_mitigating_controls(
                scenario_id=scenario.id,
                organization_id=user.organization_id,
                control_ids=mitigating_uuids,
                eligible_control_ids=eligible_control_ids,
            )
            # Issue #79 L6: update_from_wizard() already bumps row_version
            # unconditionally, so no lost-update window here (unlike the
            # plain edit path) — just the audit row is missing.
            if control_change.changed:
                await AuditWriter(db).log(
                    organization_id=user.organization_id,
                    entity_type="scenario",
                    entity_id=scenario.id,
                    action="scenario.controls_changed",
                    changes={
                        "mitigating_controls": [
                            sorted(str(c) for c in control_change.before_ids),
                            sorted(str(c) for c in control_change.after_ids),
                        ]
                    },
                    user_id=user.id,
                    ip_address=client_ip(request),
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
            # Issue #79 L11: the replace-all delete below silently discarded
            # the analyst's prior elicited SME estimates with no audit trail —
            # the only anti-pattern here besides L6's controls_changed gap.
            # Load-then-log BEFORE the delete, mirroring
            # set_scenario_attack_mappings's before/after shape
            # (attack_mappings.py:169-177) and sme_estimate.recorded's
            # entity_type/entity_id convention (wizard_finalize.py:723).
            # Payload is compact (id + low/high, not full rows) per the
            # full-disk-outage constraint on audit_log growth.
            existing_estimates = (
                (
                    await db.execute(
                        select(ScenarioSMEEstimate).where(
                            ScenarioSMEEstimate.scenario_id == scenario.id,
                            ScenarioSMEEstimate.organization_id == user.organization_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if existing_estimates:
                await AuditWriter(db).log(
                    organization_id=user.organization_id,
                    entity_type="scenario_sme_estimate",
                    entity_id=scenario.id,
                    action="sme_estimate.replaced",
                    changes={
                        "removed": [
                            {"id": str(row.id), "low": row.low, "high": row.high}
                            for row in existing_estimates
                        ],
                        "added": None,
                    },
                    user_id=user.id,
                    ip_address=client_ip(request),
                )
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
        # Finalize advisory (Task 5, gate round 1 BLOCKER): the success path
        # is a 303 redirect and this codebase's flash is per-render with NO
        # session persistence (flash.py:10-11) -- a build_flash() built here
        # would be silently discarded by the redirect. Ride the established
        # ?deleted=1 query-param idiom (routes/scenarios.py:220-226) instead:
        # view_scenario re-derives the sigma from the scenario's own stored
        # dicts when it sees ?loss_wide=1 (no value smuggled through the URL).
        # T4.b (confirmation-gate I-2): FIRING basis — same rationale as the
        # ?loss_wide=1 re-derivation this redirect points at.
        max_sigma = _max_tripwire_sigma(scenario)
        # T4.a gate fix (METH I-1): toleranced (see the matching comment on
        # view_scenario's ?loss_wide=1 re-derivation above) -- a redirect to
        # ?loss_wide=1 for a stored sigma that only drifted a few ULPs above
        # the default would immediately be re-derived AWAY by that same
        # toleranced check, so gating the redirect itself the same way
        # avoids a pointless redirect+re-derive round trip.
        wide_suffix = (
            "?loss_wide=1"
            if max_sigma is not None and max_sigma > WITHIN_SCENARIO_SIGMA_DEFAULT + _SIGMA_TOL
            else ""
        )
        return RedirectResponse(
            url=f"/scenarios/{scenario.id}{wide_suffix}",
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

    # Drafts-surfaced T4b (DA-4/DA-8): guard the parse (malformed tx → 303,
    # not 500) and call `clear` directly — no more mint-then-delete via
    # get_or_create on an unknown tx. `clear` is a WHERE-delete, idempotent
    # on a missing row.
    try:
        parsed_tx = uuid.UUID(tx)
    except ValueError:
        return RedirectResponse(url="/scenarios", status_code=status.HTTP_303_SEE_OTHER)

    wiz = WizardStateService(db)
    await wiz.clear(user_id=user.id, tx_id=parsed_tx)
    await db.commit()
    return RedirectResponse(url="/scenarios", status_code=status.HTTP_303_SEE_OTHER)


async def _build_readout_cfg(
    db: AsyncSession, user: User, state: WizardState
) -> dict[str, dict[str, Any]]:
    """PR3 T2 (D22): server-computed props for the wizard step-4 live
    loss-dispersion readout (``lossDispersionReadout``, static/js/loss_preview.js).

    Called ONLY when the page is Impact (Arch NTH-R2-1 — the fieldset loop is
    page-scoped and pl/sl never render on Likelihood; a Likelihood render must
    not pay a semaphore-serialized scipy fit for cfg nobody uses).

    Returns one cfg dict per loss field (``"pl"``/``"sl"``), keyed by
    ``fieldKey`` — the shape ``lossDispersionReadout(cfg)`` expects. Both
    dicts share the same policy values (sigma default/warn threshold/cap/
    currency/tef+vuln preview means); only ``fieldKey``/``label``/
    ``initialLow``/``initialHigh``/``initialRowIndex``/``fieldCeilingExceeded``
    differ.
    """
    preview_means: dict[str, float | None] = {"tef": None, "vuln": None}
    try:
        # Threadpool AND the finalize semaphore, BOTH REQUIRED (Sec-I1/Arch-I3):
        # finalize's control is run_in_threadpool + _FINALIZE_SEMAPHORE
        # (wizard_finalize.py:54, acquired routes/scenarios.py:2774) — Sec-21
        # exists precisely so concurrent scipy.optimize loops cannot saturate
        # the shared-cpu worker, and a step-4 GET is a HOTTER path than finalize.
        async with _FINALIZE_SEMAPHORE:
            results = await run_in_threadpool(process_sme_estimates, state)
        for fs in ("tef", "vuln"):
            r = results.get(fs)
            # N1 (PR3 T2.a gate fix): guard on r.collapsed AND
            # pert.high > 0. process_sme_estimates stores a
            # PertTriple(0,0,0) SENTINEL when a fieldset's pipeline has no
            # collapser (wizard_finalize.py:439-440 — today only catastrophic
            # pl/sl, never tef/vuln, but this loop must not assume that stays
            # true forever). An unguarded read would silently compute a
            # confident $0 preview mean and feed it straight into the ALE
            # line as if it were real.
            if r is not None and r.collapsed and r.pert is not None and r.pert.high > 0:
                p = r.pert
                preview_means[fs] = (p.low + 4 * p.mode + p.high) / 6.0
    except Exception:  # broad on purpose — preview only; finalize re-validates
        # step-4 GET must never 500 because pooling would reject rows finalize
        # will flash about later — the readout just degrades to hidden (ALE
        # line requires both tefMean/vulnMean non-null).
        logger.info("step-4 readout preview means unavailable", exc_info=True)

    # M4 (PR3 T2.a gate fix): mirror finalize's cap precedence EXACTLY
    # (the capacity_max block in post_wizard_finalize, ~routes/scenarios.py
    # :2881-2896) — preserve-existing (D13 "snapshot-frozen at author time")
    # FIRST when this draft targets an existing scenario (a wizard
    # re-estimate), minted k*CURRENT-revenue only as fallback. Unguarded,
    # the readout would show k*current-revenue while finalize silently
    # keeps the authored/original cap -- a preview that lies about which cap
    # actually gates the save. `lock=False` (the default): this is a
    # read-only preview, not the locked read finalize itself takes before
    # writing.
    cap: float | None = None
    if state.target_scenario_id is not None:
        target = await ScenarioRepo(db).get_for_org(
            organization_id=user.organization_id,
            scenario_id=uuid.UUID(state.target_scenario_id),
        )
        if target is not None:
            cap = _existing_capacity_max(target)
    if cap is None:
        cap = await _capacity_max_for_org(db, user.organization_id)

    mode = "lognormal" if state.loss_shape == "catastrophic" else "capped_pert"

    # M3(b) (PR3 T2.a gate fix): the ceiling verdict must be FIELD-scoped,
    # not row-scoped -- D19 finalize rejects a catastrophic submission when
    # ANY persisted row's fitted p95 meets/exceeds the cap, regardless of
    # which row happens to be focused in the live preview (pre-fix: a green
    # panel on a tight first row could still 422 at finalize because a
    # wider second row alone breached the cap). Fit each row via the SAME
    # p5/p95 basis the wizard rows already use
    # (fair_cam.lognormal_from_quantiles, q_low=0.05/q_high=0.95). NOTE
    # (re-gate N-c): for this symmetric fit the fitted p95 IS `high`
    # identically — the fit exists here only so malformed rows
    # (inverted/non-positive, e.g. mid-edit) raise inside the try and get
    # skipped rather than misread as a breach. Only meaningful in catastrophic (lognormal)
    # mode -- capped_pert rows collapse to a bounded PERT and finalize never
    # mints/applies a capacity cap to them (state.loss_shape != "catastrophic"
    # short-circuits the capacity_max block entirely).
    field_ceiling_exceeded: dict[str, bool] = {"pl": False, "sl": False}
    if mode == "lognormal" and cap is not None and cap > 0:
        for fk in ("pl", "sl"):
            # Re-gate I4: walk the SAME rows finalize will fit — the raw list
            # can contain superseded duplicates (_dedup_latest_per_sme keeps
            # the latest per sme identity), and warning on a row finalize
            # discards makes "saving will be rejected" an absolute claim
            # about a save that would succeed.
            #
            # PR3 T4 carryover (deferred T2 NTH): _dedup_latest_per_sme
            # itself can raise -- ``row_identity_uuid`` does ``UUID(str(...))``
            # on an unparseable sme_id (ValueError) or ``row["sme_name"]``/
            # ``.casefold()`` on a missing or non-string sme_name
            # (KeyError/AttributeError) -- reachable only via draft
            # corruption (the HTTP form path always normalizes a blank
            # sme_id to None, see the step-4 POST handler), but this call
            # previously sat OUTSIDE the per-row try below, so one
            # corrupted row 500'd the WHOLE step-4 GET instead of being
            # skipped like every other malformed-row case in this preview
            # builder. Wrapped here, falling back to "no ceiling verdict for
            # this fieldset" (leaves field_ceiling_exceeded[fk] at its False
            # default) -- same "a step-4 GET must never 500 because pooling
            # would reject rows finalize will flash about later" philosophy
            # already documented on the preview_means block above.
            try:
                dedup_rows = _dedup_latest_per_sme(state.sme_estimates.get(fk) or [])
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            for row in dedup_rows:
                try:
                    low, high = float(row["low"]), float(row["high"])
                    fit = lognormal_from_quantiles(low, high, q_low=0.05, q_high=0.95)
                except (KeyError, TypeError, ValueError):
                    continue
                row_p95 = float(lognormal_quantiles(fit["mean"], fit["sigma"], (0.95,))[0])
                if row_p95 >= cap:
                    field_ceiling_exceeded[fk] = True

    base: dict[str, Any] = {
        # N6: Task 3 (analyst pin/unpin) also consumes sigmaDefault/
        # warnThreshold for the expert-form pin panel's own readout mount —
        # keep this dict's shape stable for that reuse.
        "sigmaDefault": WITHIN_SCENARIO_SIGMA_DEFAULT,
        "warnThreshold": SIGMA_WARN_THRESHOLD,
        "cap": cap,
        # Global Constraints ("wizard elicits USD"): the wizard has no
        # currency selector (entry_currency is an expert-form-only field,
        # Multi-currency P2) — USD here is correct, not a placeholder.
        "currency": "USD",
        "quantileBasis": "p5p95",  # wizard SME rows are p5/p95
        "tefMean": preview_means["tef"],
        "vulnMean": preview_means["vuln"],
    }
    out: dict[str, dict[str, Any]] = {}
    # N8 (PR3 T2.a gate fix): field key/label sourced from IMPACT_FIELDSETS
    # (the single source of truth, services/wizard_questions.py) rather than
    # a hand-typed tuple -- picks up "Secondary loss (optional)" instead of
    # a bare "Secondary loss".
    for field_key, label in IMPACT_FIELDSETS:
        rows = state.sme_estimates.get(field_key) or []
        last = rows[-1] if rows else None
        cfg = dict(base)
        cfg["mode"] = mode
        cfg["fieldKey"] = field_key
        cfg["label"] = label
        # Seeds the readout so it isn't blank on first paint (before any row
        # focus/blur fires the loss-row-input event) — the last row wins,
        # matching the "readout tracks the last-focused SME row" default.
        cfg["initialLow"] = last.get("low") if last else None
        cfg["initialHigh"] = last.get("high") if last else None
        # M2 (PR3 T2.a gate fix): the row index the seed above came from, so
        # the client can attribute the first-paint seed ("previewing last
        # saved row N") instead of leaving it unlabeled until a focus event.
        cfg["initialRowIndex"] = (len(rows) - 1) if rows else None
        cfg["fieldCeilingExceeded"] = field_ceiling_exceeded[field_key]
        out[field_key] = cfg
    return out


async def _fair_page_context(
    request: Request,
    user: User,
    state: WizardState,
    step: int,
    org_industry: str | None,
    org_revenue_tier: str | None,
    available_overlays: list[Any],
    sme_directory_for_dropdown: list[dict[str, Any]],
    db: AsyncSession,
) -> dict[str, Any]:
    """Context for a split FAIR-param page (step 3 Likelihood / step 4 Impact)
    and its HTMX swap fragment (``_fair_params_form_inner.html``).

    Scopes fieldsets to the page, filters no-op overlays (D4), and gates the
    calibration/override banner to the Impact page (PL/SL is the only calibrated
    half). The GET handler, both HTMX endpoints, and the flash re-render path
    ALL build context here so the partial renders identically regardless of
    swap source (Sec-25 PR2 single-source guard — omitting e.g. ``csrf_token``
    after an outerHTML swap would break the next POST).

    PR3 T2 (D22): also the single-source builder for ``readout_cfg`` (the live
    loss-dispersion readout's props) — built ONLY on the Impact page, so all
    four render paths that funnel through here (GET step 4, the 422 flash
    re-render, and the two HTMX prefill/overlay partial-swap POSTs) carry
    identical props (SC-6's swap-boundary contract).

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
    # PR3 T2 (D22): readout_cfg is built ONLY on the Impact page (Arch
    # NTH-R2-1) — the fieldset loop in _fair_params_form_inner.html only
    # mounts the pl/sl readouts there, and a Likelihood render must not pay
    # the semaphore-serialized scipy fit for cfg nobody uses.
    readout_cfg = await _build_readout_cfg(db, user, state) if page == "impact" else None
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
        "readout_cfg": readout_cfg,
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
        await _fair_page_context(
            request=request,
            user=user,
            state=state,
            step=3 if page == "likelihood" else 4,
            org_industry=org_industry,
            org_revenue_tier=org_revenue_tier,
            available_overlays=available_overlays,
            sme_directory_for_dropdown=sme_dir,
            db=db,
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
        await _fair_page_context(
            request=request,
            user=user,
            state=state,
            step=3 if page == "likelihood" else 4,
            org_industry=org_industry,
            org_revenue_tier=org_revenue_tier,
            available_overlays=available_overlays,
            sme_directory_for_dropdown=sme_dir,
            db=db,
        ),
    )
