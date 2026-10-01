"""Threat Agent Library pages (spec section 5.1). Read-only in P1. The scenario query is
org-scoped and shows all statuses by design (matches /scenarios)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models.enums import UserRole
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo
from idraa.routes.deps import get_db, require_role
from idraa.services.threat_communities import SLUG_RE, ThreatCommunityService

router = APIRouter()
_ALL_ROLES = require_role(UserRole.VIEWER, UserRole.ANALYST, UserRole.REVIEWER, UserRole.ADMIN)
_SCENARIO_CAP = 200


@router.get("/library/threat-communities", response_class=HTMLResponse)
async def list_threat_communities(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_ALL_ROLES),
) -> HTMLResponse:
    communities = await ThreatCommunityService(
        db, organization_id=user.organization_id
    ).list_published()
    return templates.TemplateResponse(
        request,
        "library/threat_communities.html",
        {"current_user": user, "communities": communities},
    )


@router.get("/library/threat-communities/{slug}", response_class=HTMLResponse)
async def threat_community_detail(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_ALL_ROLES),
) -> HTMLResponse:
    if not SLUG_RE.fullmatch(slug):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="threat community not found"
        )
    community = await ThreatCommunityService(db, organization_id=user.organization_id).get_by_slug(
        slug
    )
    if community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="threat community not found"
        )
    entries = await ScenarioLibraryRepo(db).list_published(threat_community_slugs=[slug], limit=200)
    rows = (
        (
            await db.execute(
                select(Scenario)
                .where(
                    Scenario.organization_id == user.organization_id,
                    Scenario.threat_community_id == community.id,
                    Scenario.threat_community_version == community.version,
                )
                .order_by(Scenario.name)
                .limit(_SCENARIO_CAP + 1)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "library/threat_community_detail.html",
        {
            "current_user": user,
            "community": community,
            "entries": entries,
            "scenarios": rows[:_SCENARIO_CAP],
            "scenarios_truncated": len(rows) > _SCENARIO_CAP,
        },
    )
