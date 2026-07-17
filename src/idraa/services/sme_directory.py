"""Per-org SME directory service. See spec §7.2."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.sme import SubjectMatterExpert
from idraa.schemas.sme import (
    SMECreate,
    SMERequest,
    SMEUpdate,
    SubjectMatterExpertDropdownView,
)
from idraa.services.audit import AuditWriter, redact_email


class SMEAlreadyExistsError(ValueError):
    """Live (non-archived) SME with the same email already exists for the org."""


class SMEArchivedEmailCollisionError(ValueError):
    """Archived SME with the same email exists; admin must un-archive instead."""


class SMENotFoundError(LookupError):
    """No SME with this id is visible under the requested visibility flags."""


class SMESystemOwnedImmutableError(ValueError):
    """update/archive/unarchive attempted on the IRIS-baseline (system-owned) SME."""


def _normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    return unicodedata.normalize("NFKC", email).strip()


def live_sme_query(stmt: Select[Any]) -> Select[Any]:
    """Arch-22 R2: single source of truth for 'live SME' filter."""
    return stmt.where(
        SubjectMatterExpert.archived_at.is_(None),
    )


async def get_sme_for_org(
    db: AsyncSession,
    sme_id: UUID,
    organization_id: UUID,
    *,
    allow_archived: bool = False,
    allow_system_owned: bool = True,
) -> SubjectMatterExpert:
    stmt = select(SubjectMatterExpert).where(
        SubjectMatterExpert.id == sme_id,
        SubjectMatterExpert.organization_id == organization_id,
    )
    if not allow_archived:
        stmt = stmt.where(SubjectMatterExpert.archived_at.is_(None))
    if not allow_system_owned:
        stmt = stmt.where(SubjectMatterExpert.is_system_owned.is_(False))
    sme = (await db.execute(stmt)).scalar_one_or_none()
    if sme is None:
        raise SMENotFoundError(str(sme_id))
    return sme


async def _check_email_collision(
    db: AsyncSession,
    organization_id: UUID,
    email_normalized: str | None,
) -> None:
    if email_normalized is None:
        return
    email_lower = email_normalized.lower()
    archived = (
        await db.execute(
            select(SubjectMatterExpert).where(
                SubjectMatterExpert.organization_id == organization_id,
                SubjectMatterExpert.email_lower == email_lower,
                SubjectMatterExpert.archived_at.is_not(None),
                SubjectMatterExpert.is_system_owned.is_(False),
            )
        )
    ).scalar_one_or_none()
    if archived is not None:
        raise SMEArchivedEmailCollisionError(email_normalized)


async def create(
    db: AsyncSession,
    data: SMECreate,
    *,
    organization_id: UUID,
    actor_id: UUID,
) -> SubjectMatterExpert:
    """Admin-side. Sec-5 R1: rejects on archived-email collision."""
    email_norm = _normalize_email(data.email)
    await _check_email_collision(db, organization_id, email_norm)
    sme = SubjectMatterExpert(
        organization_id=organization_id,
        name=data.name,
        email=email_norm,
        role_title=data.role_title,
        notes=data.notes,
        created_by=actor_id,
        created_via="admin",
    )
    db.add(sme)
    try:
        await db.flush()
    except IntegrityError as e:
        raise SMEAlreadyExistsError(email_norm or data.name) from e
    # T10 Sec-6/Sec-13: email_redacted_domain — NEVER raw.
    await AuditWriter(db).log(
        organization_id=organization_id,
        entity_type="sme",
        entity_id=sme.id,
        action="sme.created",
        changes={
            "sme_id": str(sme.id),
            "name": sme.name,
            "email_redacted_domain": redact_email(sme.email),
            "role_title": sme.role_title,
            "created_via": sme.created_via,
        },
        user_id=actor_id,
    )
    return sme


async def request(
    db: AsyncSession,
    data: SMERequest,
    *,
    organization_id: UUID,
    actor_id: UUID,
) -> SubjectMatterExpert:
    """Analyst-side. Sec-26 PR2 reject-loop block."""
    # Sec-26 PR2 fix: previously-rejected SME (analyst_request_rejected)
    # for the same email may not be re-requested by anyone in the org.
    # Admin must explicitly un-archive instead. Without this check the
    # analyst can spam pending-review queue indefinitely.
    email_norm = _normalize_email(data.email)
    if email_norm is not None:
        prior_rejected = (
            await db.execute(
                select(SubjectMatterExpert).where(
                    SubjectMatterExpert.organization_id == organization_id,
                    SubjectMatterExpert.email_lower == email_norm.lower(),
                    SubjectMatterExpert.created_via == "analyst_request_rejected",
                    SubjectMatterExpert.archived_at.is_not(
                        None
                    ),  # defense-in-depth: reject() always co-sets archived_at, so this is belt-and-suspenders
                )
            )
        ).scalar_one_or_none()
        if prior_rejected is not None:
            raise SMEArchivedEmailCollisionError(
                f"Email {email_norm} was previously rejected; ask admin to un-archive"
            )
    sme = SubjectMatterExpert(
        organization_id=organization_id,
        name=data.name,
        email=email_norm,
        role_title=data.role_title,
        created_by=actor_id,
        created_via="analyst_request",
    )
    db.add(sme)
    try:
        await db.flush()
    except IntegrityError as e:
        raise SMEAlreadyExistsError(email_norm or data.name) from e
    # T10 Sec-6/Sec-13: same sme.created event as admin create, but with
    # created_via="analyst_request" reflecting the analyst-request lifecycle.
    await AuditWriter(db).log(
        organization_id=organization_id,
        entity_type="sme",
        entity_id=sme.id,
        action="sme.created",
        changes={
            "sme_id": str(sme.id),
            "name": sme.name,
            "email_redacted_domain": redact_email(sme.email),
            "role_title": sme.role_title,
            "created_via": sme.created_via,
        },
        user_id=actor_id,
    )
    return sme


async def _ensure_not_system_owned(
    db: AsyncSession,
    sme_id: UUID,
    organization_id: UUID,
    *,
    allow_archived: bool = False,
) -> SubjectMatterExpert:
    """Sec-7/Sec-19 PR1 fix: load with allow_system_owned=True then explicitly
    raise SMESystemOwnedImmutableError (422) when target is system-owned.
    Previously routed via SMENotFoundError which translated to 404."""
    sme = await get_sme_for_org(
        db,
        sme_id,
        organization_id,
        allow_archived=allow_archived,
        allow_system_owned=True,
    )
    if sme.is_system_owned:
        raise SMESystemOwnedImmutableError(
            f"SME {sme_id} is system-owned (IRIS baseline); cannot mutate"
        )
    return sme


async def update(
    db: AsyncSession,
    sme_id: UUID,
    data: SMEUpdate,
    *,
    organization_id: UUID,
    actor_id: UUID,
) -> SubjectMatterExpert:
    sme = await _ensure_not_system_owned(db, sme_id, organization_id)
    # T10 Sec-13: capture before-state for the audit diff; email is
    # presented in `email_redacted_domain` form so the audit row NEVER
    # carries the raw local part on either side of the diff.
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if data.name is not None and data.name != sme.name:
        before["name"] = sme.name
        after["name"] = data.name
        sme.name = data.name
    if data.email is not None:
        new_email_norm = _normalize_email(data.email)
        if new_email_norm != sme.email:
            before["email_redacted_domain"] = redact_email(sme.email)
            after["email_redacted_domain"] = redact_email(new_email_norm)
            sme.email = new_email_norm
    if data.role_title is not None and data.role_title != sme.role_title:
        before["role_title"] = sme.role_title
        after["role_title"] = data.role_title
        sme.role_title = data.role_title
    if data.notes is not None and data.notes != sme.notes:
        before["notes"] = sme.notes
        after["notes"] = data.notes
        sme.notes = data.notes
    await db.flush()
    # No-op edit: skip the audit emission (mirrors ScenarioService.update).
    if after:
        await AuditWriter(db).log(
            organization_id=organization_id,
            entity_type="sme",
            entity_id=sme.id,
            action="sme.updated",
            changes={
                "sme_id": str(sme.id),
                "before": before,
                "after": after,
            },
            user_id=actor_id,
        )
    return sme


async def archive(
    db: AsyncSession,
    sme_id: UUID,
    *,
    organization_id: UUID,
    actor_id: UUID,
) -> None:
    sme = await _ensure_not_system_owned(db, sme_id, organization_id)
    sme.archived_at = datetime.now(UTC)
    sme.archived_by = actor_id
    await db.flush()
    await AuditWriter(db).log(
        organization_id=organization_id,
        entity_type="sme",
        entity_id=sme.id,
        action="sme.archived",
        changes={
            "sme_id": str(sme.id),
            "archived_by": str(actor_id),
        },
        user_id=actor_id,
    )


async def unarchive(
    db: AsyncSession,
    sme_id: UUID,
    *,
    organization_id: UUID,
    actor_id: UUID,
) -> tuple[SubjectMatterExpert, datetime | None, UUID | None]:
    """Sec-23 R3 read-before-mutate: returns (sme, prior_archived_at, prior_archived_by)
    for caller's audit-event construction."""
    sme = await _ensure_not_system_owned(
        db,
        sme_id,
        organization_id,
        allow_archived=True,
    )
    prior_at = sme.archived_at
    prior_by = sme.archived_by
    sme.archived_at = None
    sme.archived_by = None
    await db.flush()
    return sme, prior_at, prior_by


async def _get_or_create_system_sme(
    db: AsyncSession,
    organization_id: UUID,
    *,
    name: str,
    role_title: str,
    audit_action: str,
) -> tuple[SubjectMatterExpert, bool]:
    """Race-safe lazy create of a per-org system-owned SME identified by
    ``name`` (Arch-15 R2 / Arch-26 R3). Returns (sme, created).

    Spec-1/Sec-17 PR1: ON CONFLICT targets the partial unique index
    ``ux_sme_org_system_owned`` — WIDENED 2026-07-07 from ``(organization_id)``
    to ``(organization_id, name) WHERE is_system_owned`` so an org can hold both
    the "Industry baseline" (IRIS) and "Library reference" system SMEs. The
    predicate text must match the index DDL byte-for-byte: SQLite stores
    ``is_system_owned = 1``; PG stores ``is_system_owned = TRUE``. Passing a
    SQLAlchemy expression like ``is_(True)`` renders as ``IS 1`` / ``IS TRUE``
    which SQLite considers a different predicate and rejects — use raw ``text()``
    literals matching the model's DDL.

    Arch-27 PR3: use ``db.get_bind().dialect.name`` (works on async sessions
    bound to AsyncEngine OR sync engine); ``db.bind`` is None for sessions
    created via async_sessionmaker without explicit bind."""
    dialect = db.get_bind().dialect.name
    values_dict = {
        "organization_id": organization_id,
        "name": name,
        "email": None,
        "role_title": role_title,
        "is_system_owned": True,
        "created_via": "system",
        "created_by": None,
    }
    stmt: Any
    if dialect == "postgresql":
        stmt = (
            pg_insert(SubjectMatterExpert)
            .values(**values_dict)
            .on_conflict_do_nothing(
                index_elements=["organization_id", "name"],
                index_where=text("is_system_owned = TRUE"),
            )
        )
    else:
        stmt = (
            sqlite_insert(SubjectMatterExpert)
            .values(**values_dict)
            .on_conflict_do_nothing(
                index_elements=["organization_id", "name"],
                index_where=text("is_system_owned = 1"),
            )
        )
    res = await db.execute(stmt)
    await db.flush()
    sme = (
        await db.execute(
            select(SubjectMatterExpert).where(
                SubjectMatterExpert.organization_id == organization_id,
                SubjectMatterExpert.is_system_owned.is_(True),
                SubjectMatterExpert.name == name,
            )
        )
    ).scalar_one()
    # `Result.rowcount` IS populated for INSERT/UPDATE/DELETE on PG + SQLite
    # via SQLAlchemy 2.x's CursorResult — the mypy `attr-defined` from the
    # generic `Result[Any]` stub is a stub-precision miss, not a runtime bug.
    created = res.rowcount == 1  # type: ignore[attr-defined]
    # T10 Spec-18 R3: the *_materialized event is the ONLY audit event for the
    # system SME row's creation — sme.created is NOT emitted. Emit only on the
    # create branch so the idempotent second call stays audit-silent.
    if created:
        await AuditWriter(db).log(
            organization_id=organization_id,
            entity_type="sme",
            entity_id=sme.id,
            action=audit_action,
            changes={
                "sme_id": str(sme.id),
                "organization_id": str(organization_id),
            },
            user_id=None,  # system-owned: no actor
        )
    return sme, created


async def get_or_create_iris_sme(
    db: AsyncSession,
    organization_id: UUID,
) -> tuple[SubjectMatterExpert, bool]:
    """Race-safe lazy create of the per-org IRIS "Industry baseline" system SME.

    MD-7: name="Industry baseline" AND role_title="Industry baseline" — the
    §8.3 template renders `sme.name + " (one estimate)"` for is_system_owned=True
    rows, surfacing "Industry baseline (one estimate)" per MD-7's UI disclosure.
    """
    return await _get_or_create_system_sme(
        db,
        organization_id,
        name="Industry baseline",
        role_title="Industry baseline",
        audit_action="sme.iris_materialized",
    )


async def get_or_create_library_sme(
    db: AsyncSession,
    organization_id: UUID,
) -> tuple[SubjectMatterExpert, bool]:
    """Race-safe lazy create of the per-org "Library reference" system SME.

    #wizard-library-prefill: attributes the curated distributions seeded from a
    library entry (the reference-class archetype) so a library-derived scenario
    carries the entry's threat-specific values, NOT the threat-blind IRIS
    baseline. Distinct from the IRIS SME via the widened
    ``(organization_id, name)`` partial unique index.
    """
    return await _get_or_create_system_sme(
        db,
        organization_id,
        name="Library reference",
        role_title="Library reference",
        audit_action="sme.library_materialized",
    )


async def list_for_dropdown(
    db: AsyncSession,
    organization_id: UUID,
) -> list[dict[str, Any]]:
    """Spec-9 PR1 fix: return JSON-serializable dicts (not ORM rows) so
    Jinja's |tojson works correctly without detached-session errors."""
    stmt = select(SubjectMatterExpert).where(
        SubjectMatterExpert.organization_id == organization_id,
    )
    stmt = live_sme_query(stmt).order_by(SubjectMatterExpert.name)
    rows = list((await db.execute(stmt)).scalars())
    return [
        SubjectMatterExpertDropdownView(
            id=str(row.id),
            name=row.name,
            role_title=row.role_title,
            is_system_owned=row.is_system_owned,
        ).model_dump()
        for row in rows
    ]
