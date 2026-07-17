"""First-time setup wizard. Creates the first Organization + admin User, logs in.

Behaviour summary:

- GET ``/setup`` renders the wizard form if no user exists; otherwise 303s to /.
- POST ``/setup`` creates the Organization, the admin User, an AuthSession,
  three AuditLog rows (org-create, user-create, login), sets the signed
  ``idraa_session`` cookie, and 303-redirects to ``/``.
- The ``setup_guard`` middleware in ``app.py`` is what forces un-seeded
  deployments to /setup; this router only handles the /setup path itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models.enums import IndustryType, OrganizationSize, UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    create_session,
    hash_password,
    set_session_cookie,
)

router = APIRouter()


async def _has_any_user(db: AsyncSession) -> bool:
    row = await db.execute(select(func.count()).select_from(User))
    return (row.scalar_one() or 0) > 0


@router.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if await _has_any_user(db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "setup/wizard.html",
        {
            "current_user": None,
            "flash": None,
            "form": {},
            "error": None,
            "industries": list(IndustryType),
            "sizes": list(OrganizationSize),
        },
    )


@router.post("/setup")
async def setup_post(
    request: Request,
    org_name: str = Form(..., max_length=255),
    industry_type: str = Form(..., max_length=64),
    organization_size: str = Form(..., max_length=64),
    email: str = Form(..., max_length=255),
    full_name: str = Form(..., max_length=255),
    password: str = Form(..., min_length=8, max_length=1024),
    db: AsyncSession = Depends(get_db),
) -> Response:
    # Bootstrap race: two concurrent setup POSTs could both pass _has_any_user
    # before either commits, minting two admin users. SERIALIZABLE isolation
    # makes SQLite's BEGIN IMMEDIATE fire (the second POST blocks until the
    # first commits, then re-reads _has_any_user and sees count > 0). Postgres
    # raises SerializationFailure on conflict — surfaces as a 500 for the
    # loser, which is the correct "try again" signal for a bootstrap flow.
    await db.connection(execution_options={"isolation_level": "SERIALIZABLE"})

    if await _has_any_user(db):
        return RedirectResponse("/", status_code=303)

    try:
        industry = IndustryType(industry_type)
        size = OrganizationSize(organization_size)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "setup/wizard.html",
            {
                "current_user": None,
                "flash": None,
                "form": {
                    "org_name": org_name,
                    "industry_type": industry_type,
                    "organization_size": organization_size,
                    "email": email,
                    "full_name": full_name,
                },
                "error": "Invalid industry or size",
                "industries": list(IndustryType),
                "sizes": list(OrganizationSize),
            },
            status_code=400,
        )

    audit = AuditWriter(db)

    org = Organization(name=org_name, industry_type=industry, organization_size=size)
    db.add(org)
    await db.flush()
    await audit.log(
        organization_id=org.id,
        entity_type="organization",
        entity_id=org.id,
        action="create",
        changes={"name": [None, org_name]},
        user_id=None,
        ip_address=client_ip(request),
    )

    # Normalize email with .lower().strip() to stay consistent with
    # load_user_by_email in services/auth.py — a trailing space on the form
    # input would otherwise store "a@b.c " but look up as "a@b.c" and the
    # user could never log in.
    normalized_email = email.lower().strip()

    user = User(
        organization_id=org.id,
        email=normalized_email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await audit.log(
        organization_id=org.id,
        entity_type="user",
        entity_id=user.id,
        action="create",
        changes={"email": [None, normalized_email]},
        user_id=user.id,
        ip_address=client_ip(request),
    )

    sess = await create_session(db, user.id, ip=client_ip(request))
    await audit.log(
        organization_id=org.id,
        entity_type="session",
        entity_id=sess.id,
        action="login",
        changes={},
        user_id=user.id,
        ip_address=client_ip(request),
    )

    # Transaction commit owned by get_db dependency (see deps.py::get_db + db.py::get_session).

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, sess.id)
    return response
