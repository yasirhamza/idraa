"""Register import routes — staged multi-step upload (admin-only).

Epic #34 P1c Task 4 (upload / sheet-pick / column-map) + Task 5 (value-bind +
binding profiles) + Task 6 (preview / convert / report). Task 8 (later, same
PR) extends this module with converter-aware copy on the report/view pages.

Flow (full-page 303 redirects threading the opaque ``token`` in the path —
the app-wide wizard precedent; no HTMX step-nav precedent exists to mirror,
per the plan's scope-drift log):

    GET  /register-import                      upload form
    POST /register-import                      stage_upload -> 303 sheet|columns
    GET  /register-import/{token}/sheet         xlsx multi-sheet picker
    POST /register-import/{token}/sheet         set_sheet -> 303 columns
    GET  /register-import/{token}/columns       header -> target mapping form
    POST /register-import/{token}/columns       set_column_map -> 303 bind
    GET  /register-import/{token}/bind          value-bind form (3 fieldsets)
    POST /register-import/{token}/bind          set_value_bindings (+ optional
                                                 save_profile) -> 303 preview
    POST /register-import/{token}/apply-profile apply_profile -> 303 bind
                                                 (drift warnings flashed via
                                                 ``?drift=`` query params — this
                                                 codebase's flash pattern is
                                                 per-render only, see
                                                 services/flash.py, so a value
                                                 that must survive a redirect
                                                 rides the query string, mirroring
                                                 the existing ``?saved=1`` /
                                                 ``?imported=N`` precedents)
    GET  /register-import/{token}/preview       dry classification (Task 6):
                                                 preview() = build_bound_rows +
                                                 classify_rows, would_create /
                                                 parked / duplicates / errors
                                                 rendered via preview_table
    POST /register-import/{token}/convert       apply() -> 200 report.html
                                                 directly (not a redirect —
                                                 the token is deleted the
                                                 moment this succeeds, so
                                                 there is no page left to
                                                 redirect BACK to); re-POST
                                                 on the same (now-deleted)
                                                 token 409s (single-use)

RBAC: every route is ``require_role(UserRole.ADMIN)`` (Global Constraints).
CSRF is enforced by the global CSRFMiddleware on every unsafe method here —
these routes are NOT exempted.

``PreviewExpiredError`` (unknown / expired / cross-org / wrong-flow token)
renders the register-import-specific 409 expired page (Task 4 plan-gate
amendment Spec-I1) — mirrors ``routes/scenario_import.py``'s posture exactly,
just with its own template (the existing ``scenarios/import_expired.html``
siblings are entity-worded, not generically reusable).

``MAX_UPLOAD_BYTES`` guard on ``POST /register-import`` is belt-and-suspenders
across THREE layers (Task 4 plan-gate amendment Sec-I3): a forgeable
Content-Length pre-check here (mirrors ``routes/scenario_import.py:88-97``),
a post-read length check here, AND ``RegisterImportService.stage_upload``'s
own post-read check (the one that actually holds for a chunked/streamed
upload with no, or a lying, Content-Length header).
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from idraa.app import templates
from idraa.errors import NotFoundError, ValidationError
from idraa.models.enums import ThreatCategory, UserRole
from idraa.models.user import User
from idraa.routes.deps import MAX_UPLOAD_BYTES, client_ip, get_db, require_role
from idraa.routes.scenario_form_helpers import THREAT_CATEGORY_CHOICES
from idraa.services.flash import build_flash
from idraa.services.qualitative_bands import EffectiveBand, QualitativeBandService
from idraa.services.qualitative_converter import SL_NOTE, ClassifiedRows, ConversionReport
from idraa.services.register_import import (
    PreviewExpiredError,
    RegisterImportService,
    preselect_bindings,
)
from idraa.services.register_import_parsers import list_sheet_names

router = APIRouter(tags=["register-import"])

# Human-friendly labels for the 8 column-map targets, in a UX-sensible order
# (score-relevant fields first). Values are exactly `TARGETS` (Task 4).
_TARGET_OPTIONS: list[tuple[str, str]] = [
    ("title", "Title"),
    ("likelihood", "Likelihood"),
    ("impact", "Impact"),
    ("category", "Category"),
    ("description", "Description"),
    ("owner", "Owner"),
    ("carry_along", "Carry along (kept in the scenario description)"),
    ("ignore", "Ignore"),
]
# Kept in sync with `TARGETS` by test_register_import_routes.py's
# `test_target_options_match_targets` (no runtime assert — S101).

# The three value-bind fieldsets, in render order. Also doubles as the
# `{group}_value_{i}` / `{group}_target_{i}` form-field prefix set both
# `_parse_bind_form` (below) and `templates/register_import/bind.html`
# agree on.
_BIND_GROUPS: tuple[str, ...] = ("likelihood", "impact", "category")

# The category fieldset's "opt out" choice. Kept in sync with
# `services.register_import._PARKED_CATEGORY` (a private module constant —
# duplicated here rather than imported, same posture as `_TARGET_OPTIONS`
# above) by test_register_import_routes.py's
# `test_bind_post_park_category_value_accepted`, which round-trips this
# EXACT value through the live POST endpoint and asserts a 303 (not a 422
# from the service's `_validate_bindings_group` rejecting an unrecognized
# target) — a behavioral guard, not a static string-equality one.
# Grammar pinned verbatim by the plan (Meth-R2-NTH-1 / plan-gate M-2 — OT is
# IN scope, this label is about non-information/non-OT rows only).
_PARK_VALUE = "__parked__"
_PARK_LABEL = "Parked — out of scope (neither information- nor OT-risk; see #39)"

# Category select options: the curated ThreatCategory labels (shared with
# the scenario form so casing/wording stays consistent across the two
# scenario-creation paths) plus the park option, with a blank leading
# placeholder so an unbound value never accidentally shows the first real
# option as "selected" (form_field's <select> only marks `selected` on an
# exact value match).
_CATEGORY_OPTIONS: list[tuple[str, str]] = [
    ("", "— choose a category —"),
    *THREAT_CATEGORY_CHOICES,
    (_PARK_VALUE, _PARK_LABEL),
]


def _band_options(
    effective: dict[tuple[str, str], EffectiveBand], kind: str
) -> list[tuple[str, str]]:
    """Sorted (value, display label) options for one band ``kind``
    ("frequency" | "magnitude"), plus a blank leading placeholder (same
    unbound-never-looks-selected rationale as ``_CATEGORY_OPTIONS``).
    Sorted by ``mode`` ascending — a natural least-to-most severe/frequent
    reading order; ``EffectiveBand`` carries no ``sort_order`` (that lives
    only on the canonical ORM row), so this is the best ordering signal
    available at this layer.
    """
    rows = sorted((b for b in effective.values() if b.kind == kind), key=lambda b: b.mode)
    placeholder = (
        "— choose a frequency band —" if kind == "frequency" else "— choose a magnitude band —"
    )

    def _fmt_money(v: float) -> str:
        if v >= 1_000_000:
            return f"${v / 1_000_000:g}M"
        if v >= 1_000:
            return f"${v / 1_000:g}K"
        return f"${v:g}"

    def _display(b: EffectiveBand) -> str:
        name = b.label.replace("_", " ").title()
        if kind == "frequency":
            # UAT feedback 2026-07-19: show the events/yr semantics at
            # decision time so a probability-worded register label ("Likely")
            # is not bound positionally onto a hot frequency band.
            return f"{name} — {b.low:g} to {b.high:g} events/yr"
        return f"{name} — {_fmt_money(b.low)} to {_fmt_money(b.high)}"

    return [("", placeholder), *((b.label, _display(b)) for b in rows)]


def _parse_bind_form(
    raw: FormData, distinct: dict[str, list[str]]
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Parse the bind form's ``{group}_value_{i}``/``{group}_target_{i}``
    pairs (mirrors ``columns_post``'s ``header_i``/``target_i`` convention)
    into a bindings dict + a per-value field_errors dict (blank/missing
    target -> "must be bound"). Index order matches ``distinct[group]``'s
    order 1:1 because both the GET render and this POST parse call the SAME
    ``distinct_values()`` (sorted, deterministic) — no client-submitted
    index or value string is trusted as a lookup key on its own.

    This is a LOCAL, route-layer pass so the 422 re-render can attach a
    per-field error to the exact unbound row (Task 5's own requirement);
    ``RegisterImportService.set_value_bindings`` still re-validates
    server-side as the authoritative gate (Sec-I2) — this function only
    improves what the user sees when that gate would reject the submission
    for a missing binding.
    """
    bindings: dict[str, dict[str, str]] = {g: {} for g in _BIND_GROUPS}
    field_errors: dict[str, dict[str, str]] = {g: {} for g in _BIND_GROUPS}
    for group in _BIND_GROUPS:
        for i, value in enumerate(distinct.get(group, [])):
            target = str(raw.get(f"{group}_target_{i}", "")).strip()
            if target:
                bindings[group][value] = target
            else:
                field_errors[group][value] = "This value must be bound before continuing."
    return bindings, field_errors


def _expired_response(request: Request, user: User, exc: PreviewExpiredError) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "register_import/import_expired.html",
        {"current_user": user, "flash": None, "message": str(exc)},
        status_code=status.HTTP_409_CONFLICT,
    )


def _upload_fmt(entity_type: str) -> str:
    """Recover ``"xlsx"``/``"csv"`` from a resolved preview row's
    ``entity_type`` (``"register:<fmt>"``) — trivial enough (the format is
    the literal suffix this module's own ``stage_upload`` wrote) that it
    doesn't warrant reaching into ``register_import``'s private helper."""
    return entity_type.rsplit(":", 1)[-1]


# ---- step 1: upload ----------------------------------------------------


@router.get("/register-import", response_class=HTMLResponse)
async def register_import_get(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    profiles = await RegisterImportService(db).list_profiles(user.organization_id)
    return templates.TemplateResponse(
        request,
        "register_import/upload.html",
        {"current_user": user, "flash": None, "profiles": profiles},
    )


@router.post("/register-import")
async def register_import_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    file: UploadFile = File(...),
) -> Response:
    # Sec-I3 layer 1/3: forgeable Content-Length pre-check, BEFORE the body
    # is read at all (mirrors routes/scenario_import.py:88-97).
    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and content_length.isdigit()
        and int(content_length) > MAX_UPLOAD_BYTES
    ):
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")
    data = await file.read()
    # Sec-I3 layer 2/3: post-read check — holds even when Content-Length was
    # absent or understated. Layer 3/3 is stage_upload's own check below.
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")

    svc = RegisterImportService(db)
    profiles = await svc.list_profiles(user.organization_id)

    # Sec-N (Task 1 amendment, applies here): UploadFile.filename is
    # `str | None` — reject None/empty at the route, before staging, so
    # `stage_upload`'s `filename: str` parameter is never handed a None.
    filename = (file.filename or "").strip()
    if not filename:
        return templates.TemplateResponse(
            request,
            "register_import/upload.html",
            {
                "current_user": user,
                "flash": build_flash("a filename is required", "error"),
                "profiles": profiles,
            },
            status_code=422,
        )

    try:
        staged = await svc.stage_upload(
            organization_id=user.organization_id,
            filename=filename,
            content_type=file.content_type,
            data=data,
            user=user,
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "register_import/upload.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "profiles": profiles,
            },
            status_code=422,
        )

    if staged.fmt == "xlsx" and staged.sheet_names is not None and len(staged.sheet_names) > 1:
        return RedirectResponse(
            f"/register-import/{staged.token}/sheet", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        f"/register-import/{staged.token}/columns", status_code=status.HTTP_303_SEE_OTHER
    )


# ---- step 2: sheet pick (xlsx multi-sheet only) -------------------------


@router.get("/register-import/{token}/sheet", response_class=HTMLResponse)
async def register_import_sheet_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = RegisterImportService(db)
    try:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)

    if _upload_fmt(preview.entity_type) != "xlsx":
        raise HTTPException(status_code=422, detail="sheet selection only applies to xlsx uploads")
    try:
        sheet_names = list_sheet_names(preview.csv_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = (preview.state_json or {}).get("filename")
    return templates.TemplateResponse(
        request,
        "register_import/sheet.html",
        {
            "current_user": user,
            "flash": None,
            "token": token,
            "filename": filename,
            "sheet_options": [(s, s) for s in sheet_names],
        },
    )


@router.post("/register-import/{token}/sheet")
async def register_import_sheet_post(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    sheet_name: str = Form(...),
) -> Response:
    svc = RegisterImportService(db)
    try:
        await svc.set_sheet(
            organization_id=user.organization_id, token=token, sheet_name=sheet_name
        )
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
        sheet_names = list_sheet_names(preview.csv_bytes)
        filename = (preview.state_json or {}).get("filename")
        return templates.TemplateResponse(
            request,
            "register_import/sheet.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "token": token,
                "filename": filename,
                "sheet_options": [(s, s) for s in sheet_names],
            },
            status_code=422,
        )
    return RedirectResponse(
        f"/register-import/{token}/columns", status_code=status.HTTP_303_SEE_OTHER
    )


# ---- step 3: column map --------------------------------------------------


@router.get("/register-import/{token}/columns", response_class=HTMLResponse)
async def register_import_columns_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = RegisterImportService(db)
    try:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
        headers = await svc.get_headers(organization_id=user.organization_id, token=token)
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)

    state = preview.state_json or {}
    return templates.TemplateResponse(
        request,
        "register_import/column_map.html",
        {
            "current_user": user,
            "flash": None,
            "token": token,
            "filename": state.get("filename"),
            "headers": headers,
            "column_map": state.get("column_map") or {},
            "targets": _TARGET_OPTIONS,
        },
    )


@router.post("/register-import/{token}/columns")
async def register_import_columns_post(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    raw = await request.form()
    column_map: dict[str, str] = {}
    i = 0
    while f"header_{i}" in raw:
        header = str(raw[f"header_{i}"])
        target = str(raw.get(f"target_{i}", "ignore"))
        column_map[header] = target
        i += 1

    svc = RegisterImportService(db)
    try:
        await svc.set_column_map(
            organization_id=user.organization_id, token=token, column_map=column_map
        )
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
        state = preview.state_json or {}
        return templates.TemplateResponse(
            request,
            "register_import/column_map.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "token": token,
                "filename": state.get("filename"),
                "headers": await svc.get_headers(organization_id=user.organization_id, token=token),
                "column_map": column_map,
                "targets": _TARGET_OPTIONS,
            },
            status_code=422,
        )
    return RedirectResponse(f"/register-import/{token}/bind", status_code=status.HTTP_303_SEE_OTHER)


# ---- step 4: value-bind ---------------------------------------------------


async def _bind_context(
    user: User,
    svc: RegisterImportService,
    band_service: QualitativeBandService,
    *,
    organization_id: uuid.UUID,
    token: str,
    distinct: dict[str, list[str]],
    bindings: dict[str, dict[str, str]],
    field_errors: dict[str, dict[str, str]],
    profile_name: str,
    flash: dict[str, str | None] | None,
) -> dict[str, object]:
    effective = await band_service.effective_bands(organization_id)
    profiles = await svc.list_profiles(organization_id)
    return {
        "current_user": user,
        "flash": flash,
        "token": token,
        "distinct": distinct,
        "bindings": bindings,
        "likelihood_options": _band_options(effective, "frequency"),
        "impact_options": _band_options(effective, "magnitude"),
        "category_options": _CATEGORY_OPTIONS,
        "field_errors": field_errors,
        "profile_name": profile_name,
        "profile_options": [(str(p.id), p.name) for p in profiles],
    }


@router.get("/register-import/{token}/bind", response_class=HTMLResponse)
async def register_import_bind_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = RegisterImportService(db)
    try:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
        distinct = await svc.distinct_values(organization_id=user.organization_id, token=token)
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    band_service = QualitativeBandService(db)
    effective = await band_service.effective_bands(user.organization_id)
    # Pre-selection ONLY via `preselect_bindings` (exact case-insensitive
    # label match — spec §5 / Global Constraints, zero heuristics), then
    # overlaid with whatever this token already has SAVED in `state_json`
    # (e.g. from a prior POST that 422'd on a different field, or from
    # `apply_profile`) — saved state wins per value, since it reflects an
    # actual admin/profile decision rather than a computed guess.
    preselected = preselect_bindings(distinct, effective, ThreatCategory)
    saved = (preview.state_json or {}).get("value_bindings") or {}
    bindings = {
        group: {**preselected.get(group, {}), **(saved.get(group) or {})} for group in _BIND_GROUPS
    }

    # `POST /apply-profile` 303-redirects back here with drift warnings
    # riding `?drift=` query params (this codebase's flash pattern is
    # per-render-dict only — see services/flash.py — so a value that must
    # survive a redirect has to travel some other way; the existing
    # `?saved=1`/`?imported=N` query-flag precedents are the closest fit,
    # extended here to carry the actual warning text since drift warnings
    # are informational strings, not just a count).
    drift_warnings = request.query_params.getlist("drift")
    flash = build_flash("; ".join(drift_warnings), "warning") if drift_warnings else None

    context = await _bind_context(
        user,
        svc,
        band_service,
        organization_id=user.organization_id,
        token=token,
        distinct=distinct,
        bindings=bindings,
        field_errors={g: {} for g in _BIND_GROUPS},
        profile_name="",
        flash=flash,
    )
    return templates.TemplateResponse(request, "register_import/bind.html", context)


@router.post("/register-import/{token}/bind")
async def register_import_bind_post(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    raw = await request.form()
    svc = RegisterImportService(db)
    try:
        distinct = await svc.distinct_values(organization_id=user.organization_id, token=token)
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    bindings, field_errors = _parse_bind_form(raw, distinct)
    profile_name = str(raw.get("profile_name", "")).strip()
    band_service = QualitativeBandService(db)

    async def _rerender(flash: dict[str, str | None], status_code: int) -> HTMLResponse:
        context = await _bind_context(
            user,
            svc,
            band_service,
            organization_id=user.organization_id,
            token=token,
            distinct=distinct,
            bindings=bindings,
            field_errors=field_errors,
            profile_name=profile_name,
            flash=flash,
        )
        return templates.TemplateResponse(
            request, "register_import/bind.html", context, status_code=status_code
        )

    # Unbound value(s) -> 422 re-render with per-field errors, WITHOUT a
    # round-trip through the service (Task 5's own requirement — the
    # service's own rejection message is a single string, not attributable
    # to one row; see `_parse_bind_form`'s docstring).
    if any(field_errors[g] for g in _BIND_GROUPS):
        return await _rerender(
            build_flash("some values still need to be bound before you can continue", "error"),
            422,
        )

    try:
        await svc.set_value_bindings(
            organization_id=user.organization_id, token=token, bindings=bindings
        )
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        return await _rerender(build_flash(str(exc), "error"), 422)

    if profile_name:
        try:
            await svc.save_profile(
                organization_id=user.organization_id, name=profile_name, token=token, user=user
            )
        except ValidationError as exc:
            # Bindings ARE already persisted at this point (set_value_bindings
            # above succeeded) — only the profile-save half failed (duplicate
            # name is the only realistic case reachable from this form; see
            # save_profile's own name-length/non-empty checks, which the
            # "profile_name" text input can't violate via normal use).
            # Re-rendering still shows the just-bound selections, not a
            # blank form.
            return await _rerender(build_flash(str(exc), "error"), 422)

    return RedirectResponse(
        f"/register-import/{token}/preview", status_code=status.HTTP_303_SEE_OTHER
    )


# ---- binding profiles: apply -----------------------------------------------


@router.post("/register-import/{token}/apply-profile")
async def register_import_apply_profile_post(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    profile_id: uuid.UUID = Form(...),
) -> Response:
    svc = RegisterImportService(db)
    try:
        warnings = await svc.apply_profile(
            organization_id=user.organization_id, token=token, profile_id=profile_id
        )
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    location = f"/register-import/{token}/bind"
    if warnings:
        location = f"{location}?{urlencode([('drift', w) for w in warnings])}"
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


# ---- step 5: preview + convert (Task 6) -------------------------------


def _classification_rows(classified: ClassifiedRows) -> list[dict[str, object]]:
    """Flatten a :class:`ClassifiedRows` into ``preview_table`` rows, one per
    source row across all four buckets, sorted by the file's own row order.

    Vocabulary glossary (plan Task 6 amendment, Spec-R2-NTH): the service's
    bucket names (``would_create``/``parked``/``duplicates``/``errors``)
    render here under the badge keys ``create``/``parked``/``duplicate``/
    ``error`` that ``macros/import_preview.html``'s ``_action_badge`` style
    map understands.
    """
    rows: list[dict[str, object]] = []
    for row in classified.would_create:
        rows.append({"line": row.source_row, "title": row.title, "note": "", "action": "create"})
    for pr in classified.parked:
        note = (
            "blank likelihood/impact cell"
            if pr.reason == "blank_cells"
            else "category parked / out of scope"
        )
        rows.append({"line": pr.source_row, "title": pr.title, "note": note, "action": "parked"})
    for dup in classified.duplicates:
        rows.append(
            {
                "line": dup.source_row,
                "title": dup.title,
                "note": f"duplicate — {dup.reason}",
                "action": "duplicate",
            }
        )
    for err in classified.errors:
        rows.append({"line": err.source_row, "title": "", "note": err.message, "action": "error"})
    rows.sort(key=lambda r: r["line"])  # type: ignore[arg-type,return-value]
    return rows


def _mapping_version_rows(versions: dict[str, object], layer: str) -> list[dict[str, object]]:
    """``{"kind:label": version, ...}`` -> sorted ``preview_table`` rows for
    one ``mapping_versions()`` layer ("canonical" | "org")."""
    layer_versions = versions.get(layer)
    if not isinstance(layer_versions, dict):
        return []
    return [{"band": band, "version": version} for band, version in sorted(layer_versions.items())]


@router.get("/register-import/{token}/preview", response_class=HTMLResponse)
async def register_import_preview_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = RegisterImportService(db)
    try:
        preview_row = await svc.get_staged(organization_id=user.organization_id, token=token)
        classified = await svc.preview(organization_id=user.organization_id, token=token)
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = (preview_row.state_json or {}).get("filename")
    return templates.TemplateResponse(
        request,
        "register_import/preview.html",
        {
            "current_user": user,
            "flash": None,
            "token": token,
            "filename": filename,
            "rows": _classification_rows(classified),
            "would_create_count": len(classified.would_create),
            "parked_count": len(classified.parked),
            "duplicate_count": len(classified.duplicates),
            "error_count": len(classified.errors),
            "sl_note": SL_NOTE,
        },
    )


@router.post("/register-import/{token}/convert")
async def register_import_convert_post(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = RegisterImportService(db)
    try:
        report: ConversionReport = await svc.apply(
            organization_id=user.organization_id,
            user=user,
            token=token,
            ip_address=client_ip(request),
        )
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    created_rows = [
        {"scenario_id": str(c.scenario_id), "source_row": c.source_row, "title": c.title}
        for c in report.created
    ]
    parked_rows = [
        {
            "line": pr.source_row,
            "title": pr.title,
            "note": (
                "blank likelihood/impact cell"
                if pr.reason == "blank_cells"
                else "category parked / out of scope"
            ),
        }
        for pr in report.parked
    ]
    skipped_rows = [
        {"line": d.source_row, "title": d.title, "reason": f"duplicate — {d.reason}"}
        for d in report.skipped_duplicates
    ]
    error_rows = [{"line": e.source_row, "message": e.message} for e in report.errors]

    return templates.TemplateResponse(
        request,
        "register_import/report.html",
        {
            "current_user": user,
            "flash": None,
            "report": report,
            "created_rows": created_rows,
            "parked_rows": parked_rows,
            "skipped_rows": skipped_rows,
            "error_rows": error_rows,
            "canonical_version_rows": _mapping_version_rows(report.mapping_versions, "canonical"),
            "org_version_rows": _mapping_version_rows(report.mapping_versions, "org"),
        },
    )


@router.post("/register-import/profiles/{profile_id}/delete")
async def delete_profile(
    request: Request,
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    """Delete a saved binding profile (mirror of the band-CRUD delete shape)."""
    svc = RegisterImportService(db)
    try:
        await svc.delete_profile(
            organization_id=user.organization_id,
            profile_id=profile_id,
            user=user,
            ip_address=client_ip(request),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url="/register-import", status_code=303)
