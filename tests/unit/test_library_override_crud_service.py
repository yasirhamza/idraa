"""F9 — ScenarioLibraryService override CRUD: create / update / delete tombstone.

9 unit tests covering the three new service methods plus the key guards
(IDOR, optimistic-lock, shape-signature discipline).

Spec: docs/superpowers/specs/2026-04-28-phase-1.5a-scenario-library-design.md
§12.1 (override tombstone policy) + §7.2 (service layer).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import (
    IDORError,
    LibraryOverrideAlreadyExistsError,
    ValidationError,
)
from idraa.models.scenario_library import ScenarioLibraryOverride
from idraa.models.user import User
from idraa.services.scenario_library import OverrideDraft, ScenarioLibraryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _draft(
    tef: dict[str, Any] | None = None,
    vuln: dict[str, Any] | None = None,
    pl: dict[str, Any] | None = None,
    sl: dict[str, Any] | None = None,
) -> OverrideDraft:
    return OverrideDraft(
        threat_event_frequency=tef,
        vulnerability=vuln,
        primary_loss=pl,
        secondary_loss=sl,
    )


def _pert(low: float = 1.0, mode: float = 4.0, high: float = 12.0) -> dict[str, Any]:
    return {"distribution": "PERT", "low": low, "mode": mode, "high": high}


def _normal(mean: float = 5.0, std: float = 2.0) -> dict[str, Any]:
    return {"distribution": "Normal", "mean": mean, "std": std}


# ---------------------------------------------------------------------------
# Test 1: create_override writes row + audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_override_writes_row_and_audit(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    draft = _draft(tef=_pert())

    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=draft,
        reason="unit-test baseline",
        user=seed_user,
    )

    assert override.id is not None
    assert override.version == 1
    assert override.organization_id == seed_organization.id  # type: ignore[attr-defined]
    assert override.library_entry_id == seed_library_entry.id  # type: ignore[attr-defined]
    assert override.threat_event_frequency == _pert()
    assert override.deleted_at is None

    # Audit row must exist
    from sqlalchemy import select

    from idraa.models.audit_log import AuditLog

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == override.id,
                    AuditLog.action == "library_override.create",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].user_id == seed_user.id


# ---------------------------------------------------------------------------
# Test 2: create_override duplicate raises LibraryOverrideAlreadyExistsError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_override_duplicate_raises(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    draft = _draft(tef=_pert())

    await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=draft,
        reason="first",
        user=seed_user,
    )
    await db_session.flush()

    with pytest.raises(LibraryOverrideAlreadyExistsError):
        await svc.create_override(
            entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=draft,
            reason="second — should fail",
            user=seed_user,
        )


# ---------------------------------------------------------------------------
# Test 3: update_override bumps version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_override_bumps_version(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert()),
        reason="initial",
        user=seed_user,
    )
    await db_session.flush()

    updated = await svc.update_override(
        override_id=override.id,
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert(low=2.0, mode=8.0, high=24.0)),
        reason="bumped",
        methodology_change_reason=None,
        user=seed_user,
        expected_version=1,
    )

    assert updated.version == 2
    assert updated.row_version == 2
    assert updated.threat_event_frequency == _pert(low=2.0, mode=8.0, high=24.0)


# ---------------------------------------------------------------------------
# Test 4: delete_override tombstones the row (preserves it)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_override_tombstone_preserves_row(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert()),
        reason="to be deleted",
        user=seed_user,
    )
    await db_session.flush()
    override_id = override.id

    tombstoned = await svc.delete_override(
        override_id=override_id,
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        user=seed_user,
    )

    assert tombstoned.deleted_at is not None

    # Row still exists in DB (soft-delete, not hard-delete)
    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(ScenarioLibraryOverride).where(ScenarioLibraryOverride.id == override_id)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.deleted_at is not None

    # get_override no longer surfaces it (tombstoned = invisible to normal lookup)
    active = await svc.repo.get_override(
        seed_organization.id,  # type: ignore[attr-defined]
        seed_library_entry.id,  # type: ignore[attr-defined]
    )
    assert active is None


# ---------------------------------------------------------------------------
# Test 5: update_override cross-org raises IDORError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_override_cross_org_raises_idor(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert()),
        reason="initial",
        user=seed_user,
    )
    await db_session.flush()

    other_org_id = uuid.uuid4()

    with pytest.raises(IDORError):
        await svc.update_override(
            override_id=override.id,
            organization_id=other_org_id,  # wrong org
            draft=_draft(tef=_pert(low=2.0, mode=8.0, high=24.0)),
            reason="cross-org attempt",
            methodology_change_reason=None,
            user=seed_user,
            expected_version=1,
        )


# ---------------------------------------------------------------------------
# Test 6: delete_override cross-org raises IDORError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_override_cross_org_raises_idor(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert()),
        reason="to be cross-org deleted",
        user=seed_user,
    )
    await db_session.flush()

    other_org_id = uuid.uuid4()

    with pytest.raises(IDORError):
        await svc.delete_override(
            override_id=override.id,
            organization_id=other_org_id,  # wrong org
            user=seed_user,
        )


# ---------------------------------------------------------------------------
# Test 7: update_override requires methodology_change_reason on shape change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_override_methodology_change_required_on_shape_change(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """Shape change (leg added: tef goes from None to a PERT dict) without
    methodology_change_reason must raise ValidationError."""
    svc = ScenarioLibraryService(db_session)
    # Create override with tef=None (no distribution set)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(),  # all None
        reason="initial blank",
        user=seed_user,
    )
    await db_session.flush()

    # Update: add a distribution (None → PERT = shape change)
    with pytest.raises(ValidationError, match="methodology_change_reason"):
        await svc.update_override(
            override_id=override.id,
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=_draft(tef=_pert()),  # leg added
            reason="shape added",
            methodology_change_reason=None,  # missing — must raise
            user=seed_user,
            expected_version=1,
        )


# ---------------------------------------------------------------------------
# Test 8: update_override methodology_change_reason optional on pure param tuning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_override_methodology_change_optional_on_param_tuning(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """Pure param tuning (same distribution kind, different low/mode/high)
    must succeed without methodology_change_reason."""
    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert(low=1.0, mode=4.0, high=12.0)),
        reason="initial PERT",
        user=seed_user,
    )
    await db_session.flush()

    # Tune low/mode/high but keep PERT — no methodology_change_reason required
    updated = await svc.update_override(
        override_id=override.id,
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert(low=2.0, mode=8.0, high=20.0)),
        reason="tuned tef",
        methodology_change_reason=None,  # OK for pure param tuning
        user=seed_user,
        expected_version=1,
    )
    assert updated.version == 2
    assert updated.threat_event_frequency is not None
    assert updated.threat_event_frequency["mode"] == 8.0


# ---------------------------------------------------------------------------
# Test 9: update_override requires methodology_change_reason on distribution flip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_override_methodology_required_on_distribution_flip(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """PERT → Normal distribution flip must require methodology_change_reason."""
    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert()),
        reason="initial PERT",
        user=seed_user,
    )
    await db_session.flush()

    # Flip distribution kind: PERT → Normal — requires methodology_change_reason
    with pytest.raises(ValidationError, match="methodology_change_reason"):
        await svc.update_override(
            override_id=override.id,
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=_draft(tef=_normal()),  # PERT → Normal flip
            reason="switched to normal",
            methodology_change_reason=None,  # missing — must raise
            user=seed_user,
            expected_version=1,
        )

    # With a reason it must succeed
    updated = await svc.update_override(
        override_id=override.id,
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_normal()),
        reason="switched to normal",
        methodology_change_reason="Switched from PERT to Normal per updated dataset",
        user=seed_user,
        expected_version=1,
    )
    assert updated.threat_event_frequency == _normal()
    assert updated.methodology_change_reason is not None


# ---------------------------------------------------------------------------
# Tests 10-14: #333 — override writes gate through validate_fair_distributions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_override_rejects_nonfinite_pert(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """#333: an inf PERT bound in the draft must raise BEFORE any row write."""
    from sqlalchemy import select

    from idraa.errors import FAIRCAMValidationError

    svc = ScenarioLibraryService(db_session)
    with pytest.raises(FAIRCAMValidationError):
        await svc.create_override(
            entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=_draft(tef=_pert(low=1.0, mode=4.0, high=float("inf"))),
            reason="inf must be rejected",
            user=seed_user,
        )

    rows = (await db_session.execute(select(ScenarioLibraryOverride))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_update_override_rejects_nonfinite_pert_and_does_not_persist(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """#333: inf in an update draft must raise and leave the row untouched."""
    from idraa.errors import FAIRCAMValidationError

    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert()),
        reason="valid baseline",
        user=seed_user,
    )
    await db_session.flush()

    with pytest.raises(FAIRCAMValidationError):
        await svc.update_override(
            override_id=override.id,
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=_draft(tef=_pert(low=1.0, mode=4.0, high=float("inf"))),
            reason="inf must be rejected",
            methodology_change_reason=None,
            user=seed_user,
            expected_version=1,
        )

    await db_session.refresh(override)
    assert override.version == 1
    assert override.threat_event_frequency == _pert()


@pytest.mark.asyncio
async def test_update_override_rejects_lognormal_sigma_over_10(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """#333: sigma > 10 lognormal (Sec-I2 OOM/DoS bound) must be rejected."""
    from idraa.errors import FAIRCAMValidationError

    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(pl=_pert(low=1e5, mode=7.5e5, high=3e6)),
        reason="valid baseline",
        user=seed_user,
    )
    await db_session.flush()

    with pytest.raises(FAIRCAMValidationError):
        await svc.update_override(
            override_id=override.id,
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=_draft(pl={"distribution": "LOGNORMAL", "mean": 13.0, "sigma": 11.0}),
            reason="sigma over bound",
            # Shape flip (PERT → lognormal) legitimately carries a reason so the
            # test exercises the validation gate, not the shape-signature guard.
            methodology_change_reason="flip to lognormal for tail realism",
            user=seed_user,
            expected_version=1,
        )

    await db_session.refresh(override)
    assert override.version == 1


@pytest.mark.asyncio
async def test_create_override_rejects_lognormal_sigma_over_10(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """#333: the sigma > 10 gate must also fire on the CREATE path (review
    finding — both paths funnel through _validate_effective_distributions,
    but the issue requires the regression test on each)."""
    from sqlalchemy import select

    from idraa.errors import FAIRCAMValidationError

    svc = ScenarioLibraryService(db_session)
    with pytest.raises(FAIRCAMValidationError):
        await svc.create_override(
            entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=_draft(pl={"distribution": "LOGNORMAL", "mean": 13.0, "sigma": 11.0}),
            reason="sigma over bound on create",
            user=seed_user,
        )

    rows = (await db_session.execute(select(ScenarioLibraryOverride))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_create_override_merges_canonical_secondary_loss(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """#333 merge-path coverage: when the canonical entry carries a
    secondary_loss and the draft leaves it None, the canonical SL is merged
    into the validated set and a valid override succeeds."""
    seed_library_entry.secondary_loss = {  # type: ignore[attr-defined]
        "distribution": "PERT",
        "low": 50_000.0,
        "mode": 250_000.0,
        "high": 1_000_000.0,
    }
    await db_session.flush()

    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(tef=_pert()),  # sl=None → canonical SL fills the leg
        reason="TEF-only override over entry with canonical SL",
        user=seed_user,
    )
    assert override.id is not None
    assert override.secondary_loss is None  # canonical fall-through intact


@pytest.mark.asyncio
async def test_create_override_partial_draft_validates_merged_canonical(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """#333: a partial draft (only PL supplied) validates the EFFECTIVE merge —
    canonical entry values fill the unsupplied legs — and succeeds."""
    svc = ScenarioLibraryService(db_session)
    override = await svc.create_override(
        entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        draft=_draft(pl=_pert(low=2e5, mode=9e5, high=4e6)),
        reason="PL-only override",
        user=seed_user,
    )
    assert override.id is not None
    assert override.threat_event_frequency is None  # canonical fall-through intact


@pytest.mark.asyncio
async def test_create_override_rejects_vulnerability_out_of_bounds(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: User,
    seed_library_entry: object,
) -> None:
    """#333: vulnerability legs outside [0, 1] must be rejected."""
    from sqlalchemy import select

    from idraa.errors import FAIRCAMValidationError

    svc = ScenarioLibraryService(db_session)
    with pytest.raises(FAIRCAMValidationError):
        await svc.create_override(
            entry_id=seed_library_entry.id,  # type: ignore[attr-defined]
            organization_id=seed_organization.id,  # type: ignore[attr-defined]
            draft=_draft(vuln=_pert(low=0.1, mode=0.5, high=1.5)),
            reason="vuln > 1 must be rejected",
            user=seed_user,
        )

    rows = (await db_session.execute(select(ScenarioLibraryOverride))).scalars().all()
    assert rows == []
