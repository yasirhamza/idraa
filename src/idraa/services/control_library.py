"""Control-library services (P2b, #437).

- ControlLibraryService: read-only browse / fetch query over the catalog.
  Published-only, paginated, filterable by FAIR-CAM function / framework /
  control type / industry + full-text search. Mirrors ScenarioLibraryService
  list_browseable.

- flag_runs_stale_for_control: Issue #437 T8 — when a library entry is
  re-curated (version bumped) and a deployed control later re-syncs (#438),
  call this helper to flag affected completed runs as stale (`is_stale`; the run
  stays RunStatus.COMPLETED so it remains visible). The re-sync
  trigger itself is wired in #438; this module owns the flagging primitive.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from idraa.models.control_library import ControlLibraryEntry, ControlLibraryEntryAssignment
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus


async def flag_runs_stale_for_control(
    session: AsyncSession,
    organization_id: uuid.UUID,
    control_id: uuid.UUID,
) -> int:
    """Set ``is_stale=True`` on COMPLETED runs in the org that used the given control.

    Called when a library entry is re-curated (version bumped) and the deployed
    control is about to re-sync (#438).  Any run that would now produce a
    different result due to the re-sync is flagged so the UI / reports can
    surface a "re-run recommended" prompt.  The run stays ``COMPLETED`` and
    remains visible to all COMPLETED-gated consumers (reports, PDF, dashboard).

    Run ↔ control linkage
    ----------------------
    Both SINGLE and AGGREGATE runs record their controls in
    ``RiskAnalysisRun.control_ids_used`` as a JSON list of hyphenated-UUID
    strings (the executor writes ``str(control_id)`` at run-create time — see
    ``services/runs.py``).  The membership test is a necessary Python-side
    filter (SQLite has no JSON_CONTAINS), now bounded by org + column-
    restriction so the heavy ``simulation_results`` blob is NOT loaded.

    Idempotency
    -----------
    Only COMPLETED + ``is_stale=False`` runs are considered.  A run already
    flagged is neither re-written nor counted on a subsequent call (the
    function returns 0 when all affected runs are already flagged).

    Parameters
    ----------
    session:
        Active async session (caller owns commit / flush lifecycle).
    organization_id:
        Scope the sweep to a single org — cross-org writes are not allowed.
    control_id:
        The control whose re-sync triggered the staleness sweep.

    Returns
    -------
    int
        Number of runs flagged ``is_stale=True`` on this call.
    """
    cid_str = str(control_id)  # hyphenated format matching control_ids_used storage
    stmt = (
        select(RiskAnalysisRun)
        .where(
            RiskAnalysisRun.organization_id == organization_id,
            RiskAnalysisRun.status == RunStatus.COMPLETED,
            RiskAnalysisRun.is_stale == False,  # noqa: E712 — SQLAlchemy == False is intentional
        )
        .options(
            load_only(
                RiskAnalysisRun.id,
                RiskAnalysisRun.status,
                RiskAnalysisRun.control_ids_used,
                RiskAnalysisRun.is_stale,
            )
        )
    )
    result = await session.execute(stmt)
    runs = result.scalars().all()

    count = 0
    for run in runs:
        # control_ids_used membership test: necessary Python-side filter
        # (SQLite has no JSON_CONTAINS); bounded by org-scope + load_only
        # above so simulation_results blob is never loaded.
        if cid_str in (run.control_ids_used or []):
            run.is_stale = True
            count += 1

    if count:
        await session.flush()

    return count


@dataclass
class ControlLibraryBrowseFilters:
    sub_functions: list[FairCamSubFunction] = field(default_factory=list)
    control_types: list[ControlType] = field(default_factory=list)
    nist_csf_subcategories: list[str] = field(default_factory=list)
    cis_safeguards: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    search_text: str | None = None


@dataclass
class ControlLibraryBrowsePage:
    entries: list[ControlLibraryEntry]
    total: int
    page: int


class ControlLibraryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def _base_stmt(self, filters: ControlLibraryBrowseFilters) -> Select[Any]:
        # Collapse to the latest published version per logical id (mirrors
        # ScenarioLibraryRepo.list_published). The data model permits multiple
        # published rows sharing one id at different versions (composite (id,
        # version) PK), so browse must surface at most one row per id — the
        # MAX(version) among status='published' — else versioned publishing
        # would leak stale snapshots into browse. Window-function-free
        # MAX(version) GROUP BY id subquery JOINed back (SQLite compatible).
        latest_subq = (
            select(
                ControlLibraryEntry.id.label("id"),
                func.max(ControlLibraryEntry.version).label("max_version"),
            )
            .where(ControlLibraryEntry.status == "published")
            .group_by(ControlLibraryEntry.id)
            .subquery()
        )
        stmt = select(ControlLibraryEntry).join(
            latest_subq,
            and_(
                ControlLibraryEntry.id == latest_subq.c.id,
                ControlLibraryEntry.version == latest_subq.c.max_version,
            ),
        )
        if filters.control_types:
            stmt = stmt.where(ControlLibraryEntry.control_type.in_(filters.control_types))
        if filters.sub_functions:
            # Correlate the assignment match on the SURFACED (id, version), not
            # on library_entry_id alone — once two published versions coexist,
            # keying on id alone could surface a version whose current
            # assignments don't claim the filtered function. EXISTS on the
            # composite ensures an entry surfaces ONLY IF its surfaced
            # (latest-published) version has an assignment with that sub_function.
            stmt = stmt.where(
                select(ControlLibraryEntryAssignment.id)
                .where(
                    ControlLibraryEntryAssignment.library_entry_id == ControlLibraryEntry.id,
                    ControlLibraryEntryAssignment.library_entry_version
                    == ControlLibraryEntry.version,
                    ControlLibraryEntryAssignment.sub_function.in_(filters.sub_functions),
                )
                .exists()
            )
        if filters.search_text:
            escaped = (
                filters.search_text.lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            like = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    func.lower(ControlLibraryEntry.name).like(like, escape="\\"),
                    func.lower(ControlLibraryEntry.description).like(like, escape="\\"),
                    func.lower(ControlLibraryEntry.slug).like(like, escape="\\"),
                )
            )
        return stmt

    @staticmethod
    def _matches_json_facets(e: ControlLibraryEntry, filters: ControlLibraryBrowseFilters) -> bool:
        # JSON-list facets filtered in Python (correct + simple at canonical-catalog
        # scale — dozens of rows, not per-org AGGREGATE scale). Applied BEFORE
        # pagination so counts/pages stay correct. OR-within-facet, AND-across-facets.
        if filters.nist_csf_subcategories and not (
            set(filters.nist_csf_subcategories) & set(e.nist_csf_subcategories or [])
        ):
            return False
        if filters.cis_safeguards and not (
            set(filters.cis_safeguards) & set(e.cis_safeguards or [])
        ):
            return False
        return not (
            filters.industries
            and not (set(filters.industries) & set(e.applicable_industries or []))
        )

    async def list_browseable(
        self, *, filters: ControlLibraryBrowseFilters, page: int, page_size: int
    ) -> ControlLibraryBrowsePage:
        # SQL filters (status/type/sub_function/search) narrow first; JSON-list facets
        # (nist/cis/industry) post-filter in Python, then paginate the filtered set.
        base = self._base_stmt(filters).order_by(ControlLibraryEntry.name)
        all_rows = (await self._db.execute(base)).scalars().all()
        filtered = [e for e in all_rows if self._matches_json_facets(e, filters)]
        total = len(filtered)
        offset = (page - 1) * page_size
        return ControlLibraryBrowsePage(
            entries=filtered[offset : offset + page_size], total=total, page=page
        )

    async def get_published(
        self, entry_id: uuid.UUID, version: int | None = None
    ) -> ControlLibraryEntry | None:
        stmt = select(ControlLibraryEntry).where(
            ControlLibraryEntry.id == entry_id, ControlLibraryEntry.status == "published"
        )
        if version is not None:
            stmt = stmt.where(ControlLibraryEntry.version == version)
        else:
            stmt = stmt.order_by(ControlLibraryEntry.version.desc())
        return (await self._db.execute(stmt.limit(1))).scalars().first()

    async def get_published_by_slug(self, slug: str) -> ControlLibraryEntry | None:
        """Latest published catalog entry for a stable slug (None if absent/unpublished)."""
        stmt = (
            select(ControlLibraryEntry)
            .where(ControlLibraryEntry.slug == slug, ControlLibraryEntry.status == "published")
            .order_by(ControlLibraryEntry.version.desc())
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalars().first()
