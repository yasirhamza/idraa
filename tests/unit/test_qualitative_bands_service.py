"""Task 4 — QualitativeMappingRepo + QualitativeBandService (epic #34 P1b).

Mirrors ``test_library_override_crud_service.py`` fixtures/style: helper
builders instead of local fixtures, one behavior per test, audit-row
assertions inline via a direct ``AuditLog`` query.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §2.
Plan: docs/superpowers/plans/2026-07-18-mapping-tables-converter-p1b.md Task 4
(+ binding Task 4 / Task 1 amendments).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import NotFoundError, QualitativeBandVersionConflictError, ValidationError
from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.qualitative_mapping import QualitativeMappingBand, QualitativeMappingOrgBand
from idraa.models.user import User
from idraa.services.qualitative_bands import EffectiveBand, QualitativeBandService

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
    version: int = 1,
) -> QualitativeMappingBand:
    band = QualitativeMappingBand(
        kind=kind,
        label=label,
        low=low,
        mode=mode,
        high=high,
        sort_order=sort_order,
        derivation="unit-test canonical band, not a real citation",
        version=version,
    )
    db_session.add(band)
    await db_session.flush()
    return band


async def _seed_two_canonical(db_session: AsyncSession) -> None:
    """One frequency + one magnitude canonical band, both labeled 'low'."""
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


async def _audit_rows(
    db_session: AsyncSession,
    *,
    entity_id: uuid.UUID,
    action: str,
) -> list[AuditLog]:
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == entity_id,
                    AuditLog.action == action,
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# ---------------------------------------------------------------------------
# effective_bands() — canonical ⊕ org merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_bands_returns_canonical_when_no_org_override(
    db_session: AsyncSession,
    seed_organization: Organization,
) -> None:
    await _seed_two_canonical(db_session)

    svc = QualitativeBandService(db_session)
    effective = await svc.effective_bands(seed_organization.id)

    assert len(effective) == 2
    freq = effective[("frequency", "low")]
    assert isinstance(freq, EffectiveBand)
    assert (freq.low, freq.mode, freq.high) == (0.1, 0.32, 1.0)
    assert freq.source == "canonical"
    assert freq.source_version == 1


@pytest.mark.asyncio
async def test_effective_bands_org_override_wins_for_existing_label(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    await _seed_two_canonical(db_session)

    svc = QualitativeBandService(db_session)
    await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="low",
        low=0.2,
        mode=0.6,
        high=2.0,
        reason="org-specific calibration",
        user=seed_user,
    )

    effective = await svc.effective_bands(seed_organization.id)

    assert len(effective) == 2  # override replaces, does not add
    freq = effective[("frequency", "low")]
    assert (freq.low, freq.mode, freq.high) == (0.2, 0.6, 2.0)
    assert freq.source == "org"
    assert freq.source_version == 1

    # The untouched magnitude/low canonical band is unaffected.
    mag = effective[("magnitude", "low")]
    assert mag.source == "canonical"


@pytest.mark.asyncio
async def test_effective_bands_org_adds_novel_label(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    await _seed_two_canonical(db_session)

    svc = QualitativeBandService(db_session)
    await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=5.0,
        mode=8.0,
        high=15.0,
        reason="a novel org-only band not in the canonical set",
        user=seed_user,
    )

    effective = await svc.effective_bands(seed_organization.id)

    assert len(effective) == 3  # 2 canonical + 1 novel org addition
    novel = effective[("frequency", "custom_tier")]
    assert novel.source == "org"
    assert (novel.low, novel.mode, novel.high) == (5.0, 8.0, 15.0)


@pytest.mark.asyncio
async def test_effective_bands_soft_deleted_org_row_ignored(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    await _seed_two_canonical(db_session)

    svc = QualitativeBandService(db_session)
    band = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="low",
        low=0.2,
        mode=0.6,
        high=2.0,
        reason="temporary override",
        user=seed_user,
    )
    await svc.delete_org_band(
        organization_id=seed_organization.id,
        band_id=band.id,
        user=seed_user,
    )

    effective = await svc.effective_bands(seed_organization.id)

    assert len(effective) == 2  # back to canonical-only
    freq = effective[("frequency", "low")]
    assert freq.source == "canonical"
    assert (freq.low, freq.mode, freq.high) == (0.1, 0.32, 1.0)


@pytest.mark.asyncio
async def test_effective_bands_org_scoped_across_orgs(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_organization_factory: Callable[..., Awaitable[Organization]],
) -> None:
    """An org override in org A must not leak into org B's effective table."""
    await _seed_two_canonical(db_session)
    org_b = await seed_organization_factory(name="Org B")

    svc = QualitativeBandService(db_session)
    await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="low",
        low=0.2,
        mode=0.6,
        high=2.0,
        reason="org A only",
        user=seed_user,
    )

    effective_b = await svc.effective_bands(org_b.id)
    freq_b = effective_b[("frequency", "low")]
    assert freq_b.source == "canonical"
    assert (freq_b.low, freq_b.mode, freq_b.high) == (0.1, 0.32, 1.0)


# ---------------------------------------------------------------------------
# mapping_versions()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mapping_versions_shape(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    await _seed_two_canonical(db_session)

    svc = QualitativeBandService(db_session)
    await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="low",
        low=0.2,
        mode=0.6,
        high=2.0,
        reason="org override for version pin",
        user=seed_user,
    )

    versions = await svc.mapping_versions(seed_organization.id)

    assert versions["canonical"] == 1
    assert versions["org"] == {"frequency:low": 1}


# ---------------------------------------------------------------------------
# create_org_band() — CRUD + audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_org_band_writes_row_and_audit(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    band = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="magnitude",
        label="custom_tier",
        low=5_000.0,
        mode=8_000.0,
        high=12_000.0,
        reason="org-specific loss-capacity calibration",
        user=seed_user,
    )

    assert band.id is not None
    assert band.organization_id == seed_organization.id
    assert band.version == 1
    assert band.row_version == 1
    assert band.deleted_at is None
    assert (band.low, band.mode, band.high) == (5_000.0, 8_000.0, 12_000.0)

    rows = await _audit_rows(db_session, entity_id=band.id, action="qualitative_band.create")
    assert len(rows) == 1
    assert rows[0].user_id == seed_user.id
    assert rows[0].organization_id == seed_organization.id


@pytest.mark.asyncio
async def test_update_org_band_bumps_version_and_writes_audit(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    band = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="initial",
        user=seed_user,
    )

    updated = await svc.update_org_band(
        organization_id=seed_organization.id,
        band_id=band.id,
        low=2.0,
        mode=6.0,
        high=20.0,
        reason="tuned after calibration review",
        expected_row_version=1,
        user=seed_user,
    )

    assert updated.version == 2
    assert updated.row_version == 2
    assert (updated.low, updated.mode, updated.high) == (2.0, 6.0, 20.0)
    assert updated.reason == "tuned after calibration review"

    rows = await _audit_rows(db_session, entity_id=band.id, action="qualitative_band.update")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_delete_org_band_tombstones_and_writes_audit(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    band = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="to be deleted",
        user=seed_user,
    )

    tombstoned = await svc.delete_org_band(
        organization_id=seed_organization.id,
        band_id=band.id,
        user=seed_user,
    )

    assert tombstoned.deleted_at is not None

    row = (
        await db_session.execute(
            select(QualitativeMappingOrgBand).where(QualitativeMappingOrgBand.id == band.id)
        )
    ).scalar_one_or_none()
    assert row is not None  # soft-delete, not hard-delete
    assert row.deleted_at is not None

    active = await svc.repo.list_org_bands(seed_organization.id)
    assert active == []  # tombstoned row invisible to normal lookup

    rows = await _audit_rows(db_session, entity_id=band.id, action="qualitative_band.delete")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_delete_then_recreate_same_label_succeeds(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    """Amendment (Arch-I3): the partial unique index is deleted_at-scoped,
    so delete-then-recreate of the same (org, kind, label) must succeed —
    NOT collide with the tombstoned row."""
    svc = QualitativeBandService(db_session)
    first = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="first incarnation",
        user=seed_user,
    )
    await svc.delete_org_band(
        organization_id=seed_organization.id,
        band_id=first.id,
        user=seed_user,
    )

    second = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=2.0,
        mode=5.0,
        high=15.0,
        reason="re-created after delete",
        user=seed_user,
    )

    assert second.id != first.id
    assert second.deleted_at is None

    active = await svc.repo.list_org_bands(seed_organization.id)
    assert len(active) == 1
    assert active[0].id == second.id


# ---------------------------------------------------------------------------
# Validation rejections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_org_band_rejects_bad_kind(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    with pytest.raises(ValidationError, match="kind"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="severity",  # not frequency|magnitude
            label="custom_tier",
            low=1.0,
            mode=3.0,
            high=10.0,
            reason="bad kind",
            user=seed_user,
        )

    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_create_org_band_rejects_bad_label_pattern(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    with pytest.raises(ValidationError, match="label"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="frequency",
            label="Custom Tier!",  # uppercase + space + punctuation
            low=1.0,
            mode=3.0,
            high=10.0,
            reason="bad label",
            user=seed_user,
        )

    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_create_org_band_rejects_label_over_40_chars(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    with pytest.raises(ValidationError, match="label"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="frequency",
            label="a" * 41,
            low=1.0,
            mode=3.0,
            high=10.0,
            reason="label too long",
            user=seed_user,
        )


@pytest.mark.asyncio
async def test_create_org_band_rejects_ordering_violation(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    with pytest.raises(ValidationError, match="low <= mode <= high"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="frequency",
            label="custom_tier",
            low=5.0,
            mode=2.0,  # mode < low — violates ordering
            high=10.0,
            reason="bad ordering",
            user=seed_user,
        )

    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_create_org_band_rejects_degenerate_zero_width_band(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    """low == high (degenerate) must be rejected even though low <= mode <= high holds."""
    svc = QualitativeBandService(db_session)
    with pytest.raises(ValidationError, match="strictly less than"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="frequency",
            label="custom_tier",
            low=5.0,
            mode=5.0,
            high=5.0,
            reason="degenerate band",
            user=seed_user,
        )


@pytest.mark.asyncio
async def test_create_org_band_rejects_non_positive_low(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    with pytest.raises(ValidationError, match="strictly positive"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="frequency",
            label="custom_tier",
            low=0.0,
            mode=3.0,
            high=10.0,
            reason="zero low",
            user=seed_user,
        )


@pytest.mark.asyncio
async def test_create_org_band_rejects_duplicate_active(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="first",
        user=seed_user,
    )

    with pytest.raises(ValidationError, match="already exists"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="frequency",
            label="custom_tier",
            low=2.0,
            mode=5.0,
            high=15.0,
            reason="second — should fail",
            user=seed_user,
        )

    rows = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_create_org_band_rejects_blank_reason(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    with pytest.raises(ValidationError, match="reason"):
        await svc.create_org_band(
            organization_id=seed_organization.id,
            kind="frequency",
            label="custom_tier",
            low=1.0,
            mode=3.0,
            high=10.0,
            reason="   ",
            user=seed_user,
        )


# ---------------------------------------------------------------------------
# IDOR — update AND delete cross-org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_org_band_cross_org_raises_not_found(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_organization_factory: Callable[..., Awaitable[Organization]],
) -> None:
    svc = QualitativeBandService(db_session)
    band = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="org A band",
        user=seed_user,
    )

    org_b = await seed_organization_factory(name="Org B")

    with pytest.raises(NotFoundError):
        await svc.update_org_band(
            organization_id=org_b.id,  # wrong org
            band_id=band.id,
            low=2.0,
            mode=6.0,
            high=20.0,
            reason="cross-org attempt",
            expected_row_version=1,
            user=seed_user,
        )

    # Row is untouched.
    await db_session.refresh(band)
    assert band.row_version == 1
    assert (band.low, band.mode, band.high) == (1.0, 3.0, 10.0)


@pytest.mark.asyncio
async def test_delete_org_band_cross_org_raises_not_found(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_organization_factory: Callable[..., Awaitable[Organization]],
) -> None:
    svc = QualitativeBandService(db_session)
    band = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="org A band",
        user=seed_user,
    )

    org_b = await seed_organization_factory(name="Org B")

    with pytest.raises(NotFoundError):
        await svc.delete_org_band(
            organization_id=org_b.id,  # wrong org
            band_id=band.id,
            user=seed_user,
        )

    await db_session.refresh(band)
    assert band.deleted_at is None  # untouched


@pytest.mark.asyncio
async def test_update_org_band_missing_band_raises_not_found(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    with pytest.raises(NotFoundError):
        await svc.update_org_band(
            organization_id=seed_organization.id,
            band_id=uuid.uuid4(),
            low=1.0,
            mode=3.0,
            high=10.0,
            reason="no such band",
            expected_row_version=1,
            user=seed_user,
        )


# ---------------------------------------------------------------------------
# Optimistic-lock conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_org_band_stale_row_version_raises_conflict(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    svc = QualitativeBandService(db_session)
    band = await svc.create_org_band(
        organization_id=seed_organization.id,
        kind="frequency",
        label="custom_tier",
        low=1.0,
        mode=3.0,
        high=10.0,
        reason="initial",
        user=seed_user,
    )

    # First update succeeds, bumping row_version 1 -> 2.
    await svc.update_org_band(
        organization_id=seed_organization.id,
        band_id=band.id,
        low=2.0,
        mode=6.0,
        high=20.0,
        reason="first update",
        expected_row_version=1,
        user=seed_user,
    )

    # Second update replays the now-stale expected_row_version=1.
    with pytest.raises(QualitativeBandVersionConflictError):
        await svc.update_org_band(
            organization_id=seed_organization.id,
            band_id=band.id,
            low=3.0,
            mode=9.0,
            high=30.0,
            reason="stale retry",
            expected_row_version=1,
            user=seed_user,
        )

    await db_session.refresh(band)
    assert band.row_version == 2
    assert (band.low, band.mode, band.high) == (2.0, 6.0, 20.0)
