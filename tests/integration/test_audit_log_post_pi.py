"""Audit log behavior after the PR pi migration.

F20: Moved to tests/migrations/test_pr_pi_migration.py (requires alembic_config /
     alembic_engine fixtures scoped to the migrations conftest). This file
     only contains F21.

F21: New post-migration runs write audit rows correctly.
"""

from __future__ import annotations

import pytest


# F21: New runs created post-migration write audit rows with correct entity_type
@pytest.mark.asyncio
async def test_new_run_writes_audit_log_post_migration(
    db_session: object,
    seed_organization: object,
    seed_scenario_with_controls: object,
    seed_user: object,
    wire_executor_to_test_db: None,
) -> None:
    """Run a new analysis post-migration; verify audit_log gets at least one
    entry with entity_type='risk_analysis_run' and a matching entity_id.
    """
    from fastapi import BackgroundTasks
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from idraa.models.audit_log import AuditLog
    from idraa.models.organization import Organization
    from idraa.models.scenario import Scenario
    from idraa.models.user import User
    from idraa.services.runs import RunService

    db: AsyncSession = db_session  # type: ignore[assignment]
    org: Organization = seed_organization  # type: ignore[assignment]
    scenario: Scenario = seed_scenario_with_controls  # type: ignore[assignment]
    user: User = seed_user  # type: ignore[assignment]

    service = RunService(db)
    run = await service.create_and_dispatch(
        organization_id=org.id,
        scenario_ids=[scenario.id],
        mc_iterations_override=200,
        created_by=user.id,
        background_tasks=BackgroundTasks(),
    )
    # mc_iterations=200 < _SYNC_THRESHOLD=1000 → executed inline
    await db.refresh(run)

    rs = await db.execute(select(AuditLog).where(AuditLog.entity_type == "risk_analysis_run"))
    rows = list(rs.scalars().all())
    assert len(rows) >= 1, (
        "No risk_analysis_run audit entries written by RunService.create_and_dispatch"
    )
    # All audit rows must reference the run we just created
    audit_run_ids = {str(r.entity_id) for r in rows}
    assert str(run.id) in audit_run_ids, (
        f"Audit entries exist but none reference run.id={run.id}. Found entity_ids: {audit_run_ids}"
    )
