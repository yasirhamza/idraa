"""Design-system styleguide page.

Always mounted; returns 404 when `Settings.dev_styleguide_enabled` is False so
test fixtures and runtime env-var changes both work without app rebuild
(plan-gate Arch-7). Admin-only via require_role(UserRole.ADMIN).
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from idraa.app import templates
from idraa.config import get_settings
from idraa.models.enums import UserRole
from idraa.models.user import User
from idraa.routes.deps import require_role

router = APIRouter(prefix="/_dev", tags=["dev"])


@router.get("/styleguide", response_class=HTMLResponse)
async def styleguide(
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    if not get_settings().dev_styleguide_enabled:
        raise HTTPException(status_code=404)
    sample_actions = [
        {"label": "Export CSV", "href": "/x.csv", "style": "outline"},
        {"label": "+ New", "href": "/x/new", "style": "primary"},
    ]
    sample_control_columns = [
        {"key": "name", "label": "Name", "sortable": True, "priority": "primary"},
        {"key": "domain", "label": "Domain", "priority": "secondary"},
        {"key": "status", "label": "Status", "kind": "status_pill", "pill_kind": "control"},
        {"key": "amount", "label": "ALE", "align": "right", "numeric": True},
    ]
    sample_control_rows = [
        {"name": "AV/EDR rollout", "domain": "V·R", "status": "active", "amount": "$412,000"},
        {"name": "Backups (immutable)", "domain": "R", "status": "active", "amount": "$1,200,000"},
        {"name": "Email gateway", "domain": "V", "status": "maintenance", "amount": "—"},
    ]
    sample_matrix = SimpleNamespace(
        controls=[
            SimpleNamespace(
                control_id="av-edr",
                control_name="AV/EDR rollout",
                control_type="Detective",
                total_reduction=412000,
            ),
            SimpleNamespace(
                control_id="backups",
                control_name="Backups",
                control_type="Recovery",
                total_reduction=1240000,
            ),
            SimpleNamespace(
                control_id="email-gw",
                control_name="Email gateway",
                control_type="Preventive",
                total_reduction=890000,
            ),
        ],
        rows=[
            SimpleNamespace(
                scenario_name="Ransomware-DC",
                scenario_id="demo-r1",
                cells=[
                    {"control_id": "av-edr", "value": 412000},
                    {"control_id": "backups", "value": None},
                    {"control_id": "email-gw", "value": None},
                ],
            ),
            SimpleNamespace(
                scenario_name="Phishing → BEC",
                scenario_id="demo-r2",
                cells=[
                    {"control_id": "av-edr", "value": None},
                    {"control_id": "backups", "value": 40000},
                    {"control_id": "email-gw", "value": 890000},
                ],
            ),
            SimpleNamespace(
                scenario_name="Insider data exfil",
                scenario_id="demo-r3",
                cells=[
                    {"control_id": "av-edr", "value": None},
                    {"control_id": "backups", "value": 1200000},
                    {"control_id": "email-gw", "value": None},
                ],
            ),
        ],
    )
    return templates.TemplateResponse(
        request,
        "_dev/styleguide.html",
        {
            "current_user": user,
            "sample_actions": sample_actions,
            "sample_control_columns": sample_control_columns,
            "sample_control_rows": sample_control_rows,
            "sample_matrix": sample_matrix,
        },
    )
