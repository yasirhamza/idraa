"""Overlay seeding + CRUD service.

Two distinct responsibilities live here:

1. ``seed_starter_overlays_for_org`` (extracted under C3) — shared
   between the Alembic seed migration AND test fixtures so both code
   paths produce byte-identical rows. Tests build schema via
   ``Base.metadata.create_all`` and never run Alembic data migrations,
   so without this extraction every test depending on seeded overlays
   would see zero rows. Reference: plan §C3, B6 (single seed source) +
   B14 (fail loud on missing methodology).

2. ``OverlayService`` (C5) — the CRUD layer admins exercise from the
   overlay edit screens. Every mutation writes an audit row in the
   caller's session (atomic via ``AuditWriter``); the route layer
   commits. Updates are protected by an optimistic-lock on
   ``expected_version`` (preamble B8): two admins editing the same row
   concurrently produce an ``OverlayVersionConflictError`` rather than
   a silent overwrite. ``tag`` rename is intentionally rejected — the
   tag is a stable identifier that pinned scenarios reference; renaming
   it would orphan revisions for existing pins. Use deactivate +
   create-new to retire a tag.

Audit ``action`` strings follow the project-wide ``<entity>.<verb>``
taxonomy: ``overlay.create`` / ``overlay.update`` / ``overlay.deactivate``.
This deviates from the bare-verb pattern in ``services/controls.py``;
``controls.py`` predates the taxonomy fold-in and will be reconciled
when that service next changes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import ConflictError
from idraa.models.overlay import OverlayDefinition, OverlayDefinitionRevision
from idraa.schemas.overlay import OverlayForm
from idraa.services.audit import AuditWriter


class OverlayVersionConflictError(ConflictError):
    """Raised when ``OverlayService.update`` is called with a stale
    ``expected_version`` (B8 optimistic lock).

    Message names both the caller's expected version and the row's
    actual version so the route layer can render a "reload and retry"
    409 page that tells the admin which revision they were editing
    against.

    Subclasses :class:`idraa.errors.ConflictError` so the route layer
    can map to HTTP 409 by catching the base class.
    """


async def seed_starter_overlays_for_org(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
) -> int:
    """Seed STARTER_OVERLAYS for one organization. Returns the count seeded.

    Idempotent: skips (org, tag) rows that already exist. Raises RuntimeError
    if STARTER_OVERLAY_PROVENANCE is missing methodology for any starter tag —
    silent-skip would leave dangling forward-references with no signal.

    Used by both the Alembic seed migration AND test fixtures (test conftest
    builds schema via Base.metadata.create_all and never invokes Alembic data
    migrations, so this callable is the only path to seeded data under tests).
    """
    from idraa.services._starter_overlays_seed_data import (
        STARTER_OVERLAY_PROVENANCE,
        STARTER_OVERLAYS,
    )

    existing_tags_result = await db.execute(
        select(OverlayDefinition.tag).where(
            OverlayDefinition.organization_id == organization_id,
        )
    )
    existing_tags = {tag for (tag,) in existing_tags_result.all()}

    seeded = 0
    for overlay in STARTER_OVERLAYS:
        if overlay.tag in existing_tags:
            continue
        provenance = STARTER_OVERLAY_PROVENANCE.get(overlay.tag)
        if provenance is None or not str(provenance.get("methodology") or "").strip():
            raise RuntimeError(
                f"STARTER_OVERLAY_PROVENANCE missing methodology for tag "
                f"{overlay.tag!r}; refusing to seed dangling forward-references"
            )
        # provenance value type is object — narrow the iterable explicitly so
        # mypy sees a list constructor that accepts the seed-data tuple shape.
        sources_obj = provenance.get("sources", ())
        sources = list(sources_obj) if isinstance(sources_obj, tuple | list) else []
        methodology = str(provenance["methodology"])

        od = OverlayDefinition(
            organization_id=organization_id,
            tag=overlay.tag,
            display_name=overlay.display_name,
            frequency_multiplier=overlay.frequency_multiplier,
            magnitude_multiplier=overlay.magnitude_multiplier,
            sources=sources,
            methodology=methodology,
            version=1,
            is_active=True,
        )
        db.add(od)
        await db.flush()

        odr = OverlayDefinitionRevision(
            overlay_definition_id=od.id,
            version=1,
            tag=od.tag,
            display_name=od.display_name,
            frequency_multiplier=od.frequency_multiplier,
            magnitude_multiplier=od.magnitude_multiplier,
            sources=list(od.sources),
            methodology=od.methodology,
            methodology_change_reason="initial seed from STARTER_OVERLAYS",
            created_by_user_id=None,
        )
        db.add(odr)
        seeded += 1

    if seeded:
        await db.flush()
    return seeded


# Fields whose change is detected and snapshotted on overlay update.
# ``tag`` is intentionally absent — rename is rejected up front by
# ``OverlayService.update`` (see ``_TAG_RENAME_MSG``).
_DIFFABLE_FIELDS: tuple[str, ...] = (
    "display_name",
    "frequency_multiplier",
    "magnitude_multiplier",
    "sources",
    "methodology",
)

_TAG_RENAME_MSG = (
    "tag rename not allowed: tag is a stable identifier referenced by "
    "pinned scenarios; use deactivate + create-new instead"
)


class OverlayService:
    """CRUD + audit + version-bump for ``OverlayDefinition``.

    Mutations land in the caller's session without committing — the
    route layer (C7) wraps the call in ``async with db.begin()`` and
    commits atomically with any sibling writes. Audit rows are flushed
    in the same session via ``AuditWriter``, so a caller rollback
    discards both halves together.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        form: OverlayForm,
        ip_address: str | None = None,
    ) -> OverlayDefinition:
        """Create a new overlay + its initial revision (version=1).

        Writes an ``overlay.create`` audit row in the same session.
        ``methodology_change_reason`` from the form is recorded on the
        revision (typically "initial creation" or similar).
        """
        od = OverlayDefinition(
            organization_id=organization_id,
            tag=form.tag,
            display_name=form.display_name,
            frequency_multiplier=form.frequency_multiplier,
            magnitude_multiplier=form.magnitude_multiplier,
            sources=list(form.sources),
            methodology=form.methodology,
            version=1,
            is_active=True,
        )
        self._db.add(od)
        await self._db.flush()

        rev = OverlayDefinitionRevision(
            overlay_definition_id=od.id,
            version=1,
            tag=od.tag,
            display_name=od.display_name,
            frequency_multiplier=od.frequency_multiplier,
            magnitude_multiplier=od.magnitude_multiplier,
            sources=list(od.sources),
            methodology=od.methodology,
            methodology_change_reason=form.methodology_change_reason,
            created_by_user_id=user_id,
        )
        self._db.add(rev)
        await self._db.flush()

        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="overlay",
            entity_id=od.id,
            action="overlay.create",
            changes={"tag": [None, od.tag], "version": [None, 1]},
            user_id=user_id,
            ip_address=ip_address,
        )
        return od

    async def update(
        self,
        *,
        overlay: OverlayDefinition,
        user_id: uuid.UUID | None,
        form: OverlayForm,
        expected_version: int,
        ip_address: str | None = None,
    ) -> OverlayDefinition:
        """Apply ``form`` to ``overlay``, bumping version + writing a new revision.

        - Raises ``OverlayVersionConflictError`` if ``overlay.version``
          doesn't match ``expected_version`` (B8 optimistic lock).
        - Raises ``ValueError`` if ``form.tag`` differs from
          ``overlay.tag`` — tag rename is forbidden because pinned
          revisions reference the tag as a stable identifier.
        - If no diffable field changed, returns ``overlay`` unchanged
          — no audit row, no revision row, no version bump. The
          ``methodology_change_reason`` is therefore only persisted
          when there's an actual change to explain.
        """
        if overlay.version != expected_version:
            raise OverlayVersionConflictError(
                f"overlay version conflict: expected_version={expected_version} "
                f"but actual version={overlay.version}; another admin updated "
                f"this overlay — reload and retry"
            )
        if form.tag != overlay.tag:
            raise ValueError(_TAG_RENAME_MSG)

        changes: dict[str, list[object]] = {}
        for field in _DIFFABLE_FIELDS:
            prev = getattr(overlay, field)
            new = getattr(form, field)
            # Normalise list comparisons: form sources is a list, ORM
            # sources is also a list, but a future column-type change
            # mustn't silently flip equality semantics.
            prev_cmp = list(prev) if isinstance(prev, list) else prev
            new_cmp = list(new) if isinstance(new, list) else new
            if prev_cmp != new_cmp:
                changes[field] = [prev_cmp, new_cmp]

        if not changes:
            return overlay

        prev_version = overlay.version
        new_version = prev_version + 1

        for field, (_, new_val) in changes.items():
            # Stored value should be a fresh list (not a reference into
            # the diff dict's value list) to avoid aliasing surprises if
            # the caller later mutates ``form.sources``.
            if isinstance(new_val, list):
                setattr(overlay, field, list(new_val))
            else:
                setattr(overlay, field, new_val)
        overlay.version = new_version

        rev = OverlayDefinitionRevision(
            overlay_definition_id=overlay.id,
            version=new_version,
            tag=overlay.tag,
            display_name=overlay.display_name,
            frequency_multiplier=overlay.frequency_multiplier,
            magnitude_multiplier=overlay.magnitude_multiplier,
            sources=list(overlay.sources),
            methodology=overlay.methodology,
            methodology_change_reason=form.methodology_change_reason,
            created_by_user_id=user_id,
        )
        self._db.add(rev)
        await self._db.flush()

        audit_changes: dict[str, object] = dict(changes)
        audit_changes["version"] = [prev_version, new_version]

        await AuditWriter(self._db).log(
            organization_id=overlay.organization_id,
            entity_type="overlay",
            entity_id=overlay.id,
            action="overlay.update",
            changes=audit_changes,
            user_id=user_id,
            ip_address=ip_address,
        )
        return overlay

    async def deactivate(
        self,
        *,
        overlay: OverlayDefinition,
        user_id: uuid.UUID | None,
        reason: str,
        ip_address: str | None = None,
    ) -> OverlayDefinition:
        """Mark overlay inactive. Idempotent on already-inactive rows.

        Idempotency rule: if ``overlay.is_active`` is already False,
        return immediately without writing a second audit row. This
        keeps the operation safe to retry (e.g. an HTMX double-submit)
        without producing duplicate audit noise.
        """
        if not overlay.is_active:
            return overlay

        overlay.is_active = False

        await AuditWriter(self._db).log(
            organization_id=overlay.organization_id,
            entity_type="overlay",
            entity_id=overlay.id,
            action="overlay.deactivate",
            changes={"is_active": [True, False], "reason": [None, reason]},
            user_id=user_id,
            ip_address=ip_address,
        )
        return overlay
