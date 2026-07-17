"""Service unit tests for SME directory. Per spec §9.3."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from idraa.schemas.sme import SMECreate, SMERequest, SMEUpdate
from idraa.services import sme_directory as svc
from idraa.services.sme_directory import (
    SMEArchivedEmailCollisionError,
    SMENotFoundError,
    SMESystemOwnedImmutableError,
)


@pytest_asyncio.fixture
async def admin_sme(db, org_id, actor_id):
    return await svc.create(
        db,
        SMECreate(name="Jane", email="jane@example.com"),
        organization_id=org_id,
        actor_id=actor_id,
    )


async def test_create_normalizes_email_nfkc(db, org_id, actor_id):
    sme = await svc.create(
        db,
        SMECreate(name="A", email="Jane@Example.com  "),
        organization_id=org_id,
        actor_id=actor_id,
    )
    await db.refresh(sme)
    assert sme.email == "Jane@Example.com"
    assert sme.email_lower == "jane@example.com"


async def test_cross_org_lookup_returns_404(db, org_id, admin_sme):
    other_org = uuid4()
    with pytest.raises(SMENotFoundError):
        await svc.get_sme_for_org(db, admin_sme.id, other_org)


async def test_archived_email_collision_on_admin_create(db, org_id, actor_id, admin_sme):
    await svc.archive(db, admin_sme.id, organization_id=org_id, actor_id=actor_id)
    with pytest.raises(SMEArchivedEmailCollisionError):
        await svc.create(
            db,
            SMECreate(name="Bob", email="jane@example.com"),
            organization_id=org_id,
            actor_id=actor_id,
        )


async def test_request_creates_live_sme(db, org_id, actor_id):
    """request() creates a live (non-pending) SME row with created_via='analyst_request'."""
    sme = await svc.request(
        db,
        SMERequest(name="Alice Chen"),
        organization_id=org_id,
        actor_id=actor_id,
    )
    assert sme.name == "Alice Chen"
    assert sme.created_via == "analyst_request"
    assert sme.created_by == actor_id
    # pending_review attribute no longer exists on the model post-strip.
    assert not hasattr(sme, "pending_review")


async def test_iris_lazy_create_concurrent_calls(db, org_id):
    results = await asyncio.gather(*[svc.get_or_create_iris_sme(db, org_id) for _ in range(5)])
    smes = {r[0].id for r in results}
    assert len(smes) == 1
    created_count = sum(1 for r in results if r[1])
    assert created_count == 1


async def test_update_rejects_system_owned(db, org_id, actor_id):
    # Sec-7/Sec-19 PR1 fix: system-owned target raises
    # SMESystemOwnedImmutableError (→ 422), NOT SMENotFoundError (→ 404).
    iris, _ = await svc.get_or_create_iris_sme(db, org_id)
    with pytest.raises(SMESystemOwnedImmutableError):
        await svc.update(
            db,
            iris.id,
            SMEUpdate(name="Hacked"),
            organization_id=org_id,
            actor_id=actor_id,
        )


async def test_list_for_dropdown_excludes_archived(db, org_id, actor_id, admin_sme):
    archived_sme = await svc.create(
        db,
        SMECreate(name="Archived", email="archived@example.com"),
        organization_id=org_id,
        actor_id=actor_id,
    )
    await svc.archive(db, archived_sme.id, organization_id=org_id, actor_id=actor_id)
    rows = await svc.list_for_dropdown(db, org_id)
    # Spec-9 PR1: list_for_dropdown returns list[dict], not ORM rows.
    ids = {r["id"] for r in rows}
    assert str(admin_sme.id) in ids
    assert str(archived_sme.id) not in ids
