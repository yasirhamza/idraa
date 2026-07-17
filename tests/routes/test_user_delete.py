"""Conditional hard-delete for users (#296).

A user may be hard-deleted ONLY if they authored no business entities
(runs, scenarios, controls). Otherwise the admin must deactivate instead.

These tests build authored entities directly in the admin's org via the
``db_session`` fixture (which points at the same SQLite file as ``client``),
because the shared ``seed_*`` factories live in a *different* org than the
``authed_admin`` fixture's org and the delete path is org-scoped.

CSRF: POSTs go through the shared ``csrf_post`` helper. RBAC: reviewer is
rejected at the ``require_role(ADMIN)`` dependency with 403.
"""

from __future__ import annotations

import hashlib
import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import UserRole
from idraa.models.user import User
from tests.conftest import csrf_post
from tests.factories import create_user


async def _admin_org_id(db_session: AsyncSession) -> uuid.UUID:
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    return me.organization_id


async def _seed_user_in_org(
    db_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    email: str,
    role: UserRole = UserRole.ANALYST,
) -> User:
    from idraa.models.organization import Organization

    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    return await create_user(db_session, org, email=email, role=role)


async def _seed_run_authored_by(
    db_session: AsyncSession, org_id: uuid.UUID, author_id: uuid.UUID
) -> None:
    """Minimal RiskAnalysisRun with created_by=author_id, no scenario FK dependency.

    A run requires a scenario_id (NOT NULL FK). Build a minimal scenario in
    the same org first, then the run referencing it.
    """
    from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
    from idraa.models.scenario import Scenario

    scenario = Scenario(
        organization_id=org_id,
        name="authored-scenario",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        status=EntityStatus.ACTIVE,
        created_by=author_id,
    )
    db_session.add(scenario)
    await db_session.flush()
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=author_id,
        simulation_results=None,
    )
    db_session.add(run)
    await db_session.commit()


async def test_delete_user_authored_nothing_succeeds(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    org_id = await _admin_org_id(db_session)
    target = await _seed_user_in_org(db_session, org_id, email="nobody@test.local")
    await db_session.commit()
    target_id = target.id

    r = await csrf_post(
        client,
        f"/users/{target_id}/delete",
        {"confirm": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db_session.expire_all()
    gone = (await db_session.execute(select(User).where(User.id == target_id))).scalar_one_or_none()
    assert gone is None


async def test_delete_user_who_authored_run_returns_409(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    org_id = await _admin_org_id(db_session)
    target = await _seed_user_in_org(db_session, org_id, email="author@test.local")
    await db_session.commit()
    target_id = target.id
    await _seed_run_authored_by(db_session, org_id, target_id)

    r = await csrf_post(
        client,
        f"/users/{target_id}/delete",
        {"confirm": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "deactivate" in r.text.lower()
    db_session.expire_all()
    still = (
        await db_session.execute(select(User).where(User.id == target_id))
    ).scalar_one_or_none()
    assert still is not None


async def test_cannot_delete_last_admin_returns_409(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    org_id = me.organization_id
    # The actor (``user@test.local``) is the org's only ACTIVE admin. Seed a
    # SECOND admin as the delete target so the self-delete guard isn't what
    # fires, but mark the target INACTIVE so the active-admin count stays at 1
    # (the actor). Deleting any admin while ``_is_last_admin`` is True is
    # refused (the guard checks target.role==ADMIN regardless of the target's
    # own active state, mirroring edit_post's last-admin guard).
    target = await _seed_user_in_org(
        db_session, org_id, email="lastadmin@test.local", role=UserRole.ADMIN
    )
    target.is_active = False
    await db_session.commit()
    target_id = target.id

    r = await csrf_post(
        client,
        f"/users/{target_id}/delete",
        {"confirm": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 409
    db_session.expire_all()
    still = (
        await db_session.execute(select(User).where(User.id == target_id))
    ).scalar_one_or_none()
    assert still is not None


async def test_reviewer_forbidden(
    authed_reviewer: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, org_id = authed_reviewer
    # Seed a deletable target in the reviewer's org.
    target = await _seed_user_in_org(db_session, org_id, email="victim@test.local")
    await db_session.commit()
    target_id = target.id
    r = await csrf_post(
        client,
        f"/users/{target_id}/delete",
        {"confirm": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    db_session.expire_all()
    still = (
        await db_session.execute(select(User).where(User.id == target_id))
    ).scalar_one_or_none()
    assert still is not None


async def test_missing_confirm_returns_400(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    org_id = await _admin_org_id(db_session)
    target = await _seed_user_in_org(db_session, org_id, email="needsconfirm@test.local")
    await db_session.commit()
    target_id = target.id
    r = await csrf_post(
        client,
        f"/users/{target_id}/delete",
        {},  # no confirm
        follow_redirects=False,
    )
    assert r.status_code == 400
    db_session.expire_all()
    still = (
        await db_session.execute(select(User).where(User.id == target_id))
    ).scalar_one_or_none()
    assert still is not None


async def test_cross_org_delete_returns_404(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    # Build a SEPARATE org with its own user; the admin must not be able to
    # delete it (org-scoped fetch returns None -> 404).
    from tests.factories import create_org

    other_org = await create_org(db_session, name="Other Org")
    other_user = await create_user(db_session, other_org, email="outsider@test.local")
    await db_session.commit()
    other_id = other_user.id

    r = await csrf_post(
        client,
        f"/users/{other_id}/delete",
        {"confirm": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 404
    db_session.expire_all()
    still = (await db_session.execute(select(User).where(User.id == other_id))).scalar_one_or_none()
    assert still is not None


async def test_audit_row_has_no_raw_email_local_part(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    org_id = await _admin_org_id(db_session)
    # Distinctive local part so its absence is meaningful.
    local_part = "verydistinctlocal"
    target = await _seed_user_in_org(db_session, org_id, email=f"{local_part}@test.local")
    await db_session.commit()
    target_id = target.id

    r = await csrf_post(
        client,
        f"/users/{target_id}/delete",
        {"confirm": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "user.delete",
                AuditLog.entity_id == target_id,
            )
        )
    ).scalar_one()
    import json

    serialized = json.dumps(row.changes)
    assert local_part not in serialized
    # Domain is allowed to remain (redact_email keeps the domain).
    assert "test.local" in serialized
