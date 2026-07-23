"""Users admin routes — list + invite + edit.

Every route requires ``UserRole.ADMIN``. The 1.1.8 401->303 /login handler
in ``app.py::_auth_redirect_handler`` handles un-authenticated callers;
non-admin callers fall through to the default 403 JSON handler (a redirect
would loop signed-in-but-wrong-role users back and forth).

Transaction commit is owned by the ``get_db`` dependency (see
``routes/deps.py::get_db`` + ``db.py::get_session`` — same 1.1.5.a pattern
used by /setup, /login, and /organization). Handlers here do NOT call
``await db.commit()`` directly.

Self-edit + last-admin guards (plan 1.1.5 M3 carryover): an admin cannot
demote or disable themselves (self-lockout risk), and the last active
admin cannot be demoted or disabled by ANY caller (would leave the org
with no one who can manage users). Both return 400 because the caller is
authorized — the operation itself is semantically invalid.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.errors import UserDeleteError
from idraa.formatting import utc_isoformat
from idraa.models.enums import StepUpCategory, UserRole
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_role, require_step_up
from idraa.services.audit import AuditWriter, log_bulk_export
from idraa.services.auth import is_locked, reset_login_throttle, revoke_user_sessions
from idraa.services.mfa_enrollment import reset_user_mfa
from idraa.services.org import require_sole_org
from idraa.services.users import (
    _authored_count,
    delete_user,
    get_user,
    invite_user,
    list_users,
)
from idraa.utils.csv_export import csv_response

router = APIRouter()


def _edit_error(request: Request, me: User, user: User, error: str) -> Response:
    """Re-render ``users/edit.html`` with an error banner and HTTP 400.

    Replaces raw ``HTTPException(400, detail=...)`` in ``edit_post`` for the
    form-validation 400s (unknown role, self-lockout guards, last-admin
    guard). Those paths used to fall through to ``_auth_redirect_handler``
    and emit raw JSON — ugly for a form POST. Mirrors the ``invite.html``
    pattern: a single ``{{ error }}`` string inside an ``alert-error`` div.

    The 404 path (``get_user`` returned None) is NOT routed through this
    helper — a missing user id is a hard error, not a re-render case.
    """
    return templates.TemplateResponse(
        request,
        "users/edit.html",
        {
            "current_user": me,
            "flash": None,
            "user": user,
            "roles": list(UserRole),
            "error": error,
        },
        status_code=400,
    )


@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    org = await require_sole_org(db)
    users = await list_users(db, org.id)
    # Per-user delete eligibility for the list view-model: a user is hard-
    # deletable only if they authored no business entities AND aren't the
    # current actor. The service re-checks all guards on POST (this flag is a
    # UI affordance, not the authorization gate).
    can_delete: dict[str, bool] = {}
    for u in users:
        authored = await _authored_count(db, u.id, org.id)
        can_delete[str(u.id)] = authored == 0 and u.id != me.id
    locked: dict[str, bool] = {str(u.id): is_locked(u) for u in users}
    return templates.TemplateResponse(
        request,
        "users/list.html",
        {
            "current_user": me,
            "flash": None,
            "users": users,
            "roles": list(UserRole),
            "can_delete": can_delete,
            "locked": locked,
        },
    )


@router.get("/users/export.csv", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))])
async def users_export_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    """Stream all users for the current org as a CSV download (admin-only).

    Plan-gate Arch-2: registered BEFORE /users/{user_id}/edit.
    Plan-gate Sec-3: scoped by org from require_sole_org.
    Admin-only: user list includes contact details + role assignments.
    """
    org = await require_sole_org(db)
    users = await list_users(db, org.id)
    # #304: bulk egress audit row — user-list export is PII egress, the most
    # audit-sensitive of the bulk endpoints.
    await log_bulk_export(
        db,
        organization_id=org.id,
        entity_type="user",
        fmt="csv",
        count=len(users),
        user_id=me.id,
        ip_address=client_ip(request),
    )
    header = ["email", "role", "is_active", "created_at"]
    rows = (
        (
            u.email,
            u.role.value if hasattr(u.role, "value") else str(u.role),
            "true" if u.is_active else "false",
            utc_isoformat(u.created_at),
        )
        for u in users
    )
    return csv_response(filename="users.csv", header=header, rows_iter=rows)


@router.get("/users/invite", response_class=HTMLResponse)
async def invite_get(
    request: Request,
    me: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "users/invite.html",
        {"current_user": me, "flash": None, "roles": list(UserRole), "error": None},
    )


@router.post("/users/invite", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))])
async def invite_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
    email: str = Form(..., max_length=255),
    full_name: str = Form(..., max_length=255),
    role: str = Form(..., max_length=64),
    password: str = Form(..., min_length=8, max_length=1024),
) -> Response:
    org = await require_sole_org(db)
    try:
        role_enum = UserRole(role)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "users/invite.html",
            {
                "current_user": me,
                "flash": None,
                "roles": list(UserRole),
                "error": f"Unknown role: {role}",
            },
            status_code=400,
        )
    try:
        user = await invite_user(
            db,
            org_id=org.id,
            email=email,
            full_name=full_name,
            role=role_enum,
            password=password,
        )
        await AuditWriter(db).log(
            organization_id=org.id,
            entity_type="user",
            entity_id=user.id,
            action="create",
            changes={"email": [None, user.email], "role": [None, role_enum.value]},
            user_id=me.id,
            ip_address=client_ip(request),
        )
    except IntegrityError:
        # Duplicate email within the same org hits the uq_users_org_email
        # unique constraint at flush time. Rollback is required: the failed
        # flush leaves the session in a state where get_db's auto-commit
        # on exit would also raise. Rolling back puts us on a clean
        # transaction so the subsequent TemplateResponse path is benign.
        await db.rollback()
        # Normalize the echoed email the same way invite_user stores it,
        # so the displayed value matches what the constraint matched on.
        existing = email.lower().strip()
        return templates.TemplateResponse(
            request,
            "users/invite.html",
            {
                "current_user": me,
                "flash": None,
                "roles": list(UserRole),
                "error": f"A user with email {existing} already exists",
            },
            status_code=400,
        )
    # Transaction commit owned by get_db dependency.
    return RedirectResponse("/users", status_code=303)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_get(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    user = await get_user(db, user_id, me.organization_id)
    if user is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "users/edit.html",
        {
            "current_user": me,
            "flash": None,
            "user": user,
            "roles": list(UserRole),
            "mfa_reset_done": request.query_params.get("mfa_reset") == "1",
            "locked": is_locked(user),
            "unlocked_done": request.query_params.get("unlocked") == "1",
        },
    )


@router.post("/users/{user_id}/edit", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))])
async def edit_post(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
    role: str = Form(..., max_length=64),
    is_active: str | None = Form(None),
) -> Response:
    user = await get_user(db, user_id, me.organization_id)
    if user is None:
        raise HTTPException(404)

    # Parse the role enum FIRST so the guards below operate on a validated
    # value. Consistent with invite_post's try/except on the same coercion.
    try:
        new_role = UserRole(role)
    except ValueError:
        return _edit_error(request, me, user, f"Unknown role: {role}")

    # Checkbox semantics: HTML sends "on" when checked, omits the key when
    # unchecked. Presence + non-empty string -> active.
    new_active = is_active is not None and is_active != ""

    # Self-edit guard: an admin cannot demote or deactivate themselves.
    # Rationale: prevents accidental self-lockout. If an admin genuinely
    # needs to demote/disable their account, a SECOND admin must do it.
    if me.id == user.id:
        if user.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
            return _edit_error(request, me, user, "You cannot demote yourself from admin")
        if user.is_active and not new_active:
            return _edit_error(request, me, user, "You cannot deactivate yourself")

    # Last-admin guard: demoting the only active admin leaves the org with
    # no one who can manage users. Refuse. (Deactivating the last active
    # admin is also caught here because deactivation is a stricter form of
    # demotion for admin-management purposes.)
    if user.role == UserRole.ADMIN and (
        new_role != UserRole.ADMIN or (user.is_active and not new_active)
    ):
        active_admin_count = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == user.organization_id,
                User.role == UserRole.ADMIN,
                User.is_active == True,  # noqa: E712 — SQLAlchemy column comparison requires ==
            )
        )
        # User is CURRENTLY an active admin (we're in this branch because
        # user.role == ADMIN). If they're the only one, refuse.
        if active_admin_count is not None and active_admin_count <= 1:
            return _edit_error(
                request,
                me,
                user,
                "Cannot demote or deactivate the last active admin",
            )

    changes: dict[str, list[object]] = {}
    if user.role != new_role:
        changes["role"] = [user.role.value, new_role.value]
        user.role = new_role
    if user.is_active != new_active:
        changes["is_active"] = [user.is_active, new_active]
        user.is_active = new_active
        if not new_active:  # idraa#80 L13 — deactivation kills live sessions
            revoked = await revoke_user_sessions(db, user.id)
            await AuditWriter(db).log(
                organization_id=user.organization_id,
                entity_type="user",
                entity_id=user.id,
                action="user.sessions_revoked",
                changes={"count": revoked, "via": "ui"},
                user_id=me.id,
                ip_address=client_ip(request),
            )
    if changes:
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="update",
            changes=changes,
            user_id=me.id,
            ip_address=client_ip(request),
        )
    # Transaction commit owned by get_db dependency.
    return RedirectResponse("/users", status_code=303)


@router.post(
    "/users/{user_id}/set-active", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))]
)
async def set_active_post(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
    active: str = Form(..., max_length=8),
) -> Response:
    """Per-row Activate / Deactivate toggle for the users list (#296).

    A thin dedicated endpoint (the edit POST requires a ``role`` field, which
    a one-click list toggle shouldn't have to round-trip). ``active`` is
    ``"1"``/``"0"`` (truthy/falsey). Reuses the same self-lockout + last-admin
    guards as ``edit_post``: an admin can't deactivate themselves, and the
    last active admin can't be deactivated by anyone (returns 400 — caller is
    authorized, the operation is semantically invalid).
    """
    user = await get_user(db, user_id, me.organization_id)
    if user is None:
        raise HTTPException(404)

    new_active = active not in ("", "0", "false", "False")

    if user.is_active == new_active:
        return RedirectResponse("/users", status_code=303)  # no-op

    # Deactivation guards (activation is always safe).
    if not new_active:
        if me.id == user.id:
            raise HTTPException(status_code=400, detail="You cannot deactivate yourself")
        if user.role == UserRole.ADMIN:
            active_admin_count = await db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.organization_id == user.organization_id,
                    User.role == UserRole.ADMIN,
                    User.is_active == True,  # noqa: E712 — SQLAlchemy column comparison requires ==
                )
            )
            if active_admin_count is not None and active_admin_count <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot deactivate the last active admin",
                )

    changes: dict[str, list[object]] = {"is_active": [user.is_active, new_active]}
    user.is_active = new_active
    if not new_active:  # idraa#80 L13 — deactivation kills live sessions
        revoked = await revoke_user_sessions(db, user.id)
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="user.sessions_revoked",
            changes={"count": revoked, "via": "ui"},
            user_id=me.id,
            ip_address=client_ip(request),
        )
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="update",
        changes=changes,
        user_id=me.id,
        ip_address=client_ip(request),
    )
    # Transaction commit owned by get_db dependency.
    return RedirectResponse("/users", status_code=303)


@router.post(
    "/users/{user_id}/delete", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))]
)
async def delete_post(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
    confirm: str | None = Form(default=None),
) -> Response:
    """Conditional hard-delete a user (#296).

    RBAC: ``require_role(ADMIN)`` rejects non-admins with 403 at the
    dependency. CSRF: middleware-validated via the ``_csrf`` form field.
    Mandatory ``confirm`` -> 400 if absent/falsey.

    Error mapping: service returns ``False`` (cross-org / missing) -> 404;
    ``UserHasHistoryError`` / ``UserDeleteError`` -> 409 with the guidance
    message in the body. Success -> 303 redirect to ``/users?deleted=1``.

    NOTE: ``delete_user`` commits internally (delete + audit row must land
    atomically), so this handler does not rely on the ``get_db`` auto-commit
    — that teardown commit is a harmless no-op once the service has committed.
    """
    if confirm is None or confirm in ("", "0", "false", "False"):
        raise HTTPException(status_code=400, detail="confirm: missing or falsey")

    try:
        deleted = await delete_user(
            db,
            user_id=user_id,
            actor_id=me.id,
            org_id=me.organization_id,
        )
    except UserDeleteError as exc:
        # Covers UserHasHistoryError (subclass) too — both map to 409.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not deleted:
        raise HTTPException(404)

    return RedirectResponse("/users?deleted=1", status_code=303)


@router.post(
    "/users/{user_id}/reset-mfa", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))]
)
async def reset_mfa_post(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
    confirm: str | None = Form(default=None),
) -> Response:
    """Audited admin factor-reset (design §Recovery).

    Clears the target's passkeys + TOTP + recovery codes, clears
    mfa_enrolled_at (interstitial re-traps at next login under
    policy=required), and revokes the target's live sessions. Never
    authenticates the admin as the target. SELF-reset is allowed — it
    revokes the admin's own sessions too, landing them on /login to
    re-enroll.
    """
    if confirm is None or confirm in ("", "0", "false", "False"):
        raise HTTPException(status_code=400, detail="confirm: missing or falsey")
    user = await get_user(db, user_id, me.organization_id)
    if user is None:
        raise HTTPException(404)
    counts = await reset_user_mfa(db, user)
    revoked = await revoke_user_sessions(db, user.id)
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="user.mfa_admin_reset",
        changes={"factors_cleared": counts, "via": "ui"},
        user_id=me.id,
        ip_address=client_ip(request),
    )
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="user.sessions_revoked",
        changes={"count": revoked, "via": "ui"},
        user_id=me.id,
        ip_address=client_ip(request),
    )
    return RedirectResponse(f"/users/{user.id}/edit?mfa_reset=1", status_code=303)


@router.post(
    "/users/{user_id}/unlock", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))]
)
async def unlock_user_post(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
    confirm: str | None = Form(default=None),
) -> Response:
    """Audited admin unlock of a lockout-throttled account (idraa#81).

    Clears the per-account throttle (locked_until + failed_login_count). Per-
    source (IP) blocks are separate and auto-expire; not cleared here.
    """
    if confirm is None or confirm in ("", "0", "false", "False"):
        raise HTTPException(status_code=400, detail="confirm: missing or falsey")
    user = await get_user(db, user_id, me.organization_id)
    if user is None:
        raise HTTPException(404)
    reset_login_throttle(user)
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="user.login_unlocked",
        changes={"via": "ui"},
        user_id=me.id,
        ip_address=client_ip(request),
    )
    return RedirectResponse(f"/users/{user.id}/edit?unlocked=1", status_code=303)
