"""AuditWriter transactional smoke tests — Task 1.1.3.

JSON-safety regression tests (issue #125) live at the bottom of the file
and exercise the recursive coercion contract that AuditWriter.log applies
to its ``changes`` payload before storing into the SQLAlchemy ``JSON`` column.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import EntityStatus, IndustryType, OrganizationSize
from idraa.models.organization import Organization
from idraa.services.audit import AuditWriter


async def test_audit_writer_writes_row(db_session: AsyncSession) -> None:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()

    writer = AuditWriter(db_session)
    await writer.log(
        organization_id=org.id,
        entity_type="organization",
        entity_id=org.id,
        action="create",
        changes={"name": [None, "Acme"]},
        user_id=None,
    )
    await db_session.commit()

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "create"
    assert rows[0].changes == {"name": [None, "Acme"]}


async def test_audit_writer_rolls_back_with_caller(db_session: AsyncSession) -> None:
    """If the caller's transaction rolls back, the audit row disappears with it.

    This is the invariant AuditWriter's docstring promises: the writer adds to
    the caller's session without committing, so a caller rollback discards the
    audit write atomically with the business change that triggered it.
    """
    org = Organization(
        name="Acme",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()

    writer = AuditWriter(db_session)
    await writer.log(
        organization_id=org.id,
        entity_type="organization",
        entity_id=org.id,
        action="create",
        changes={"name": [None, "Acme"]},
        user_id=None,
    )

    await db_session.rollback()

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# Issue #125 — JSON-safety regression (6 tests + 1 idempotency)
#
# AuditWriter.log must recursively coerce Decimal / UUID / datetime / date /
# Enum values inside ``changes`` before storing, so SQLAlchemy's default
# ``json.dumps`` doesn't raise ``TypeError: Object of type X is not JSON
# serializable``. Pre-fix, an edit that changed a Decimal column (e.g.
# Control.annual_cost 0 → 10000) produced a 500 — HTMX swallowed it and the
# save button looked unresponsive.
# ---------------------------------------------------------------------------


async def _seed_org(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="JsonSafety",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()
    return org


async def test_audit_writer_coerces_decimal_in_changes(db_session: AsyncSession) -> None:
    """Decimal pairs flatten to string form — round-trip preserves precision."""
    org = await _seed_org(db_session)
    writer = AuditWriter(db_session)
    await writer.log(
        organization_id=org.id,
        entity_type="control",
        entity_id=org.id,
        action="control.update",
        changes={"annual_cost": [Decimal("0.00"), Decimal("10000")]},
        user_id=None,
    )
    await db_session.commit()
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.changes == {"annual_cost": ["0.00", "10000"]}
    # Round-trip — Decimal-from-string preserves the original numeric value.
    assert Decimal(row.changes["annual_cost"][0]) == Decimal("0.00")
    assert Decimal(row.changes["annual_cost"][1]) == Decimal("10000")


async def test_audit_writer_coerces_uuid_in_changes(db_session: AsyncSession) -> None:
    """UUIDs flatten to string form; round-trip via uuid.UUID(s) succeeds."""
    org = await _seed_org(db_session)
    writer = AuditWriter(db_session)
    new_id = uuid.uuid4()
    await writer.log(
        organization_id=org.id,
        entity_type="control_function_assignment",
        entity_id=org.id,
        action="control_function_assignment.create",
        changes={"control_id": [None, new_id]},
        user_id=None,
    )
    await db_session.commit()
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.changes == {"control_id": [None, str(new_id)]}
    assert uuid.UUID(row.changes["control_id"][1]) == new_id


async def test_audit_writer_coerces_datetime_and_date_in_changes(
    db_session: AsyncSession,
) -> None:
    """datetime → ISO 8601 with tz; date → ISO 8601 without tz."""
    org = await _seed_org(db_session)
    writer = AuditWriter(db_session)
    ts = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
    d = date(2026, 5, 14)
    await writer.log(
        organization_id=org.id,
        entity_type="control_function_assignment",
        entity_id=org.id,
        action="control_function_assignment.confirm",
        changes={
            "confirmed_by_user_at": [None, ts],
            "review_due": [None, d],
        },
        user_id=None,
    )
    await db_session.commit()
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.changes["confirmed_by_user_at"] == [None, "2026-05-14T12:00:00+00:00"]
    assert row.changes["review_due"] == [None, "2026-05-14"]


async def test_audit_writer_coerces_enum_in_changes(db_session: AsyncSession) -> None:
    """Enum → e.value (a str-enum here, but any Enum.value is fine)."""
    org = await _seed_org(db_session)
    writer = AuditWriter(db_session)
    await writer.log(
        organization_id=org.id,
        entity_type="control",
        entity_id=org.id,
        action="control.update",
        changes={"status": [EntityStatus.ACTIVE, EntityStatus.DELETED]},
        user_id=None,
    )
    await db_session.commit()
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.changes == {"status": ["active", "deleted"]}


async def test_audit_writer_recurses_through_nested_structures(
    db_session: AsyncSession,
) -> None:
    """Nested dicts and lists recurse — Decimal inside dict inside list works."""
    org = await _seed_org(db_session)
    writer = AuditWriter(db_session)
    id_a, id_b = uuid.uuid4(), uuid.uuid4()
    await writer.log(
        organization_id=org.id,
        entity_type="organization",
        entity_id=org.id,
        action="organization.update",
        changes={
            "meta": {
                "price": Decimal("9.99"),
                "ids": [id_a, id_b],
                "tier": EntityStatus.ACTIVE,
            }
        },
        user_id=None,
    )
    await db_session.commit()
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.changes == {
        "meta": {
            "price": "9.99",
            "ids": [str(id_a), str(id_b)],
            "tier": "active",
        }
    }


async def test_audit_writer_is_idempotent_on_precoerced_values(
    db_session: AsyncSession,
) -> None:
    """Already-string values pass through unchanged — callers that pre-coerced
    (e.g. confirm_assignment's `.isoformat()`) keep working without double-encoding."""
    org = await _seed_org(db_session)
    writer = AuditWriter(db_session)
    pre = {
        "confirmed_by_user_at": [None, "2026-05-14T12:00:00+00:00"],
        "name": [None, "Acme"],
        "count": [0, 1],
        "ratio": [0.5, 0.75],
        "active": [False, True],
        "absent": [None, None],
    }
    await writer.log(
        organization_id=org.id,
        entity_type="organization",
        entity_id=org.id,
        action="organization.update",
        changes=pre,
        user_id=None,
    )
    await db_session.commit()
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.changes == pre
