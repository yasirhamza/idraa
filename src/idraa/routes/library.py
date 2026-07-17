"""GET /library — analyst+ browse UI with HTMX-driven filter sidebar.

Spec §8.1 §8.3.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.config import get_settings
from idraa.errors import (
    LibraryEntryDeleteRefusedError,
    LibraryEntryNotFoundError,
)
from idraa.models.attack import AttackTechnique, ScenarioLibraryEntryAttackMapping
from idraa.models.enums import (
    AssetClass,
    IndustrySubSector,
    IndustryType,
    ThreatActorType,
    ThreatCategory,
    UserRole,
)
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo
from idraa.routes.deps import client_ip, get_db, require_role, require_user
from idraa.services.audit import log_bulk_export
from idraa.services.library_bundle_export import export_bundle_response
from idraa.services.scenario_control_recommendations import recommended_controls_for
from idraa.services.scenario_library import (
    BrowseFilters,
    ScenarioLibraryService,
    available_facets,
)
from idraa.utils.csv_export import csv_response

router = APIRouter(tags=["library"])

# F14 carryover A: hoist enum valid-value sets at module scope so
# _parse_browse_filters doesn't rebuild them on every request.
_VALID_VALUES: dict[type[StrEnum], frozenset[str]] = {
    ThreatActorType: frozenset(m.value for m in ThreatActorType),
    ThreatCategory: frozenset(m.value for m in ThreatCategory),
    AssetClass: frozenset(m.value for m in AssetClass),
    IndustryType: frozenset(m.value for m in IndustryType),
    IndustrySubSector: frozenset(m.value for m in IndustrySubSector),
}


def _parse_browse_filters(request: Request) -> BrowseFilters:
    """Parse the GET querystring into a BrowseFilters DTO."""
    qp = request.query_params

    def _multi(key: str, enum_cls: type[StrEnum]) -> list[StrEnum]:
        valid = _VALID_VALUES[enum_cls]
        return [enum_cls(v) for v in qp.getlist(key) if v in valid]

    return BrowseFilters(
        threat_actor_types=_multi("threat_actor_type", ThreatActorType),  # type: ignore[arg-type]
        threat_event_types=_multi("threat_event_type", ThreatCategory),  # type: ignore[arg-type]
        asset_classes=_multi("asset_class", AssetClass),  # type: ignore[arg-type]
        applicable_industries=_multi("industry", IndustryType),  # type: ignore[arg-type]
        applicable_sub_sectors=_multi("sub_sector", IndustrySubSector),  # type: ignore[arg-type]
        # F14 carryover D: truncate to 200 chars (belt-and-suspenders; repo
        # also escapes LIKE wildcards).
        search_text=qp.get("q", "")[:200] or None,
        # P3 Task 6: provenance filter — allowlist to the two valid values so a
        # crafted ?source=foo can't inject an arbitrary string into the SQL
        # equality (it falls through to None = no narrowing).
        source=(qp.get("source") if qp.get("source") in ("seed", "imported") else None),
    )


@router.get("/library", response_class=HTMLResponse)
async def get_library(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(
        require_role(
            UserRole.VIEWER,
            UserRole.ANALYST,
            UserRole.REVIEWER,
            UserRole.ADMIN,
        )
    ),
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    """Browse the scenario library — viewer+ per §8.2."""
    organization = await db.get(Organization, user.organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    # F14 carryover E: capture settings once; avoids a duplicate get_settings() call.
    page_size = get_settings().list_page_size
    filters = _parse_browse_filters(request)
    svc = ScenarioLibraryService(db)
    page_data = await svc.list_browseable(
        filters=filters,
        page=page,
    )
    facets = await available_facets(db)
    return templates.TemplateResponse(
        request,
        "library/browse.html",
        {
            "current_user": user,
            "flash": None,
            "organization": organization,
            "entries": page_data.entries,
            "page": page_data.page,
            "total": page_data.total,
            "filters": filters,
            "page_size": page_size,
            "facets": facets,
        },
    )


@router.get("/library/export.csv")
async def library_export_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(
        require_role(
            UserRole.VIEWER,
            UserRole.ANALYST,
            UserRole.REVIEWER,
            UserRole.ADMIN,
        )
    ),
) -> Response:
    """Stream all published library entries as a CSV download.

    Plan-gate Arch-2: registered BEFORE /library/entries/{entry_id} so
    "export.csv" is matched as a literal path.
    Plan-gate Sec-3: library entries are not org-specific (shared canonical
    data); no org filter needed. Access is still gated by authentication
    (viewer+ role).
    """
    svc = ScenarioLibraryService(db)
    entries = await svc.repo.list_published(limit=10_000)
    # #304: bulk egress audit row (canonical catalog, but the downloading
    # org/user/ip is what the row records).
    await log_bulk_export(
        db,
        organization_id=user.organization_id,
        entity_type="library_bundle",
        fmt="csv",
        count=len(entries),
        user_id=user.id,
        ip_address=client_ip(request),
    )
    header = ["id", "name", "threat_event_type", "threat_actor_type", "asset_class", "status"]
    rows = (
        (
            str(e.id),
            e.name,
            e.threat_event_type.value
            if hasattr(e.threat_event_type, "value")
            else str(e.threat_event_type),
            e.threat_actor_type.value
            if hasattr(e.threat_actor_type, "value")
            else str(e.threat_actor_type),
            e.asset_class.value if hasattr(e.asset_class, "value") else str(e.asset_class),
            e.status if isinstance(e.status, str) else str(e.status),
        )
        for e in entries
    )
    return csv_response(filename="library.csv", header=header, rows_iter=rows)


@router.get("/library/export")
async def library_export(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    """Download the full published catalog as a re-importable JSON bundle.

    P3: a LITERAL path registered alongside ``/library/export.csv`` and BEFORE
    ``/library/entries/{entry_id}`` so it is matched literally, not captured by
    the typed entry-id param. ``require_user`` — any authenticated user may
    download the public catalog (read of shared canonical data); import is
    admin-only (a separate router). The bundle is the exact LibraryEntrySeed
    shape so a downloaded file re-imports cleanly.
    """
    entries = await ScenarioLibraryRepo(db).list_published(limit=10_000, offset=0)
    # #304: bulk egress audit row.
    await log_bulk_export(
        db,
        organization_id=user.organization_id,
        entity_type="library_bundle",
        fmt="json",
        count=len(entries),
        user_id=user.id,
        ip_address=client_ip(request),
    )
    return export_bundle_response(entries, filename="scenario-library.json")


@router.get("/library/entries/{entry_id}/export")
async def library_export_one(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    """Download one entry (latest published version) as a JSON bundle.

    Registered BEFORE the bare ``/library/entries/{entry_id}`` detail route so
    the ``/export`` suffix is matched as its own handler. ``require_user`` —
    same read-of-public-catalog posture as the bulk export.
    """
    entry = await ScenarioLibraryRepo(db).get_latest_published_by_id(entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="library entry not found")
    return export_bundle_response([entry], filename=f"library-entry-{entry_id}.json")


@router.get("/library/entries/{entry_id}", response_class=HTMLResponse)
async def get_library_entry(
    entry_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(
        require_role(
            UserRole.VIEWER,
            UserRole.ANALYST,
            UserRole.REVIEWER,
            UserRole.ADMIN,
        )
    ),
    version: int | None = None,
) -> HTMLResponse:
    """Detail page for a single library entry."""
    svc = ScenarioLibraryService(db)
    entry = await svc.repo.get_by_id_version(entry_id, version) if version else None
    if entry is None:
        # Latest published if no version specified
        entry = await svc.repo.get_latest_published_by_id(entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="library entry not found",
        )
    organization = await db.get(Organization, user.organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    override = await svc.repo.get_override(user.organization_id, entry_id)
    versions_list = await svc.repo.list_versions(entry_id)
    recommendations = await recommended_controls_for(db, entry=entry, org_id=user.organization_id)
    # Sec-N2: gate the adopt button via the UserRole enum (passed as can_adopt),
    # not a hardcoded role.value string-literal list in the template.
    can_adopt = user.role in (UserRole.ADMIN, UserRole.ANALYST)
    # #475 P2 Task 6: curated technique claims for this exact entry snapshot
    # (composite key mirrors ScenarioLibraryOverride) — ordered by technique_id
    # for a stable render order.
    attack_mappings = (
        (
            await db.execute(
                select(ScenarioLibraryEntryAttackMapping)
                .join(
                    AttackTechnique,
                    ScenarioLibraryEntryAttackMapping.technique_id == AttackTechnique.id,
                )
                .where(
                    ScenarioLibraryEntryAttackMapping.library_entry_id == entry.id,
                    ScenarioLibraryEntryAttackMapping.library_entry_version == entry.version,
                )
                .order_by(AttackTechnique.technique_id)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "library/entry_detail.html",
        {
            "current_user": user,
            "flash": None,
            "organization": organization,
            "entry": entry,
            "override": override,
            "versions": versions_list,
            "recommendations": recommendations,
            "can_adopt": can_adopt,
            "attack_mappings": attack_mappings,
        },
    )


@router.post("/library/entries/{entry_id}/delete")
async def delete_library_entry(
    entry_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    """Admin-only runtime delete of an ``imported`` library entry (Option B).

    SECURITY: ADMIN-only (imported entries are GLOBAL — every org sees them)
    + CSRF-protected by the global middleware (this unsafe POST is NOT
    exempted). The service enforces the two safety guards:
    - Arch-I2 per-row seed-undeletable guard → 403.
    - Arch-I1 override-FK guard (live OR tombstoned) → 403.

    Option B: a pinned scenario does NOT block the delete; the delete
    succeeds and the result page surfaces the pinned-scenario count as a
    warning (the scenarios keep working unchanged — clone-time copied the
    FAIR distributions onto the scenario row).
    """
    svc = ScenarioLibraryService(db)
    try:
        result = await svc.delete_imported_entry(
            entry_id=entry_id,
            user=user,
            ip_address=client_ip(request),
        )
    except LibraryEntryNotFoundError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="library entry not found"
        ) from None
    except LibraryEntryDeleteRefusedError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    await db.commit()
    return templates.TemplateResponse(
        request,
        "library/delete_result.html",
        {
            "current_user": user,
            "flash": None,
            "result": result,
        },
    )


@router.get("/library/_partials/cards", response_class=HTMLResponse)
async def get_library_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(
        require_role(
            UserRole.VIEWER,
            UserRole.ANALYST,
            UserRole.REVIEWER,
            UserRole.ADMIN,
        )
    ),
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    """HTMX hx-get target: returns the cards-only partial for filter changes."""
    organization = await db.get(Organization, user.organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    filters = _parse_browse_filters(request)
    svc = ScenarioLibraryService(db)
    page_data = await svc.list_browseable(
        filters=filters,
        page=page,
    )
    return templates.TemplateResponse(
        request,
        "library/_entry_card.html",
        {
            "current_user": user,
            "flash": None,
            "organization": organization,
            "entries": page_data.entries,
            "page": page_data.page,
        },
    )
