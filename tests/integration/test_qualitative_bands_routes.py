"""Org-band admin CRUD routes — full create/update/delete + RBAC + IDOR +
optimistic-lock + delete-then-recreate (epic #34 P1c Task 7).

Mirrors ``tests/integration/test_library_override_crud.py``'s style/depth:
helper builders instead of local fixtures, direct DB assertions via
``db_session`` alongside the HTTP-level assertions.

Plan: docs/superpowers/plans/2026-07-18-import-ui-p1c.md Task 7.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.qualitative_mapping import QualitativeMappingBand, QualitativeMappingOrgBand
from tests.conftest import csrf_post

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_canonical(
    db_session: AsyncSession,
    *,
    kind: str = "frequency",
    label: str = "low",
    low: float = 0.1,
    mode: float = 0.32,
    high: float = 1.0,
    sort_order: int = 1,
) -> QualitativeMappingBand:
    band = QualitativeMappingBand(
        kind=kind,
        label=label,
        low=low,
        mode=mode,
        high=high,
        sort_order=sort_order,
        derivation="route-test canonical band, not a real citation",
        version=1,
    )
    db_session.add(band)
    # commit (not flush): `client` and `db_session` are independent engines
    # against the same SQLite file (conftest.py:client docstring) — the
    # seeded row must be durably visible to the HTTP-driven session.
    await db_session.commit()
    return band


DEFAULT_CREATE_PAYLOAD: dict[str, str] = {
    "kind": "frequency",
    "label": "custom_tier",
    "low": "1.0",
    "mode": "3.0",
    "high": "10.0",
    "reason": "org-specific calibration",
}


# ---------------------------------------------------------------------------
# List — EFFECTIVE table, canonical + org marks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_bands_list_shows_canonical_and_org_marks(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_canonical(
        db_session, kind="frequency", label="low", low=0.1, mode=0.32, high=1.0, sort_order=1
    )
    await _seed_canonical(
        db_session,
        kind="magnitude",
        label="low",
        low=10_000.0,
        mode=32_000.0,
        high=100_000.0,
        sort_order=1,
    )

    # Override only the frequency/low band; magnitude/low stays canonical.
    create = await csrf_post(
        admin_client,
        "/qualitative-bands",
        data={
            "kind": "frequency",
            "label": "low",
            "low": "0.2",
            "mode": "0.6",
            "high": "2.0",
            "reason": "org-specific calibration",
        },
    )
    assert create.status_code in (200, 303)

    r = await admin_client.get("/qualitative-bands")
    assert r.status_code == 200
    assert "org override" in r.text
    assert "canonical" in r.text

    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()
    assert f"/qualitative-bands/{band.id}/edit" in r.text


@pytest.mark.asyncio
async def test_admin_bands_list_empty_state(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/qualitative-bands")
    assert r.status_code == 200
    assert "No mapping bands" in r.text


# ---------------------------------------------------------------------------
# Create — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_can_create_org_band(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code in (200, 303)

    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()
    assert band.kind == "frequency"
    assert band.label == "custom_tier"
    assert band.version == 1
    assert band.row_version == 1
    assert band.deleted_at is None
    assert (band.low, band.mode, band.high) == (1.0, 3.0, 10.0)


@pytest.mark.asyncio
async def test_admin_get_new_band_form(admin_client: AsyncClient) -> None:
    """Clean-load render path (band=None, form=None) — not otherwise exercised
    by the 422/409 re-render tests below, which always pass a populated
    ``band`` or ``form`` context."""
    r = await admin_client.get("/qualitative-bands/new")
    assert r.status_code == 200
    assert "New mapping band" in r.text


@pytest.mark.asyncio
async def test_admin_get_edit_band_form(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code in (200, 303)
    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()

    r = await admin_client.get(f"/qualitative-bands/{band.id}/edit")
    assert r.status_code == 200
    assert "Edit mapping band" in r.text
    assert f'value="{band.row_version}"' in r.text


# ---------------------------------------------------------------------------
# Create — 422 validation flash (incl. non-finite)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_band_rejects_non_finite_high_422(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """FastAPI's float Form parses "inf" -> float('inf') before the handler
    runs; the service's finiteness gate (Sec-I1) must reject it as 422 — not
    a 500 — and nothing may persist."""
    r = await csrf_post(
        admin_client,
        "/qualitative-bands",
        data={**DEFAULT_CREATE_PAYLOAD, "high": "inf"},
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    assert "finite" in r.text.lower()

    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_create_band_rejects_bad_label_pattern_422(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(
        admin_client,
        "/qualitative-bands",
        data={**DEFAULT_CREATE_PAYLOAD, "label": "Bad Label!"},
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    assert "label" in r.text.lower()

    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_create_band_rejects_duplicate_active_422(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """create_org_band folds "already exists" into a plain ValidationError
    (no dedicated AlreadyExists exception here, unlike library_overrides) —
    maps to 422, not 409."""
    first = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert first.status_code in (200, 303)

    second = await csrf_post(
        admin_client,
        "/qualitative-bands",
        data={**DEFAULT_CREATE_PAYLOAD, "reason": "second attempt — should fail"},
    )
    assert second.status_code == 422
    assert "already exists" in second.text.lower()

    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Update — happy path + 409 lock conflict re-render
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_update_band_bumps_version(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code in (200, 303)
    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()

    r = await csrf_post(
        admin_client,
        f"/qualitative-bands/{band.id}",
        data={
            "low": "2.0",
            "mode": "6.0",
            "high": "20.0",
            "reason": "v2 — re-calibrated",
            "expected_row_version": "1",
        },
    )
    assert r.status_code in (200, 303)
    await db_session.refresh(band)
    assert band.version == 2
    assert band.row_version == 2
    assert (band.low, band.mode, band.high) == (2.0, 6.0, 20.0)
    assert band.reason == "v2 — re-calibrated"


@pytest.mark.asyncio
async def test_update_band_rejects_non_finite_high_422(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Update-path mirror of the create-path non-finite gate — the row
    keeps its prior values + version on rejection."""
    r = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code in (200, 303)
    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()

    r = await csrf_post(
        admin_client,
        f"/qualitative-bands/{band.id}",
        data={
            "low": "1.0",
            "mode": "5.0",
            "high": "inf",
            "reason": "must not commit",
            "expected_row_version": "1",
        },
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    await db_session.refresh(band)
    assert band.version == 1
    assert band.high == 10.0


@pytest.mark.asyncio
async def test_update_band_stale_row_version_conflict_409(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code in (200, 303)
    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()

    first = await csrf_post(
        admin_client,
        f"/qualitative-bands/{band.id}",
        data={
            "low": "2.0",
            "mode": "6.0",
            "high": "20.0",
            "reason": "first update",
            "expected_row_version": "1",
        },
    )
    assert first.status_code in (200, 303)

    # Replays the now-stale expected_row_version=1.
    stale = await csrf_post(
        admin_client,
        f"/qualitative-bands/{band.id}",
        data={
            "low": "3.0",
            "mode": "9.0",
            "high": "30.0",
            "reason": "stale retry",
            "expected_row_version": "1",
        },
    )
    assert stale.status_code == 409, f"expected 409, got {stale.status_code}"
    assert "version conflict" in stale.text.lower()
    # Re-render carries the CURRENT row_version (2) so a resubmit succeeds.
    assert 'name="expected_row_version" value="2"' in stale.text

    await db_session.refresh(band)
    assert band.row_version == 2
    assert (band.low, band.mode, band.high) == (2.0, 6.0, 20.0)


# ---------------------------------------------------------------------------
# Delete — happy path + delete-then-recreate flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_soft_delete_band(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    r = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code in (200, 303)
    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()

    r = await csrf_post(admin_client, f"/qualitative-bands/{band.id}/delete", data={})
    assert r.status_code in (200, 303)
    await db_session.refresh(band)
    assert band.deleted_at is not None


@pytest.mark.asyncio
async def test_delete_then_recreate_same_label_succeeds(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Arch-I3: the partial unique index is deleted_at-scoped, so
    delete-then-recreate of the same (org, kind, label) must succeed at the
    route level too — not just the service level (already pinned in
    test_qualitative_bands_service.py)."""
    first_resp = await csrf_post(admin_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert first_resp.status_code in (200, 303)
    first_band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalar_one()

    deleted = await csrf_post(admin_client, f"/qualitative-bands/{first_band.id}/delete", data={})
    assert deleted.status_code in (200, 303)

    second_resp = await csrf_post(
        admin_client,
        "/qualitative-bands",
        data={
            **DEFAULT_CREATE_PAYLOAD,
            "low": "2.0",
            "mode": "5.0",
            "high": "15.0",
            "reason": "recreated",
        },
    )
    assert second_resp.status_code in (200, 303)

    # first_band was loaded into this session's identity map before the
    # delete (committed via admin_client's independent connection) — expire
    # it so the final query re-reads current DB state instead of returning
    # the stale cached object (mirrors db_session.refresh() elsewhere in
    # this file; here multiple rows are involved so expire_all is simpler).
    db_session.expire_all()
    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert len(rows) == 2  # tombstoned first + active second
    active = [row for row in rows if row.deleted_at is None]
    assert len(active) == 1
    assert active[0].label == "custom_tier"
    assert active[0].low == 2.0
    assert active[0].id != first_band.id


# ---------------------------------------------------------------------------
# IDOR — cross-org 404 (GET edit, POST update, POST delete)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_get_edit_form_cross_org_blocked(
    admin_client: AsyncClient,
    seed_organization_factory: Callable[..., Awaitable[Any]],
    db_session: AsyncSession,
) -> None:
    other_org = await seed_organization_factory(name="other org for qual-band idor")
    cross_band = QualitativeMappingOrgBand(
        organization_id=other_org.id,
        kind="frequency",
        label="cross_org_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="cross-org band that admin_client should NOT see",
        version=1,
        row_version=1,
    )
    db_session.add(cross_band)
    await db_session.commit()

    r = await admin_client.get(f"/qualitative-bands/{cross_band.id}/edit")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_band_cross_org_blocked(
    admin_client: AsyncClient,
    seed_organization_factory: Callable[..., Awaitable[Any]],
    db_session: AsyncSession,
) -> None:
    other_org = await seed_organization_factory(name="other org for qual-band update idor")
    cross_band = QualitativeMappingOrgBand(
        organization_id=other_org.id,
        kind="frequency",
        label="cross_org_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="cross-org band",
        version=1,
        row_version=1,
    )
    db_session.add(cross_band)
    await db_session.commit()

    r = await csrf_post(
        admin_client,
        f"/qualitative-bands/{cross_band.id}",
        data={
            "low": "9.0",
            "mode": "9.0",
            "high": "99.0",
            "reason": "cross-org write attempt",
            "expected_row_version": "1",
        },
    )
    assert r.status_code == 404

    await db_session.refresh(cross_band)
    assert cross_band.row_version == 1  # untouched


@pytest.mark.asyncio
async def test_delete_band_cross_org_blocked(
    admin_client: AsyncClient,
    seed_organization_factory: Callable[..., Awaitable[Any]],
    db_session: AsyncSession,
) -> None:
    other_org = await seed_organization_factory(name="other org for qual-band delete idor")
    cross_band = QualitativeMappingOrgBand(
        organization_id=other_org.id,
        kind="frequency",
        label="cross_org_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="cross-org band",
        version=1,
        row_version=1,
    )
    db_session.add(cross_band)
    await db_session.commit()

    r = await csrf_post(admin_client, f"/qualitative-bands/{cross_band.id}/delete", data={})
    assert r.status_code == 404

    await db_session.refresh(cross_band)
    assert cross_band.deleted_at is None  # untouched


# ---------------------------------------------------------------------------
# RBAC — analyst/reviewer 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyst_cannot_list_bands(analyst_client: AsyncClient) -> None:
    r = await analyst_client.get("/qualitative-bands")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_cannot_list_bands(reviewer_client: AsyncClient) -> None:
    r = await reviewer_client.get("/qualitative-bands")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_analyst_cannot_create_band(analyst_client: AsyncClient) -> None:
    r = await csrf_post(analyst_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_cannot_create_band(reviewer_client: AsyncClient) -> None:
    r = await csrf_post(reviewer_client, "/qualitative-bands", data=DEFAULT_CREATE_PAYLOAD)
    assert r.status_code == 403
