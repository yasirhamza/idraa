"""Admin override CRUD — full create/update/delete + RBAC + version bump + audit.

Spec §8.1 §8.2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.scenario_library import ScenarioLibraryOverride
from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_admin_can_create_override(
    admin_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "2.0",
            "tef_mode": "6.0",
            "tef_high": "18.0",
            "reason": "Healthcare org sees 1.5x baseline TEF for ransomware.",
        },
    )
    assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_create_override_rejects_non_numeric_distribution(
    admin_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """Malformed numeric input on create returns 422, not 500.

    F15.a regression — _parse_distribution previously did unguarded
    float() which propagated ValueError as 500. Form-layer float
    validation now catches malformed input at FastAPI level.
    """
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "abc",  # malformed
            "tef_mode": "6.0",
            "tef_high": "18.0",
            "reason": "should not commit",
        },
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}"


@pytest.mark.asyncio
async def test_analyst_cannot_create_override(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    r = await csrf_post(
        analyst_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "2.0",
            "tef_mode": "6.0",
            "tef_high": "18.0",
            "reason": "n/a",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_update_override_bumps_version(
    admin_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    # Create
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "v1",
        },
    )
    assert r.status_code in (200, 303)

    # Fetch the created override
    o = (await db_session.execute(select(ScenarioLibraryOverride))).scalar_one()

    # Update
    r = await csrf_post(
        admin_client,
        f"/library/overrides/{o.id}",
        data={
            "tef_low": "1.0",
            "tef_mode": "5.0",
            "tef_high": "10.0",
            "reason": "v2 — re-calibrated",
            "methodology_change_reason": "Q1 IR data",
            "expected_version": "1",
        },
    )
    assert r.status_code in (200, 303)
    await db_session.refresh(o)
    assert o.version == 2


@pytest.mark.asyncio
async def test_admin_soft_delete_override(
    admin_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "to delete",
        },
    )
    assert r.status_code in (200, 303)

    o = (await db_session.execute(select(ScenarioLibraryOverride))).scalar_one()

    r = await csrf_post(
        admin_client,
        f"/library/overrides/{o.id}/delete",
        data={},
    )
    assert r.status_code in (200, 303)
    await db_session.refresh(o)
    assert o.deleted_at is not None


@pytest.mark.asyncio
async def test_override_create_writes_audit_log(
    admin_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "audit-tracked",
        },
    )
    assert r.status_code in (200, 303)

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "library_override.create")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_admin_get_overrides_list(
    admin_client: AsyncClient,
) -> None:
    r = await admin_client.get("/library/overrides")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_get_edit_form_cross_org_blocked(
    admin_client: AsyncClient,
    seed_library_entry: Any,
    seed_organization_factory: Callable[..., Awaitable[Any]],
    db_session: AsyncSession,
) -> None:
    """r2 MAJOR — IDOR coverage on the GET edit-form path.

    Admin in org A requests `/library/overrides/{id}/edit` for an override
    owned by org B. Must 404, not 200 — even GET must org-scope.
    """
    # Create override owned by a SECOND org (not the admin_client's org).
    other_org = await seed_organization_factory(name="other org for idor")
    cross_override = ScenarioLibraryOverride(
        organization_id=other_org.id,
        library_entry_id=seed_library_entry.id,
        library_entry_version=seed_library_entry.version,
        threat_event_frequency={
            "distribution": "PERT",
            "low": 1.0,
            "mode": 2.0,
            "high": 3.0,
        },
        reason="cross-org override that admin_client should NOT see",
        version=1,
        row_version=1,
    )
    db_session.add(cross_override)
    await db_session.commit()

    r = await admin_client.get(f"/library/overrides/{cross_override.id}/edit")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_override_duplicate_renders_single_flash(
    admin_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """Issue #107: form.html's inline flash block was deleted; base.html →
    layouts/_flash.html is the only flash source. Triggering the 409
    duplicate-create path must render exactly one ``alert alert-`` element."""
    payload: dict[str, str] = {
        "entry_id": str(seed_library_entry.id),
        "tef_low": "1.0",
        "tef_mode": "2.0",
        "tef_high": "3.0",
        "reason": "first",
    }
    first = await csrf_post(admin_client, "/library/overrides", data=payload)
    assert first.status_code in (200, 303)

    second = await csrf_post(
        admin_client,
        "/library/overrides",
        data={**payload, "reason": "second-attempt"},
    )
    assert second.status_code == 409
    assert "Override already exists" in second.text
    assert second.text.count("alert alert-") == 1


@pytest.mark.asyncio
async def test_create_override_rejects_infinite_value_422(
    admin_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """#333 route-level: FastAPI float Form parses "inf" to float('inf') — the
    service gate must reject it as 422 (re-rendered form), NOT a 500, and
    nothing may persist."""
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "1.0",
            "tef_mode": "4.0",
            "tef_high": "inf",
            "reason": "must not commit",
        },
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    rows = (await db_session.execute(select(ScenarioLibraryOverride))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_update_override_rejects_infinite_value_422(
    admin_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """#333 route-level: inf on the update path returns 422 and the row keeps
    its prior distribution + version."""
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(seed_library_entry.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "v1",
        },
    )
    assert r.status_code in (200, 303)
    o = (await db_session.execute(select(ScenarioLibraryOverride))).scalar_one()

    r = await csrf_post(
        admin_client,
        f"/library/overrides/{o.id}",
        data={
            "tef_low": "1.0",
            "tef_mode": "5.0",
            "tef_high": "inf",
            "reason": "v2 with inf — must be rejected",
            "expected_version": "1",
        },
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    await db_session.refresh(o)
    assert o.version == 1
    assert o.threat_event_frequency["high"] == 3.0
