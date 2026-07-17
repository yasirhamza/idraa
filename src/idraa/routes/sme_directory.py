"""SME directory routes. See spec §8.1.

9 admin routes + 1 analyst-request route. Audit-event emission lands in
T10 — this module wires the service layer to HTTP only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import UserRole
from idraa.models.sme import SubjectMatterExpert
from idraa.models.user import User
from idraa.routes.deps import get_db, require_role
from idraa.schemas.sme import SMECreate, SMEDirectoryEntry, SMERequest, SMEUpdate
from idraa.services import sme_directory as svc
from idraa.services.audit import AuditWriter

router = APIRouter()


@router.get("/sme-directory")
async def list_smes(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> list[dict[str, Any]]:
    return await svc.list_for_dropdown(db, user.organization_id)


@router.post(
    "/sme-directory/new",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
)
async def admin_create(
    data: SMECreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> SubjectMatterExpert:
    try:
        return await svc.create(
            db,
            data,
            organization_id=user.organization_id,
            actor_id=user.id,
        )
    except svc.SMEArchivedEmailCollisionError as e:
        raise HTTPException(422, f"Archived SME with this email exists: {e}") from e
    except svc.SMEAlreadyExistsError as e:
        raise HTTPException(422, f"SME already exists: {e}") from e


@router.post(
    "/scenarios/wizard/request-sme",
    status_code=status.HTTP_201_CREATED,
    response_model=SMEDirectoryEntry,
)
async def analyst_request(
    data: SMERequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> SubjectMatterExpert:
    # Per plan-gate I-Arch-3: FastAPI serializes the returned ORM row via
    # ``response_model=SMEDirectoryEntry`` + ``from_attributes=True`` on the
    # model. Do NOT add an explicit ``SMEDirectoryEntry.model_validate(sme)``
    # call here — it's redundant.
    try:
        return await svc.request(
            db,
            data,
            organization_id=user.organization_id,
            actor_id=user.id,
        )
    except svc.SMEArchivedEmailCollisionError as e:
        raise HTTPException(422, str(e)) from e
    except svc.SMEAlreadyExistsError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/sme-directory/{sme_id}", response_model=None)
async def get_sme(
    sme_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> SubjectMatterExpert:
    try:
        return await svc.get_sme_for_org(
            db,
            sme_id,
            user.organization_id,
            allow_archived=True,
        )
    except svc.SMENotFoundError as e:
        raise HTTPException(404, "SME not found") from e


@router.post("/sme-directory/{sme_id}/edit", response_model=None)
async def edit_sme(
    sme_id: UUID,
    data: SMEUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> SubjectMatterExpert:
    try:
        return await svc.update(
            db,
            sme_id,
            data,
            organization_id=user.organization_id,
            actor_id=user.id,
        )
    except svc.SMENotFoundError as e:
        raise HTTPException(404, "SME not found") from e
    except svc.SMESystemOwnedImmutableError as e:
        raise HTTPException(422, str(e)) from e


@router.post(
    "/sme-directory/{sme_id}/archive",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def archive_sme(
    sme_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> None:
    try:
        await svc.archive(
            db,
            sme_id,
            organization_id=user.organization_id,
            actor_id=user.id,
        )
    except svc.SMENotFoundError as e:
        raise HTTPException(404, "SME not found") from e
    except svc.SMESystemOwnedImmutableError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/sme-directory/{sme_id}/unarchive", response_model=None)
async def unarchive_sme(
    sme_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> SubjectMatterExpert:
    try:
        # Sec-23 R3 read-before-mutate: T10 threads prior_at/prior_by into
        # the audit-event payload (Sec-23/Sec-16 PR1). The service returns
        # the values it read BEFORE clearing archived_at/by so the audit row
        # captures who archived this SME originally — even though that
        # information is gone from the row itself after unarchive.
        sme, prior_at, prior_by = await svc.unarchive(
            db,
            sme_id,
            organization_id=user.organization_id,
            actor_id=user.id,
        )
    except svc.SMENotFoundError as e:
        raise HTTPException(404, "SME not found") from e
    except svc.SMESystemOwnedImmutableError as e:
        raise HTTPException(422, str(e)) from e
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="sme",
        entity_id=sme.id,
        action="sme.unarchived",
        changes={
            "sme_id": str(sme.id),
            "unarchived_by": str(user.id),
            "prior_archived_at": prior_at,  # AuditWriter coerces datetime → isoformat
            "prior_archived_by": prior_by,  # AuditWriter coerces UUID → str
        },
        user_id=user.id,
    )
    return sme
