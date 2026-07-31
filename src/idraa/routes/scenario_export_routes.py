"""Scenario export routes — bulk list export + single-scenario export.

Extracted out of ``routes/scenarios.py`` under issue #119 (standing architect
advisory — the file crossed 4,600 lines). Mechanical extraction, NO semantic
change: both handlers below are byte-identical to their prior home in
``routes/scenarios.py``, just re-homed onto their own :class:`APIRouter`.

Mirrors :mod:`idraa.routes.scenario_import`'s own extraction shape: this
router owns literal ``/scenarios/export`` and ``/scenarios/{scenario_id}/export``
paths, and MUST be included in ``app.py`` BEFORE ``scenarios_router`` — that
router owns ``GET /scenarios/{scenario_id}`` as an UNTYPED path param
(B5 declaration-order precedent, ``routes/scenarios.py``), which would
otherwise capture ``/scenarios/export`` and 422 on UUID parsing of the
literal string ``"export"``. ``/scenarios/{scenario_id}/export`` has no such
collision (three path segments vs two) but is included alongside its sibling
for one consistent router.

RBAC: ``require_user`` (any authenticated role) per the B3 plan-gate
precedent — export is a read, not a VIEWER-only allowlist. Both routes are
gated behind ``require_step_up(StepUpCategory.EXPORTS)``. CSRF is enforced
by the global CSRFMiddleware (GET is a safe method; no route-level CSRF dep
needed). Transaction commit is owned by the ``get_db`` dependency.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, StepUpCategory
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.routes.deps import audit_client_ip, get_db, require_step_up, require_user
from idraa.services.audit import log_bulk_export

router = APIRouter()


@router.get(
    "/scenarios/export",  # B5: MUST be declared before /scenarios/{scenario_id}
    dependencies=[Depends(require_step_up(StepUpCategory.EXPORTS))],
)
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
        ip_address=audit_client_ip(request),
        filters={"status": status.value} if status is not None else None,
    )
    if fmt == "json":
        return export_json_response(rows_page, filename="scenarios.json")
    return export_csv_response(rows_page, filename="scenarios.csv")


@router.get(
    "/scenarios/{scenario_id}/export",
    dependencies=[Depends(require_step_up(StepUpCategory.EXPORTS))],
)
async def scenario_export_one(
    scenario_id: uuid.UUID,
    request: Request,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),  # B3
) -> Response:
    """Export a single scenario — any authenticated user, org-scoped 404 on cross-org id.

    Cross-org IDs return 404 (NOT 403) so we don't leak existence of scenarios
    owned by other orgs (mirrors view_scenario's B9/B10 precedent).

    idraa#107/#110 review: audited like the bulk sibling so per-id exports
    consume the export budget (``log_bulk_export`` IS the rate limiter).
    """
    from idraa.services.scenario_export import export_csv_response, export_json_response

    scenario = await db.get(Scenario, scenario_id)
    if scenario is None or scenario.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Scenario not found")
    await log_bulk_export(
        db,
        organization_id=user.organization_id,
        entity_type="scenario",
        fmt=format if format == "json" else "csv",
        count=1,
        user_id=user.id,
        ip_address=audit_client_ip(request),
        filters={"scenario_id": str(scenario_id)},
    )
    if format == "json":
        return export_json_response([scenario], filename=f"scenario-{scenario_id}.json")
    return export_csv_response([scenario], filename=f"scenario-{scenario_id}.csv")
