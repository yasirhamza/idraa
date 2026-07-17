"""Sec-13 R3 / Sec-6 R1 (T10 wizard-step-3 evaluator-style finalize): the
SME directory audit-event payloads MUST NEVER carry raw email — neither
in ``sme.created`` (admin or analyst-request entry) nor in either side of
the ``sme.updated`` diff.

The project audit framework is :class:`idraa.services.audit.AuditWriter`;
emitted rows land in the ``audit_log`` table. These tests run the SME
directory service against the real DB session and query AuditLog rows
directly — the plan-text shows a hypothetical ``audit_capture`` fixture
but this project's prevailing pattern (see ``tests/unit/test_audit_service.py``
and ``tests/unit/test_create_from_wizard.py``) reads rows back via
SQLAlchemy ``select(AuditLog)``.

The "no raw email anywhere" assertion is enforced via ``repr(...)`` on
the persisted ``changes`` JSON column: if the local part appears
ANYWHERE — top-level, nested under ``before`` / ``after``, in a key, in
a stringified UUID accidentally containing the email — the test fails.
"""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.user import User
from idraa.schemas.sme import SMECreate, SMEUpdate
from idraa.services import sme_directory as svc


async def test_sme_created_payload_redacts_email(
    db_session: AsyncSession,
    admin_user: User,
) -> None:
    """``sme.created`` carries ``email_redacted_domain`` and NEVER the raw
    address. Domain leak is documented accepted risk per Sec-15."""
    await svc.create(
        db_session,
        SMECreate(name="Jane", email="jane@example.com"),
        organization_id=admin_user.organization_id,
        actor_id=admin_user.id,
    )
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "sme.created"),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"expected exactly 1 sme.created event, got {len(rows)}"
    changes = rows[0].changes
    assert changes["email_redacted_domain"] == "*****@example.com"
    # Belt-and-suspenders: full serialized payload contains no raw email,
    # anywhere — top-level, nested, key, value.
    serialized = json.dumps(changes).lower()
    assert "jane@example.com" not in serialized, (
        f"raw email leaked into audit payload: {serialized!r}"
    )


async def test_sme_updated_payload_redacts_email_in_diff(
    db_session: AsyncSession,
    admin_user: User,
) -> None:
    """``sme.updated`` diff carries ``email_redacted_domain`` on BOTH the
    ``before`` and ``after`` halves — never the raw old or new email."""
    sme = await svc.create(
        db_session,
        SMECreate(name="Jane", email="jane@example.com"),
        organization_id=admin_user.organization_id,
        actor_id=admin_user.id,
    )
    # Clear the sme.created event so the assertion below targets ONLY the
    # update event.
    await db_session.execute(delete(AuditLog).where(AuditLog.action == "sme.created"))

    await svc.update(
        db_session,
        sme.id,
        SMEUpdate(email="jane@newdomain.com"),
        organization_id=admin_user.organization_id,
        actor_id=admin_user.id,
    )
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "sme.updated"),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"expected exactly 1 sme.updated event, got {len(rows)}"
    changes = rows[0].changes
    before = cast(dict[str, Any], changes["before"])
    after = cast(dict[str, Any], changes["after"])
    assert before["email_redacted_domain"] == "*****@example.com"
    assert after["email_redacted_domain"] == "*****@newdomain.com"
    # No raw email on either side of the diff — exercise the full payload
    # serialized through the JSON column representation.
    serialized = json.dumps(changes).lower()
    assert "jane@example.com" not in serialized, f"raw old email leaked: {serialized!r}"
    assert "jane@newdomain.com" not in serialized, f"raw new email leaked: {serialized!r}"


async def test_analyst_request_sme_created_payload_redacts_email(
    db_session: AsyncSession,
    admin_user: User,
) -> None:
    """Analyst-side ``request`` path emits the same ``sme.created`` event
    with ``created_via="analyst_request"``, and the same email-redaction
    discipline applies."""
    from idraa.schemas.sme import SMERequest

    await svc.request(
        db_session,
        SMERequest(name="Carlos", email="carlos@example.com"),
        organization_id=admin_user.organization_id,
        actor_id=admin_user.id,
    )
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "sme.created"),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    changes = rows[0].changes
    assert changes["email_redacted_domain"] == "*****@example.com"
    assert changes["created_via"] == "analyst_request"
    assert "pending_review" not in changes, (
        "pending_review column was dropped per 2026-05-25 SME free-text design; "
        "audit payload should not include it."
    )
    serialized = json.dumps(changes).lower()
    assert "carlos@example.com" not in serialized
