"""Data-access layer for ScenarioLibraryEntry + ScenarioLibraryOverride.

Spec §7.1.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    IndustrySubSector,
    IndustryType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario_library import (
    ScenarioLibraryEntry,
    ScenarioLibraryOverride,
)


def _json_array_overlaps(column: Any, values: list[str]) -> Any:
    """Return an EXISTS predicate: JSON array column overlaps with values list.

    SQLite has no native ARRAY @> operator; emulate via a JSON_EACH correlated
    subquery. The caller wraps the result in or_(is_json_null(column), inner)
    to handle the "NULL means applies to everything" rule.

    SQLite/JSON-null subtlety (paranoid-review Major-finding F7):
    SQLAlchemy's JSON type serialises Python None as the JSON literal 'null'
    (not as SQL NULL). So ``column.is_(None)`` is FALSE for these rows, and
    json_each('null') raises "malformed JSON". The fix:

      NULLIF(column, 'null')  — converts the JSON-null string to SQL NULL
      COALESCE(..., '[]')     — converts SQL NULL to an empty JSON array

    The caller's ``is_json_null(column)`` guard uses the same NULLIF trick so
    that rows with applicable_X = JSON null are returned unconditionally
    (semantics: "no restriction → applies to all").

    Postgres path: when the app migrates to Postgres, replace this function
    with a @> operator path without changing public method signatures.
    """
    safe_col = func.coalesce(func.nullif(column, "null"), "[]")
    json_each = func.json_each(safe_col).table_valued("value")
    inner = (
        select(literal_column("1"))
        .select_from(json_each)
        .where(json_each.c.value.in_(values))
        .exists()
    )
    return inner


def _is_json_null(column: Any) -> Any:
    """True when the JSON column holds a JSON null (Python None → stored as 'null').

    SQLAlchemy JSON type stores Python None as the JSON literal 'null', not as
    SQL NULL. NULLIF(column, 'null') converts that JSON-null string to SQL NULL
    so IS NULL comparisons work correctly.
    """
    return func.nullif(column, "null").is_(None)


class ScenarioLibraryRepo:
    """Data-access layer for scenario library canonical entries and org overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_published(
        self,
        threat_actor_types: list[ThreatActorType] | None = None,
        threat_event_types: list[ThreatCategory] | None = None,
        asset_classes: list[AssetClass] | None = None,
        applicable_industries: list[IndustryType] | None = None,
        applicable_sub_sectors: list[IndustrySubSector] | None = None,
        search_text: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScenarioLibraryEntry]:
        """Return latest published version per logical id.

        Window-function-free: uses MAX(version) per id WHERE status='published'
        subquery JOINed back — SQLite compatible.
        """
        latest_subq = (
            select(
                ScenarioLibraryEntry.id.label("id"),
                func.max(ScenarioLibraryEntry.version).label("max_version"),
            )
            .where(ScenarioLibraryEntry.status == "published")
            .group_by(ScenarioLibraryEntry.id)
            .subquery()
        )

        stmt = select(ScenarioLibraryEntry).join(
            latest_subq,
            and_(
                ScenarioLibraryEntry.id == latest_subq.c.id,
                ScenarioLibraryEntry.version == latest_subq.c.max_version,
            ),
        )

        if threat_actor_types:
            stmt = stmt.where(ScenarioLibraryEntry.threat_actor_type.in_(threat_actor_types))
        if threat_event_types:
            stmt = stmt.where(ScenarioLibraryEntry.threat_event_type.in_(threat_event_types))
        if asset_classes:
            stmt = stmt.where(ScenarioLibraryEntry.asset_class.in_(asset_classes))
        if source:
            stmt = stmt.where(ScenarioLibraryEntry.source == source)

        if applicable_industries:
            ind_values = [v.value for v in applicable_industries]
            stmt = stmt.where(
                or_(
                    _is_json_null(ScenarioLibraryEntry.applicable_industries),
                    _json_array_overlaps(ScenarioLibraryEntry.applicable_industries, ind_values),
                )
            )

        if applicable_sub_sectors:
            ss_values = [v.value for v in applicable_sub_sectors]
            stmt = stmt.where(
                or_(
                    _is_json_null(ScenarioLibraryEntry.applicable_sub_sectors),
                    _json_array_overlaps(ScenarioLibraryEntry.applicable_sub_sectors, ss_values),
                )
            )

        if search_text:
            # Escape SQL LIKE metacharacters so user-supplied % and _ don't
            # silently expand into wildcards. Use backslash as the escape char
            # via the LIKE ... ESCAPE '\\' SQL form.
            escaped = (
                search_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            like = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    func.lower(ScenarioLibraryEntry.name).like(like, escape="\\"),
                    func.lower(ScenarioLibraryEntry.description).like(like, escape="\\"),
                    func.lower(ScenarioLibraryEntry.slug).like(like, escape="\\"),
                )
            )

        stmt = stmt.order_by(ScenarioLibraryEntry.name).limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows)

    async def get_by_id_version(
        self,
        entry_id: uuid.UUID,
        version: int,
        *,
        for_update: bool = False,
    ) -> ScenarioLibraryEntry | None:
        """Pin-resolution lookup — exact (id, version) row, any status.

        Returns even deprecated rows for audit-grade pin discipline.

        When ``for_update=True``, the SELECT acquires a row-level FOR UPDATE
        lock so a concurrent librarian status-flip cannot change the row
        between the read and the stamp in ScenarioService._stamp_new_scenario.
        SQLite is single-writer so FOR UPDATE is a no-op there; on Postgres it
        serialises the read with any concurrent UPDATE on the same
        composite-PK row.
        """
        stmt = select(ScenarioLibraryEntry).where(
            and_(
                ScenarioLibraryEntry.id == entry_id,
                ScenarioLibraryEntry.version == version,
            )
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(
        self,
        slug: str,
        version: int | None = None,
    ) -> ScenarioLibraryEntry | None:
        """Look up a library entry by slug.

        - ``version=None``: returns the latest *published* row for this slug.
          Drafts and deprecated rows are excluded — this is the analyst-facing
          browse path.
        - ``version=N`` (admin/audit): returns the row at exactly that
          ``(slug, version)`` regardless of status. Mirrors
          :meth:`get_by_id_version`'s status-agnostic behaviour for pin lookups.
        """
        stmt = select(ScenarioLibraryEntry).where(ScenarioLibraryEntry.slug == slug)
        if version is not None:
            stmt = stmt.where(ScenarioLibraryEntry.version == version)
        else:
            stmt = stmt.where(ScenarioLibraryEntry.status == "published")
            stmt = stmt.order_by(ScenarioLibraryEntry.version.desc())
        return (await self.session.execute(stmt)).scalars().first()

    async def list_versions(
        self,
        entry_id: uuid.UUID,
    ) -> list[ScenarioLibraryEntry]:
        """Audit/admin view: all versions of a logical entry, ascending."""
        stmt = (
            select(ScenarioLibraryEntry)
            .where(ScenarioLibraryEntry.id == entry_id)
            .order_by(ScenarioLibraryEntry.version)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_override(
        self,
        organization_id: uuid.UUID,
        library_entry_id: uuid.UUID,
    ) -> ScenarioLibraryOverride | None:
        """Latest active override for (org, entry) pair.

        The UNIQUE constraint on (organization_id, library_entry_id) means at
        most one row can exist per pair. F9: filters tombstoned rows
        (deleted_at IS NULL) so soft-deleted overrides are invisible to
        normal resolution paths.
        """
        stmt = select(ScenarioLibraryOverride).where(
            and_(
                ScenarioLibraryOverride.organization_id == organization_id,
                ScenarioLibraryOverride.library_entry_id == library_entry_id,
                ScenarioLibraryOverride.deleted_at.is_(None),  # filter tombstoned
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest_published_by_id(
        self,
        entry_id: uuid.UUID,
    ) -> ScenarioLibraryEntry | None:
        """Return the highest-version published row for entry_id, or None.

        Used by GET /library/entries/{id} when no ?version= specified — surfaces
        the current publish-state row even if newer drafts exist.
        """
        stmt = (
            select(ScenarioLibraryEntry)
            .where(
                and_(
                    ScenarioLibraryEntry.id == entry_id,
                    ScenarioLibraryEntry.status == "published",
                )
            )
            .order_by(ScenarioLibraryEntry.version.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_published(
        self,
        threat_actor_types: list[ThreatActorType] | None = None,
        threat_event_types: list[ThreatCategory] | None = None,
        asset_classes: list[AssetClass] | None = None,
        applicable_industries: list[IndustryType] | None = None,
        applicable_sub_sectors: list[IndustrySubSector] | None = None,
        search_text: str | None = None,
        source: str | None = None,
    ) -> int:
        """Count published entries matching filters (or all if no filters given).

        Used by ScenarioLibraryService.list_browseable to populate
        BrowsePage.total for pagination UI ("page 2 of 7"). Mirrors
        list_published's filter clauses with select(func.count()) instead
        of returning rows.

        NOTE: keep filter clauses in sync with list_published — parallel
        maintenance. If list_published gains a new filter, add it here too.
        """
        latest_subq = (
            select(
                ScenarioLibraryEntry.id.label("id"),
                func.max(ScenarioLibraryEntry.version).label("max_version"),
            )
            .where(ScenarioLibraryEntry.status == "published")
            .group_by(ScenarioLibraryEntry.id)
            .subquery()
        )

        stmt = (
            select(func.count())
            .select_from(ScenarioLibraryEntry)
            .join(
                latest_subq,
                and_(
                    ScenarioLibraryEntry.id == latest_subq.c.id,
                    ScenarioLibraryEntry.version == latest_subq.c.max_version,
                ),
            )
        )

        if threat_actor_types:
            stmt = stmt.where(ScenarioLibraryEntry.threat_actor_type.in_(threat_actor_types))
        if threat_event_types:
            stmt = stmt.where(ScenarioLibraryEntry.threat_event_type.in_(threat_event_types))
        if asset_classes:
            stmt = stmt.where(ScenarioLibraryEntry.asset_class.in_(asset_classes))
        if source:
            stmt = stmt.where(ScenarioLibraryEntry.source == source)

        if applicable_industries:
            ind_values = [v.value for v in applicable_industries]
            stmt = stmt.where(
                or_(
                    _is_json_null(ScenarioLibraryEntry.applicable_industries),
                    _json_array_overlaps(ScenarioLibraryEntry.applicable_industries, ind_values),
                )
            )

        if applicable_sub_sectors:
            ss_values = [v.value for v in applicable_sub_sectors]
            stmt = stmt.where(
                or_(
                    _is_json_null(ScenarioLibraryEntry.applicable_sub_sectors),
                    _json_array_overlaps(ScenarioLibraryEntry.applicable_sub_sectors, ss_values),
                )
            )

        if search_text:
            escaped = (
                search_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            like = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    func.lower(ScenarioLibraryEntry.name).like(like, escape="\\"),
                    func.lower(ScenarioLibraryEntry.description).like(like, escape="\\"),
                    func.lower(ScenarioLibraryEntry.slug).like(like, escape="\\"),
                )
            )

        return (await self.session.execute(stmt)).scalar_one()

    async def get_override_by_version(
        self,
        override_id: uuid.UUID,
        version: int,
    ) -> ScenarioLibraryOverride | None:
        """Pin-resolution: exact (override_id, version) lookup.

        Override IS the row (not a row-set); the ``version`` field is the
        override revision number on the row itself.
        """
        stmt = select(ScenarioLibraryOverride).where(
            and_(
                ScenarioLibraryOverride.id == override_id,
                ScenarioLibraryOverride.version == version,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
