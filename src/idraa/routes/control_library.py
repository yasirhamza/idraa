"""Control library catalog browse + export (P2b Task 7). Mirrors routes/library.py.

Read-only (viewer+). The adopt (clone-snapshot) action lives in routes/controls.py
(analyst+) and is a separate task. Browse surfaces the latest-published version per
logical id; filtering/search/pagination delegate to ControlLibraryService.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.config import get_settings
from idraa.models.control import Control
from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.enums import (
    ControlSource,
    ControlType,
    FairCamSubFunction,
    StepUpCategory,
    UserRole,
)
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_role, require_step_up
from idraa.services.audit import log_bulk_export
from idraa.services.control_library import (
    ControlLibraryBrowseFilters,
    ControlLibraryBrowsePage,
    ControlLibraryService,
)
from idraa.services.org import require_sole_org
from idraa.utils.csv_export import csv_response

router = APIRouter(tags=["control-library"])

_VIEWER_PLUS = require_role(UserRole.VIEWER, UserRole.ANALYST, UserRole.REVIEWER, UserRole.ADMIN)

# Hoist the valid-value sets so _parse_filters doesn't rebuild them per request
# (mirrors routes/library.py:_VALID_VALUES).
_VALID_VALUES: dict[type[StrEnum], frozenset[str]] = {
    ControlType: frozenset(m.value for m in ControlType),
    FairCamSubFunction: frozenset(m.value for m in FairCamSubFunction),
}


def _parse_filters(request: Request) -> ControlLibraryBrowseFilters:
    """Parse the GET querystring into a ControlLibraryBrowseFilters DTO.

    Enum facets drop unknown values silently; free-text facets are length-capped
    and count-capped (belt-and-suspenders against pathological querystrings).
    """
    qp = request.query_params

    def _enum_list(key: str, enum_cls: type[StrEnum]) -> list[StrEnum]:
        valid = _VALID_VALUES[enum_cls]
        return [enum_cls(v) for v in qp.getlist(key) if v in valid]

    def _text_facet(key: str, cap: int) -> list[str]:
        # The sidebar renders each free-text facet as ONE comma-joined input
        # (value="{{ filters.x | join(', ') }}") and hx-include sends it on
        # every filter change — empty when the user hasn't typed anything. So:
        #   - split on comma to recover multi-value entries ("PR.AA-01, PR.AA-02"),
        #   - drop blank/whitespace parts, so an empty `nist_csf=` param is "no
        #     filter" rather than a literal "" tag the JSON post-filter rejects
        #     every row against (which broke ALL control-library filtering).
        out: list[str] = []
        for raw in qp.getlist(key):
            for part in raw.split(","):
                stripped = part.strip()
                if stripped:
                    out.append(stripped[:cap])
        return out[:50]

    return ControlLibraryBrowseFilters(
        sub_functions=_enum_list("sub_function", FairCamSubFunction),  # type: ignore[arg-type]
        control_types=_enum_list("control_type", ControlType),  # type: ignore[arg-type]
        nist_csf_subcategories=_text_facet("nist_csf", 64),
        cis_safeguards=_text_facet("cis", 32),
        industries=_text_facet("industry", 64),
        search_text=(qp.get("q") or "")[:200] or None,
    )


async def _assignments_by_entry(
    db: AsyncSession, entries: list[ControlLibraryEntry]
) -> dict[tuple[uuid.UUID, int], list[ControlLibraryEntryAssignment]]:
    """Load FAIR-CAM assignments for the surfaced (id, version) entry pairs.

    The browse query returns entries only; the card needs each entry's FAIR-CAM
    function badges + reference effectiveness. Keyed on the composite (id, version)
    so a card shows ONLY its surfaced version's assignments.
    """
    if not entries:
        return {}
    keys = {(e.id, e.version) for e in entries}
    rows = (
        (
            await db.execute(
                select(ControlLibraryEntryAssignment).where(
                    ControlLibraryEntryAssignment.library_entry_id.in_({e.id for e in entries})
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[tuple[uuid.UUID, int], list[ControlLibraryEntryAssignment]] = {}
    for a in rows:
        key = (a.library_entry_id, a.library_entry_version)
        if key in keys:
            out.setdefault(key, []).append(a)
    return out


async def _adopted_entry_ids(db: AsyncSession) -> set[str]:
    """Return the set of library entry ids the caller's org has already adopted.

    Org-scoped (Sec-M4): the lookup filters on ``organization_id == org.id`` — never
    a global ``library_pin`` scan, which would leak another org's adoptions. Used to
    surface the non-blocking "Already adopted" re-adopt marker (§6.5) on browse cards.
    """
    org = await require_sole_org(db)
    rows = (
        (
            await db.execute(
                select(Control.library_pin).where(
                    Control.organization_id == org.id,
                    Control.source == ControlSource.LIBRARY_DERIVED,
                )
            )
        )
        .scalars()
        .all()
    )
    return {p["entry_id"] for p in rows if p and p.get("entry_id")}


@router.get(
    "/controls/library/export.csv",
    dependencies=[Depends(require_step_up(StepUpCategory.EXPORTS))],
)
async def control_library_export_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_VIEWER_PLUS),
) -> Response:
    """Stream the published control catalog as a CSV download.

    Arch-B1 / Arch-2: declared BEFORE /controls/library and BEFORE any
    /controls/library/{id} route so "export.csv" matches as a literal path.
    Spec-5: include the framework-tag columns so the export is a usable catalog
    dump (mirrors /library/export.csv column richness), not just IDs.
    """
    page: ControlLibraryBrowsePage = await ControlLibraryService(db).list_browseable(
        filters=ControlLibraryBrowseFilters(), page=1, page_size=10_000
    )
    # #304: bulk egress audit row.
    await log_bulk_export(
        db,
        organization_id=user.organization_id,
        entity_type="control_library",
        fmt="csv",
        count=len(page.entries),
        user_id=user.id,
        ip_address=client_ip(request),
    )
    header = [
        "id",
        "version",
        "slug",
        "name",
        "control_type",
        "reference_annual_cost",
        "nist_csf_subcategories",
        "cis_safeguards",
        "iso_27001_controls",
        "status",
    ]
    rows = (
        (
            str(e.id),
            e.version,
            e.slug,
            e.name,
            e.control_type.value,
            "" if e.reference_annual_cost is None else str(e.reference_annual_cost),
            ";".join(e.nist_csf_subcategories or []),
            ";".join(e.cis_safeguards or []),
            ";".join(e.iso_27001_controls or []),
            e.status,
        )
        for e in page.entries
    )
    return csv_response(filename="control_library.csv", header=header, rows_iter=rows)


@router.get("/controls/library", response_class=HTMLResponse)
async def control_library_browse(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_VIEWER_PLUS),
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    """Browse the control library catalog — viewer+."""
    filters = _parse_filters(request)
    page_size = get_settings().list_page_size
    result = await ControlLibraryService(db).list_browseable(
        filters=filters, page=page, page_size=page_size
    )
    assignments = await _assignments_by_entry(db, result.entries)
    adopted_entry_ids = await _adopted_entry_ids(db)
    return templates.TemplateResponse(
        request,
        "controls/library/browse.html",
        {
            "current_user": user,
            "flash": None,
            "entries": result.entries,
            "assignments_by_entry": assignments,
            "adopted_entry_ids": adopted_entry_ids,
            "page": result.page,
            "total": result.total,
            "filters": filters,
            "page_size": page_size,
        },
    )


@router.get("/controls/library/_partials/cards", response_class=HTMLResponse)
async def control_library_cards(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_VIEWER_PLUS),
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    """HTMX hx-get target: cards-only partial for filter/search changes."""
    filters = _parse_filters(request)
    page_size = get_settings().list_page_size
    result = await ControlLibraryService(db).list_browseable(
        filters=filters, page=page, page_size=page_size
    )
    assignments = await _assignments_by_entry(db, result.entries)
    adopted_entry_ids = await _adopted_entry_ids(db)
    return templates.TemplateResponse(
        request,
        "controls/library/_entry_card.html",
        {
            "current_user": user,
            "flash": None,
            "entries": result.entries,
            "assignments_by_entry": assignments,
            "adopted_entry_ids": adopted_entry_ids,
            "page": result.page,
            "total": result.total,
            "filters": filters,
            "page_size": page_size,
        },
    )
