"""Service-layer API over ScenarioLibraryRepo.

Spec §7.2 + §7.4.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.errors import (
    IDORError,
    LibraryEntryDeleteRefusedError,
    LibraryEntryNotFoundError,
    LibraryEntryStatusError,
    LibraryOverrideAlreadyExistsError,
    LibraryOverrideVersionConflictError,
    ValidationError,
)
from idraa.models.enums import (
    AssetClass,
    IndustrySubSector,
    IndustryType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import (
    ScenarioLibraryEntry,
    ScenarioLibraryOverride,
)
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo
from idraa.services.audit import AuditWriter
from idraa.services.fair_cam_validation import validate_fair_distributions

if TYPE_CHECKING:
    from idraa.models.user import User


# ---------------------------------------------------------------------------
# Facet labels — centralised so sidebar and tests share one definition.
# ---------------------------------------------------------------------------

_THREAT_ACTOR_LABELS: dict[str, str] = {
    ThreatActorType.CYBERCRIMINALS: "Cybercriminals",
    ThreatActorType.NATION_STATE: "Nation-state",
    ThreatActorType.INSIDER_MALICIOUS: "Insider — malicious",
    ThreatActorType.INSIDER_ACCIDENTAL: "Insider — accidental",
    ThreatActorType.HACKTIVISTS: "Hacktivists",
    ThreatActorType.COMPETITORS: "Competitors",
}

_THREAT_CATEGORY_LABELS: dict[str, str] = {
    ThreatCategory.RANSOMWARE: "Ransomware",
    ThreatCategory.MALWARE: "Malware",
    ThreatCategory.DATA_DISCLOSURE: "Data disclosure",
    ThreatCategory.DATA_TAMPERING: "Data tampering",
    ThreatCategory.DENIAL_OF_SERVICE: "Denial of service",
    ThreatCategory.SOCIAL_ENGINEERING: "Social engineering",
    ThreatCategory.PHYSICAL_TAMPERING: "Physical tampering",
    ThreatCategory.SUPPLY_CHAIN: "Supply chain",
    ThreatCategory.INSIDER_MISUSE: "Insider misuse",
    ThreatCategory.OT_SAFETY_TAMPERING: "OT safety tampering",
    ThreatCategory.OT_AVAILABILITY: "OT availability",
    ThreatCategory.OT_INTEGRITY: "OT integrity (manipulation of view)",
    ThreatCategory.MISCELLANEOUS: "Miscellaneous",
}

_SUB_SECTOR_LABELS: dict[str, str] = {
    IndustrySubSector.OIL_AND_GAS: "Oil & gas",
    IndustrySubSector.ELECTRIC_UTILITY: "Electric utility",
    IndustrySubSector.CHEMICAL_MANUFACTURING: "Chemical manufacturing",
    IndustrySubSector.WATER_UTILITY: "Water utility",
    IndustrySubSector.NUCLEAR: "Nuclear",
    IndustrySubSector.PIPELINE: "Pipeline",
    IndustrySubSector.PROCESS_MANUFACTURING: "Process manufacturing",
    IndustrySubSector.OTHER: "Other",
}

_INDUSTRY_LABELS: dict[str, str] = {
    IndustryType.AGRICULTURE: "Agriculture",
    IndustryType.MINING: "Mining",
    IndustryType.UTILITIES: "Utilities",
    IndustryType.CONSTRUCTION: "Construction",
    IndustryType.MANUFACTURING: "Manufacturing",
    IndustryType.TRADE: "Trade",
    IndustryType.RETAIL: "Retail",
    IndustryType.TRANSPORTATION: "Transportation",
    IndustryType.INFORMATION: "Information",
    IndustryType.FINANCIAL: "Financial",
    IndustryType.REAL_ESTATE: "Real estate",
    IndustryType.PROFESSIONAL: "Professional",
    IndustryType.MANAGEMENT: "Management",
    IndustryType.ADMINISTRATIVE: "Administrative",
    IndustryType.EDUCATION: "Education",
    IndustryType.HEALTHCARE: "Healthcare",
    IndustryType.ENTERTAINMENT: "Entertainment",
    IndustryType.HOSPITALITY: "Hospitality",
    IndustryType.PUBLIC: "Public",
    IndustryType.OTHER: "Other",
}


@dataclass(frozen=True)
class FacetOption:
    """A single facet option with its value, human-readable label, and entry count."""

    value: str
    label: str
    count: int


def _sort_facets(opts: list[FacetOption]) -> list[FacetOption]:
    """Sort facet options: count descending, then value ascending as tiebreaker."""
    return sorted(opts, key=lambda o: (-o.count, o.value))


async def available_facets(
    db: AsyncSession,
) -> dict[str, list[FacetOption]]:
    """Compute browse-sidebar facets from PUBLISHED library entries.

    Returns a dict keyed by dimension name, each value a list of
    FacetOption(value, label, count) containing ONLY values present in ≥1
    published entry, sorted by count desc then value asc.

    Dimensions returned:
      - ``asset_class``:      scalar GROUP BY on the column.
      - ``threat_actor_type``: scalar GROUP BY on the column.
      - ``threat_category``:  scalar GROUP BY on threat_event_type column.
      - ``sub_sector``:       JSON-array explode of applicable_sub_sectors;
                              NULL/empty entries are skipped (applies-to-all).
      - ``industry``:         JSON-array explode of applicable_industries;
                              NULL/empty entries are skipped.

    NULL/empty JSON-list entries are excluded from sub_sector and industry
    facets intentionally: a NULL list means "applies to all" and is not a
    specific value to filter on. Those entries still appear in search results
    when a matching filter is selected, but they don't define facet values.
    """
    # ------------------------------------------------------------------
    # 1. Latest-published subquery (mirrors list_published's approach).
    # ------------------------------------------------------------------

    latest_subq = (
        select(
            ScenarioLibraryEntry.id.label("id"),
            func.max(ScenarioLibraryEntry.version).label("max_version"),
        )
        .where(ScenarioLibraryEntry.status == "published")
        .group_by(ScenarioLibraryEntry.id)
        .subquery()
    )

    from sqlalchemy import and_

    published_q = (
        select(ScenarioLibraryEntry)
        .join(
            latest_subq,
            and_(
                ScenarioLibraryEntry.id == latest_subq.c.id,
                ScenarioLibraryEntry.version == latest_subq.c.max_version,
            ),
        )
        .subquery()
    )

    # ------------------------------------------------------------------
    # 2. Scalar GROUP BYs (asset_class, threat_actor_type, threat_category).
    # ------------------------------------------------------------------
    # Import ASSET_CLASS_LABELS lazily to avoid circular imports.
    from idraa.routes.scenario_form_helpers import ASSET_CLASS_LABELS

    ac_labels: dict[str, str] = {k.value: v for k, v in ASSET_CLASS_LABELS.items()}
    scalar_dims: list[tuple[str, Any, dict[str, str]]] = [
        ("asset_class", published_q.c.asset_class, ac_labels),
        ("threat_actor_type", published_q.c.threat_actor_type, _THREAT_ACTOR_LABELS),
        ("threat_category", published_q.c.threat_event_type, _THREAT_CATEGORY_LABELS),
    ]

    result: dict[str, list[FacetOption]] = {}

    def _build_opts(rows: Sequence[Any], labels: dict[str, str]) -> list[FacetOption]:
        """Build FacetOption list from GROUP BY (value, count) rows.

        Uses tuple-unpacking (val, cnt) to avoid [0]/[1] subscripts — those
        trigger the adapter-iter lint rule which guards against accidentally
        dropping list elements.  This is not an adapter list; these are
        SQLAlchemy 2-column result rows.
        """
        opts: list[FacetOption] = []
        for val, cnt in rows:
            if val is None:
                continue
            key = str(val)
            opts.append(FacetOption(value=key, label=labels.get(key, key), count=cnt))
        return opts

    for dim_name, col, labels in scalar_dims:
        rows = (
            await db.execute(
                select(col, func.count().label("cnt")).select_from(published_q).group_by(col)
            )
        ).all()
        result[dim_name] = _sort_facets(_build_opts(rows, labels))

    # ------------------------------------------------------------------
    # 3. JSON-array dimensions (sub_sector, industry).
    # ------------------------------------------------------------------
    # SQLite: use json_each to explode the array, then group-by the
    # exploded value.  Rows whose column is SQL NULL or the JSON literal
    # 'null' (Python None serialised by SQLAlchemy) are skipped —
    # they mean "applies to all" and don't define a specific facet value.
    # ------------------------------------------------------------------
    json_dims: list[tuple[str, Any, dict[str, str]]] = [
        ("sub_sector", published_q.c.applicable_sub_sectors, _SUB_SECTOR_LABELS),
        ("industry", published_q.c.applicable_industries, _INDUSTRY_LABELS),
    ]

    for dim_name, col, labels in json_dims:
        safe_col = func.coalesce(func.nullif(col, "null"), "[]")
        json_each = func.json_each(safe_col).table_valued("value")

        rows = (
            await db.execute(
                select(
                    json_each.c.value.label("val"),
                    func.count().label("cnt"),
                )
                .select_from(published_q)
                .join(json_each, literal_column("1") == literal_column("1"))
                .where(json_each.c.value.isnot(None))
                .group_by(json_each.c.value)
            )
        ).all()
        result[dim_name] = _sort_facets(_build_opts(rows, labels))

    return result


@dataclass(frozen=True)
class MergedDistributions:
    """Field-wise merge result; passed to scenario create primitive."""

    threat_event_frequency: dict[str, Any]
    vulnerability: dict[str, Any]
    primary_loss: dict[str, Any]
    secondary_loss: dict[str, Any] | None


@dataclass(frozen=True)
class ResolvedLibraryEntry:
    """Fully resolved (entry, override, merged, pin) tuple ready to seed a Scenario."""

    entry: ScenarioLibraryEntry
    override: ScenarioLibraryOverride | None
    merged: MergedDistributions
    pin: dict[str, Any]


@dataclass
class BrowseFilters:
    """User-applied browse filter selections; defaults all None (= no narrowing)."""

    threat_actor_types: list[ThreatActorType] = field(default_factory=list)
    threat_event_types: list[ThreatCategory] = field(default_factory=list)
    asset_classes: list[AssetClass] = field(default_factory=list)
    applicable_industries: list[IndustryType] = field(default_factory=list)
    applicable_sub_sectors: list[IndustrySubSector] = field(default_factory=list)
    search_text: str | None = None
    # P3 Task 6: provenance filter — 'seed' | 'imported' | None (= both).
    source: str | None = None


@dataclass
class BrowsePage:
    entries: list[ScenarioLibraryEntry]
    total: int  # F14: global count via count_published (accurate for pagination UI).
    page: int


@dataclass(frozen=True)
class DeletedImportedEntry:
    """Result of ``delete_imported_entry`` — fed to the route's warning flash.

    ``pinned_scenario_count`` is the GLOBAL number of scenarios whose
    ``library_pin.entry_id`` referenced this entry. Option B does NOT block
    on it — it only surfaces a warning ("N scenarios were cloned from this
    entry; they keep working unchanged"). Safe because clone-time COPIES the
    FAIR distributions onto the scenario row (run_executor reads them off the
    scenario), so deleting the source affects only re-cloning + the source-
    detail panel.
    """

    slug: str
    versions_deleted: int
    pinned_scenario_count: int


@dataclass(frozen=True)
class OverrideDraft:
    """Mutable fields an analyst can supply when creating or updating an override.

    All distribution fields are optional — the caller supplies only the
    fields they want to override; ``None`` means "use canonical entry value".
    """

    threat_event_frequency: dict[str, Any] | None
    vulnerability: dict[str, Any] | None
    primary_loss: dict[str, Any] | None
    secondary_loss: dict[str, Any] | None


def merge_canonical_and_override(
    entry: ScenarioLibraryEntry,
    override: ScenarioLibraryOverride | None,
) -> MergedDistributions:
    """Field-wise merge per spec §7.4.

    Paranoid-review: explicit ``is not None`` check, NOT Python truthiness.
    An empty dict ``{}`` is falsy and would silently fall through to entry —
    wrong.
    """
    if override is None:
        return MergedDistributions(
            threat_event_frequency=entry.threat_event_frequency,
            vulnerability=entry.vulnerability,
            primary_loss=entry.primary_loss,
            secondary_loss=entry.secondary_loss,
        )
    return MergedDistributions(
        threat_event_frequency=(
            entry.threat_event_frequency
            if override.threat_event_frequency is None
            else override.threat_event_frequency
        ),
        vulnerability=(
            entry.vulnerability if override.vulnerability is None else override.vulnerability
        ),
        primary_loss=(
            entry.primary_loss if override.primary_loss is None else override.primary_loss
        ),
        secondary_loss=(
            entry.secondary_loss if override.secondary_loss is None else override.secondary_loss
        ),
    )


def _validate_effective_distributions(
    entry: ScenarioLibraryEntry,
    draft: OverrideDraft,
) -> None:
    """#333: validate the EFFECTIVE distributions an override write produces.

    A draft leg of ``None`` means "use canonical entry value" (the
    ``merge_canonical_and_override`` semantics applied at consumption time),
    so validation merges the draft over the canonical entry and validates the
    result — exactly the values that can reach the Monte Carlo engine.

    Raises FAIRCAMValidationError (a ValidationError subclass; routes map it
    to HTTP 422) on non-finite legs, lognormal sigma outside (0, 10]
    (Sec-I2 OOM/DoS bound), vulnerability legs outside [0, 1], or any
    fair_cam ERROR-severity finding. Same gate as scenario create/update/
    import and library bundle import.
    """
    validate_fair_distributions(
        threat_event_frequency=(
            draft.threat_event_frequency
            if draft.threat_event_frequency is not None
            else entry.threat_event_frequency
        ),
        vulnerability=(
            draft.vulnerability if draft.vulnerability is not None else entry.vulnerability
        ),
        primary_loss=(draft.primary_loss if draft.primary_loss is not None else entry.primary_loss),
        secondary_loss=(
            draft.secondary_loss if draft.secondary_loss is not None else entry.secondary_loss
        ),
    )


class ScenarioLibraryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ScenarioLibraryRepo(session)

    async def resolve_for_clone(
        self,
        entry_id: uuid.UUID,
        organization_id: uuid.UUID,
        version: int | None = None,
    ) -> ResolvedLibraryEntry:
        """Returns merged (entry, override, merged, pin) view ready to seed a Scenario.

        ``version=None`` (default): resolve latest published version.
        ``version=N``: pin to that explicit (id, version) pair — used by F22
        reproducibility test to re-clone v1 after v2 is published.

        Raises LibraryEntryNotFoundError if entry doesn't exist;
        LibraryEntryStatusError if entry is draft/deprecated.
        """
        if version is not None:
            entry = await self.repo.get_by_id_version(entry_id, version)
        else:
            entry = await self._get_entry_by_id(entry_id)

        if entry is None:
            raise LibraryEntryNotFoundError(f"library entry {entry_id} not found or not published")
        if entry.status != "published":
            raise LibraryEntryStatusError(
                f"cannot clone from entry with status={entry.status!r} (must be published)"
            )

        override = await self.repo.get_override(organization_id, entry_id)
        merged = merge_canonical_and_override(entry, override)
        pin: dict[str, Any] = {
            "entry_id": str(entry.id),
            "version": entry.version,
            "override_id": str(override.id) if override is not None else None,
            "override_version": override.version if override is not None else None,
        }
        return ResolvedLibraryEntry(entry=entry, override=override, merged=merged, pin=pin)

    async def list_browseable(
        self,
        filters: BrowseFilters,
        page: int = 1,
        page_size: int | None = None,
    ) -> BrowsePage:
        """Return published library entries matching the explicit filters.

        All filter fields are applied as provided; when a filter is empty /
        unset the corresponding dimension is unconstrained (i.e. browse
        defaults to all entries when filters are empty).  No org-based
        defaulting or auto-narrowing is performed.

        ``page_size`` defaults to ``settings.list_page_size`` (the paginated
        ``/library`` browse). Callers that render the whole curated corpus on
        one page with no pager (the wizard picker) pass an explicit large
        value — see ``routes.scenarios._WIZARD_LIBRARY_PAGE_SIZE``.
        """
        sub_sectors = list(filters.applicable_sub_sectors)

        if page_size is None:
            page_size = get_settings().list_page_size
        offset = (page - 1) * page_size

        rows = await self.repo.list_published(
            threat_actor_types=filters.threat_actor_types or None,
            threat_event_types=filters.threat_event_types or None,
            asset_classes=filters.asset_classes or None,
            applicable_industries=filters.applicable_industries or None,
            applicable_sub_sectors=sub_sectors or None,
            search_text=filters.search_text,
            source=filters.source,
            limit=page_size,
            offset=offset,
        )
        # F14: promote BrowsePage.total from page-window count to real total
        # via a separate count_published query. Mirrors list_published filter
        # clauses so "page 2 of 7" pagination math is accurate.
        total = await self.repo.count_published(
            threat_actor_types=filters.threat_actor_types or None,
            threat_event_types=filters.threat_event_types or None,
            asset_classes=filters.asset_classes or None,
            applicable_industries=filters.applicable_industries or None,
            applicable_sub_sectors=sub_sectors or None,
            search_text=filters.search_text,
            source=filters.source,
        )
        return BrowsePage(entries=rows, total=total, page=page)

    async def _get_entry_by_id(
        self,
        entry_id: uuid.UUID,
    ) -> ScenarioLibraryEntry | None:
        """Returns the most-recent version row for the logical id, any status.

        Fetches any status (not just published) so the caller can distinguish
        "not found at all" (None → LibraryEntryNotFoundError) from "found but
        wrong status" (entry.status != 'published' → LibraryEntryStatusError).
        """
        stmt = (
            select(ScenarioLibraryEntry)
            .where(ScenarioLibraryEntry.id == entry_id)
            .order_by(ScenarioLibraryEntry.version.desc())
        )
        return (await self.session.execute(stmt)).scalars().first()

    # ------------------------------------------------------------------
    # F9 — override CRUD
    # ------------------------------------------------------------------

    async def create_override(
        self,
        entry_id: uuid.UUID,
        organization_id: uuid.UUID,
        draft: OverrideDraft,
        reason: str,
        user: User,
        methodology_change_reason: str | None = None,
        ip_address: str | None = None,
    ) -> ScenarioLibraryOverride:
        """Create a new per-org override for a canonical library entry.

        Raises:
            LibraryOverrideAlreadyExistsError: if an active (non-tombstoned)
                override already exists for this (org, entry) pair.
            LibraryEntryNotFoundError: if the library entry does not exist.
        """
        existing = await self.repo.get_override(organization_id, entry_id)
        if existing is not None:
            raise LibraryOverrideAlreadyExistsError(
                f"override already exists for entry {entry_id} in this org; "
                "use update_override to modify it"
            )

        entry = await self._get_entry_by_id(entry_id)
        if entry is None:
            raise LibraryEntryNotFoundError(f"library entry {entry_id} not found")

        # #333: gate override writes through validate_fair_distributions —
        # every other distribution write path (scenario create/update/import,
        # bundle import) already does. Raises FAIRCAMValidationError → 422.
        _validate_effective_distributions(entry, draft)

        override = ScenarioLibraryOverride(
            organization_id=organization_id,
            library_entry_id=entry.id,
            library_entry_version=entry.version,
            threat_event_frequency=draft.threat_event_frequency,
            vulnerability=draft.vulnerability,
            primary_loss=draft.primary_loss,
            secondary_loss=draft.secondary_loss,
            reason=reason,
            methodology_change_reason=methodology_change_reason,
            version=1,
            row_version=1,
            created_by=user.id,
        )
        self.session.add(override)
        await self.session.flush()

        await self._write_audit(
            user=user,
            organization_id=organization_id,
            entity_type="scenario_library_override",
            entity_id=override.id,
            action="library_override.create",
            changes={
                "entry_id": [None, str(entry.id)],
                "entry_version": [None, entry.version],
            },
            ip_address=ip_address,
        )
        return override

    async def update_override(
        self,
        override_id: uuid.UUID,
        organization_id: uuid.UUID,
        draft: OverrideDraft,
        reason: str,
        methodology_change_reason: str | None,
        user: User,
        expected_version: int,
        ip_address: str | None = None,
    ) -> ScenarioLibraryOverride:
        """Apply ``draft`` to an existing override, bumping its version.

        Uses a SELECT ... FOR UPDATE lock to prevent concurrent edits from
        silently overwriting each other (race-condition fix from paranoid
        review).

        Raises:
            LibraryEntryNotFoundError: if the override row doesn't exist.
            IDORError: if the override belongs to a different organization.
            LibraryOverrideVersionConflictError: if ``expected_version``
                doesn't match the row's current version (optimistic lock).
            ValidationError: if a distribution-shape change is detected but
                ``methodology_change_reason`` is absent or blank.
        """
        stmt = (
            select(ScenarioLibraryOverride)
            .where(ScenarioLibraryOverride.id == override_id)
            .with_for_update()
        )
        override = (await self.session.execute(stmt)).scalar_one_or_none()
        if override is None:
            raise LibraryEntryNotFoundError(f"library override {override_id} not found")

        if override.organization_id != organization_id:
            raise IDORError(f"override {override_id} does not belong to this organization")

        if override.version != expected_version:
            raise LibraryOverrideVersionConflictError(
                f"library override version conflict: "
                f"expected_version={expected_version} but actual "
                f"version={override.version}; another user updated "
                "this override — reload and retry"
            )

        # Shape-signature discipline: detect distribution-kind flip (PERT → Normal,
        # leg added/removed). Shape change requires a non-empty methodology_change_reason.
        if self._shape_changed(override, draft) and (
            not methodology_change_reason or not methodology_change_reason.strip()
        ):
            raise ValidationError(
                "methodology_change_reason is required when the distribution "
                "shape changes (e.g. PERT → Normal, or a leg is added/removed)"
            )

        # #333: same gate as create_override. The canonical entry fills any
        # draft leg left as None (the merge semantics resolve_for_clone applies
        # at consumption), so the values that can reach the engine are exactly
        # what get validated. Runs BEFORE any mutation so a rejected draft
        # leaves the row untouched.
        entry = await self._get_entry_by_id(override.library_entry_id)
        if entry is None:  # FK guarantees existence; defensive symmetry
            raise LibraryEntryNotFoundError(f"library entry {override.library_entry_id} not found")
        _validate_effective_distributions(entry, draft)

        prev_version = override.version
        override.threat_event_frequency = draft.threat_event_frequency
        override.vulnerability = draft.vulnerability
        override.primary_loss = draft.primary_loss
        override.secondary_loss = draft.secondary_loss
        override.reason = reason
        override.methodology_change_reason = methodology_change_reason
        override.version = prev_version + 1
        override.row_version = override.row_version + 1

        await self.session.flush()

        await self._write_audit(
            user=user,
            organization_id=organization_id,
            entity_type="scenario_library_override",
            entity_id=override.id,
            action="library_override.update",
            changes={"version": [prev_version, override.version]},
            ip_address=ip_address,
        )
        return override

    async def delete_override(
        self,
        override_id: uuid.UUID,
        organization_id: uuid.UUID,
        user: User,
        ip_address: str | None = None,
    ) -> ScenarioLibraryOverride:
        """Soft-delete (tombstone) an override.

        Sets ``deleted_at`` to the current UTC timestamp. The row is
        preserved for audit-grade pin lookups; ``get_override`` will no
        longer surface it to callers.

        Raises:
            LibraryEntryNotFoundError: if the override row doesn't exist.
            IDORError: if the override belongs to a different organization.
        """
        stmt = (
            select(ScenarioLibraryOverride)
            .where(ScenarioLibraryOverride.id == override_id)
            .with_for_update()
        )
        override = (await self.session.execute(stmt)).scalar_one_or_none()
        if override is None:
            raise LibraryEntryNotFoundError(f"library override {override_id} not found")

        if override.organization_id != organization_id:
            raise IDORError(f"override {override_id} does not belong to this organization")

        override.deleted_at = datetime.now(UTC)
        await self.session.flush()

        await self._write_audit(
            user=user,
            organization_id=organization_id,
            entity_type="scenario_library_override",
            entity_id=override.id,
            action="library_override.delete",
            changes={"deleted_at": [None, override.deleted_at.isoformat()]},
            ip_address=ip_address,
        )
        return override

    # ------------------------------------------------------------------
    # P3 Task 6 — guarded delete of an imported entry (Option B)
    # ------------------------------------------------------------------

    async def delete_imported_entry(
        self,
        entry_id: uuid.UUID,
        user: User,
        ip_address: str | None = None,
    ) -> DeletedImportedEntry:
        """Hard-delete every version of an ``imported``-source library entry.

        Algorithm (plan-gate-resolved):
        1. Load ALL rows for the logical ``entry_id``. None → NotFound (404).
        2. Arch-I2: per-row guard — if ANY matched row is ``source !=
           "imported"`` the delete is REFUSED (403). ``seed`` entries are
           code-managed and never deletable on the runtime path.
        3. Arch-I1: override-FK guard — query ``ScenarioLibraryOverride``
           DIRECTLY (NO ``deleted_at`` filter; a tombstoned override still
           holds the composite FK and would raise IntegrityError on delete).
           If ANY row references the entry → REFUSED (403).
        4. Option B (warning only, never blocks): COUNT scenarios globally
           whose ``library_pin.entry_id`` == ``str(entry_id)`` (hyphenated,
           matching ``resolve_for_clone``'s pin construction).
        5. ``DELETE WHERE id == entry_id`` (every version of the logical id).
        6. Write a ``library_bundle.delete`` audit row (slug + pinned count).
        7. Return the result so the route can flash the warning.

        Commit ownership stays with the route layer (flush only here).
        """
        rows = (
            (
                await self.session.execute(
                    select(ScenarioLibraryEntry).where(ScenarioLibraryEntry.id == entry_id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            raise LibraryEntryNotFoundError(f"library entry {entry_id} not found")

        # Arch-I2: per-row guard — never assume a single row. A seed entry is
        # never deletable via this runtime path.
        for row in rows:
            if row.source != "imported":
                raise LibraryEntryDeleteRefusedError(
                    f"library entry {entry_id} has source={row.source!r}; only "
                    "imported entries can be deleted at runtime (seed entries "
                    "are code-managed)"
                )

        # Every version row of one logical id shares the same slug
        # (UNIQUE(slug, version) keeps slug constant across versions), so this
        # is a representative pick, not a data-dropping reduction.
        slug = rows[0].slug  # adapter-iter: ok — slug constant across versions

        # Arch-I1: override-FK guard — count tombstoned rows too (deleted_at
        # IS NOT NULL still holds the composite FK). Do NOT use repo.get_override
        # (it filters deleted_at IS NULL).
        override_refs = (
            await self.session.execute(
                select(func.count())
                .select_from(ScenarioLibraryOverride)
                .where(ScenarioLibraryOverride.library_entry_id == entry_id)
            )
        ).scalar_one()
        if override_refs:
            raise LibraryEntryDeleteRefusedError(
                f"an org override references library entry {entry_id}; remove the override first"
            )

        # Option B: pinned-scenario count (warning only). GLOBAL scan — the
        # entry is global, not org-scoped. library_pin.entry_id is the
        # hyphenated str(entry.id) per resolve_for_clone.
        pinned_count = (
            await self.session.execute(
                select(func.count())
                .select_from(Scenario)
                .where(func.json_extract(Scenario.library_pin, "$.entry_id") == str(entry_id))
            )
        ).scalar_one()

        await self.session.execute(
            sa_delete(ScenarioLibraryEntry).where(ScenarioLibraryEntry.id == entry_id)
        )

        await self._write_audit(
            user=user,
            organization_id=user.organization_id,
            entity_type="library_bundle",
            entity_id=entry_id,
            action="library_bundle.delete",
            changes={
                "slug": [slug, None],
                "versions_deleted": [None, len(rows)],
                "pinned_scenario_count": [None, pinned_count],
            },
            ip_address=ip_address,
        )
        await self.session.flush()

        return DeletedImportedEntry(
            slug=slug,
            versions_deleted=len(rows),
            pinned_scenario_count=pinned_count,
        )

    async def _write_audit(
        self,
        *,
        user: User,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        changes: dict[str, Any],
        ip_address: str | None = None,
    ) -> None:
        """Write a single audit log row via AuditWriter."""
        await AuditWriter(self.session).log(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            changes=changes,
            user_id=user.id,
            ip_address=ip_address,
        )

    @staticmethod
    def _shape_signature(dist: dict[str, Any] | None) -> str | None:
        """Extract the distribution kind string from a distribution dict.

        Returns None if the dict is None or has no 'distribution' key.
        Used by ``_shape_changed`` to detect PERT → Normal flips and
        leg additions/removals.
        """
        if dist is None:
            return None
        return dist.get("distribution")

    def _shape_changed(
        self,
        override: ScenarioLibraryOverride,
        draft: OverrideDraft,
    ) -> bool:
        """Return True if any distribution field's shape kind has changed.

        Shape change = the 'distribution' key differs between the current
        override value and the draft value (e.g. PERT → Normal, or a
        previously-None field now has a distribution, or vice versa).
        Pure parameter tuning (low/mode/high) within the same distribution
        kind is NOT a shape change.
        """
        pairs = [
            (override.threat_event_frequency, draft.threat_event_frequency),
            (override.vulnerability, draft.vulnerability),
            (override.primary_loss, draft.primary_loss),
            (override.secondary_loss, draft.secondary_loss),
        ]
        for current, proposed in pairs:
            if self._shape_signature(current) != self._shape_signature(proposed):
                return True
        return False
