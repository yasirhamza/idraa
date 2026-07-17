"""OverlayService CRUD — phase 1.2.C5.

Verifies create / update / deactivate flow on ``OverlayService``:

- create: flushes OverlayDefinition + OverlayDefinitionRevision(version=1)
  in the same session, writes ``overlay.create`` audit row.
- update: enforces ``expected_version`` (B8 optimistic lock), refuses
  tag rename, no-op when nothing changed (no audit, no revision bump),
  bumps version + writes new revision row + ``overlay.update`` audit
  when something changed.
- deactivate: idempotent on already-inactive (no second audit row),
  writes ``overlay.deactivate`` audit on the first transition.

Action taxonomy follows the project-wide ``<entity>.<verb>`` convention
applied to all audit rows in the calibration framework — diverges from
the ``services/controls.py`` legacy bare-verb pattern, which predates the
preamble fold-in.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.overlay import OverlayDefinition, OverlayDefinitionRevision
from idraa.models.user import User
from idraa.schemas.overlay import OverlayForm
from idraa.services.overlays import (
    OverlayService,
    OverlayVersionConflictError,
)


def _form(
    *,
    tag: str = "novel_overlay",
    display_name: str = "Novel Overlay",
    frequency_multiplier: float = 1.5,
    magnitude_multiplier: float = 2.0,
    sources: list[str] | None = None,
    methodology: str = "Synthesised from threat-intel feeds and incident telemetry.",
    methodology_change_reason: str = "initial creation",
) -> OverlayForm:
    return OverlayForm(
        tag=tag,
        display_name=display_name,
        frequency_multiplier=frequency_multiplier,
        magnitude_multiplier=magnitude_multiplier,
        sources=list(sources) if sources is not None else ["NIST SP 800-30"],
        methodology=methodology,
        methodology_change_reason=methodology_change_reason,
    )


async def _audit_rows(db: AsyncSession, *, entity_id: uuid.UUID) -> list[AuditLog]:
    rows = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == "overlay", AuditLog.entity_id == entity_id)
        .order_by(AuditLog.timestamp)
    )
    return list(rows.scalars().all())


async def test_create_writes_overlay_revision_and_audit(
    db_session: AsyncSession, organization: Organization, admin_user: User
) -> None:
    """create flushes overlay + revision(version=1) + ``overlay.create`` audit row."""
    svc = OverlayService(db_session)
    actor_id = admin_user.id
    form = _form()

    od = await svc.create(
        organization_id=organization.id,
        user_id=actor_id,
        form=form,
        ip_address="10.0.0.1",
    )

    assert od.id is not None
    assert od.version == 1
    assert od.is_active is True
    assert od.tag == "novel_overlay"

    revs = (
        (
            await db_session.execute(
                select(OverlayDefinitionRevision).where(
                    OverlayDefinitionRevision.overlay_definition_id == od.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(revs) == 1
    assert revs[0].version == 1
    assert revs[0].tag == "novel_overlay"
    assert revs[0].methodology_change_reason == "initial creation"
    assert revs[0].created_by_user_id == actor_id

    audits = await _audit_rows(db_session, entity_id=od.id)
    assert len(audits) == 1
    assert audits[0].action == "overlay.create"
    assert audits[0].user_id == actor_id
    assert audits[0].ip_address == "10.0.0.1"


async def test_update_with_changes_bumps_version_and_revision_and_audit(
    db_session: AsyncSession,
    organization: Organization,
    seeded_critical_infrastructure_overlay: OverlayDefinition,
    admin_user: User,
) -> None:
    """update applies change, bumps version, writes new revision + audit.

    Flips a scalar (``display_name``), a numeric (``frequency_multiplier``),
    AND a list (``sources``) in one call so the apply loop's list-rewrap
    branch (``setattr(overlay, field, list(new_val))``) is exercised.
    Asserts list identity is fresh — the rewrap defends against shared-
    reference aliasing if the caller mutates ``form.sources`` after the
    call returns.
    """
    svc = OverlayService(db_session)
    od = seeded_critical_infrastructure_overlay
    starting_version = od.version
    starting_freq = od.frequency_multiplier
    actor_id = admin_user.id

    new_sources = ["NIST SP 800-30", "ENISA Threat Landscape 2026"]
    new_freq = starting_freq + 0.25
    new_form = _form(
        tag=od.tag,  # tag UNCHANGED — rename is rejected by service
        display_name="Critical Infrastructure (revised)",
        frequency_multiplier=new_freq,
        magnitude_multiplier=od.magnitude_multiplier,
        sources=new_sources,
        methodology=od.methodology,
        methodology_change_reason="re-baseline display name post-2026 review",
    )

    updated = await svc.update(
        overlay=od,
        user_id=actor_id,
        form=new_form,
        expected_version=starting_version,
        ip_address="10.0.0.2",
    )

    assert updated.version == starting_version + 1
    assert updated.display_name == "Critical Infrastructure (revised)"
    assert updated.frequency_multiplier == new_freq
    assert updated.sources == new_form.sources
    # List-rewrap defence: stored sources must not alias the form's list,
    # so a later caller mutation can't silently corrupt the ORM row.
    assert od.sources is not new_form.sources

    revs = (
        (
            await db_session.execute(
                select(OverlayDefinitionRevision)
                .where(OverlayDefinitionRevision.overlay_definition_id == od.id)
                .order_by(OverlayDefinitionRevision.version)
            )
        )
        .scalars()
        .all()
    )
    assert [r.version for r in revs] == [1, starting_version + 1]
    assert revs[-1].display_name == "Critical Infrastructure (revised)"
    assert revs[-1].frequency_multiplier == new_freq
    assert revs[-1].sources == new_sources
    assert revs[-1].methodology_change_reason == "re-baseline display name post-2026 review"
    assert revs[-1].created_by_user_id == actor_id

    audits = await _audit_rows(db_session, entity_id=od.id)
    update_audits = [a for a in audits if a.action == "overlay.update"]
    assert len(update_audits) == 1
    assert "display_name" in update_audits[0].changes
    assert "frequency_multiplier" in update_audits[0].changes
    assert "sources" in update_audits[0].changes
    assert update_audits[0].changes["version"] == [starting_version, starting_version + 1]


async def test_update_no_changes_is_noop(
    db_session: AsyncSession,
    organization: Organization,
    seeded_critical_infrastructure_overlay: OverlayDefinition,
) -> None:
    """update with form matching current state is a no-op: no version bump, no audit, no revision."""
    svc = OverlayService(db_session)
    od = seeded_critical_infrastructure_overlay
    starting_version = od.version

    same_form = _form(
        tag=od.tag,
        display_name=od.display_name,
        frequency_multiplier=od.frequency_multiplier,
        magnitude_multiplier=od.magnitude_multiplier,
        sources=list(od.sources),
        methodology=od.methodology,
        methodology_change_reason="forced rebaseline that should be ignored",
    )

    result = await svc.update(
        overlay=od,
        user_id=uuid.uuid4(),
        form=same_form,
        expected_version=starting_version,
    )

    assert result.version == starting_version

    revs = (
        (
            await db_session.execute(
                select(OverlayDefinitionRevision).where(
                    OverlayDefinitionRevision.overlay_definition_id == od.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(revs) == 1  # only the seed revision

    audits = await _audit_rows(db_session, entity_id=od.id)
    assert [a.action for a in audits] == []


async def test_update_with_stale_expected_version_raises_conflict(
    db_session: AsyncSession,
    seeded_critical_infrastructure_overlay: OverlayDefinition,
) -> None:
    """B8 optimistic lock: stale expected_version raises OverlayVersionConflictError
    naming both expected and actual version values."""
    svc = OverlayService(db_session)
    od = seeded_critical_infrastructure_overlay
    assert od.version == 1  # seeded fresh

    form = _form(
        tag=od.tag,
        display_name="something different",
        methodology=od.methodology,
        methodology_change_reason="stale-version test",
    )

    with pytest.raises(OverlayVersionConflictError) as exc_info:
        await svc.update(
            overlay=od,
            user_id=uuid.uuid4(),
            form=form,
            expected_version=99,
        )

    msg = str(exc_info.value)
    assert "1" in msg, f"expected actual version in message, got: {msg!r}"
    assert "99" in msg, f"expected expected_version in message, got: {msg!r}"


async def test_update_with_tag_rename_raises_value_error(
    db_session: AsyncSession,
    seeded_critical_infrastructure_overlay: OverlayDefinition,
) -> None:
    """Service rejects tag rename — caller must deactivate + create-new instead."""
    svc = OverlayService(db_session)
    od = seeded_critical_infrastructure_overlay

    form = _form(
        tag="something_else",
        display_name=od.display_name,
        frequency_multiplier=od.frequency_multiplier,
        magnitude_multiplier=od.magnitude_multiplier,
        sources=list(od.sources),
        methodology=od.methodology,
        methodology_change_reason="trying to rename",
    )

    with pytest.raises(ValueError) as exc_info:
        await svc.update(
            overlay=od,
            user_id=uuid.uuid4(),
            form=form,
            expected_version=od.version,
        )
    assert "tag rename" in str(exc_info.value).lower()


async def test_deactivate_writes_audit_and_marks_inactive(
    db_session: AsyncSession,
    seeded_critical_infrastructure_overlay: OverlayDefinition,
    admin_user: User,
) -> None:
    svc = OverlayService(db_session)
    od = seeded_critical_infrastructure_overlay
    actor_id = admin_user.id

    await svc.deactivate(
        overlay=od,
        user_id=actor_id,
        reason="superseded by 2026 revision",
        ip_address="10.0.0.3",
    )

    assert od.is_active is False

    audits = await _audit_rows(db_session, entity_id=od.id)
    deactivate_audits = [a for a in audits if a.action == "overlay.deactivate"]
    assert len(deactivate_audits) == 1
    assert deactivate_audits[0].user_id == actor_id
    assert deactivate_audits[0].changes.get("reason") == [None, "superseded by 2026 revision"]


async def test_deactivate_idempotent_on_already_inactive(
    db_session: AsyncSession,
    seeded_critical_infrastructure_overlay: OverlayDefinition,
    admin_user: User,
) -> None:
    """Calling deactivate on an already-inactive overlay is a no-op (no second audit row)."""
    svc = OverlayService(db_session)
    od = seeded_critical_infrastructure_overlay

    await svc.deactivate(overlay=od, user_id=admin_user.id, reason="first")
    await svc.deactivate(overlay=od, user_id=admin_user.id, reason="second")

    audits = await _audit_rows(db_session, entity_id=od.id)
    deactivate_audits = [a for a in audits if a.action == "overlay.deactivate"]
    assert len(deactivate_audits) == 1, (
        "second deactivate should be a no-op; only one overlay.deactivate audit row expected"
    )
    # Regression sentinel: surviving audit row records the FIRST call's
    # reason. If idempotency were weakened to allow the second call to
    # overwrite, the row count alone wouldn't catch it — but the reason
    # would silently flip to "second".
    assert deactivate_audits[0].changes["reason"] == [None, "first"]
