"""Spec-18 R3 (T10 wizard-step-3): ``sme.iris_materialized`` is the SOLE
audit event for the IRIS-baseline (system-owned) SME's creation.
``sme.created`` MUST NOT fire on the lazy-create branch — that event is
reserved for user-created SMEs.

Idempotency: the second call to ``get_or_create_iris_sme`` for an org
that already has the IRIS row returns it without writing any audit row.

The plan-text shows a hypothetical ``audit_capture`` fixture; this
project's prevailing pattern (see ``tests/unit/test_audit_service.py``)
queries the AuditLog table directly via SQLAlchemy. These tests follow
that pattern.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.services import sme_directory as svc


async def test_iris_first_call_emits_only_iris_materialized(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """First call to ``get_or_create_iris_sme`` emits exactly one
    ``sme.iris_materialized`` event and ZERO ``sme.created`` events."""
    sme, created = await svc.get_or_create_iris_sme(db_session, organization.id)
    assert created is True, "expected lazy-create branch on first call"
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_id == sme.id),
            )
        )
        .scalars()
        .all()
    )
    actions = [r.action for r in rows]
    assert "sme.iris_materialized" in actions, (
        f"missing sme.iris_materialized; got actions={actions!r}"
    )
    assert "sme.created" not in actions, (
        f"Spec-18 R3: sme.created MUST NOT fire for the IRIS baseline row; got actions={actions!r}"
    )
    iris_events = [r for r in rows if r.action == "sme.iris_materialized"]
    assert len(iris_events) == 1, (
        f"expected exactly 1 sme.iris_materialized event, got {len(iris_events)}"
    )
    payload = iris_events[0].changes
    assert payload["sme_id"] == str(sme.id)
    assert payload["organization_id"] == str(organization.id)


async def test_iris_second_call_emits_no_audit(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """Second call to ``get_or_create_iris_sme`` is the idempotent-lookup
    path: ``created=False`` and ZERO new audit rows are written."""
    # Seed the IRIS row + clear the audit log to isolate the second call.
    sme, created = await svc.get_or_create_iris_sme(db_session, organization.id)
    assert created is True
    await db_session.execute(delete(AuditLog))
    await db_session.commit()

    sme2, created2 = await svc.get_or_create_iris_sme(db_session, organization.id)
    await db_session.commit()
    assert created2 is False, "expected idempotent-lookup branch on second call"
    assert sme2.id == sme.id, "must return the same IRIS row"

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert rows == [], f"second call must be audit-silent; got {[r.action for r in rows]!r}"
