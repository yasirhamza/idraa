"""Scenario repository — IDOR-safe lookup + paginated list.

No revision-table pattern: scenarios are user work product, not
versioned master data. The ``version: str`` field on Scenario is a
descriptive label chosen by the analyst, not an optimistic-lock
primitive. ``row_version: int`` is the lock primitive (set in
service layer mutations).

Mutations live in ``services/scenarios.py`` where audit logging
happens in the same DB session as the business write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import Select

from idraa.errors import ControlNotFoundForRunError, ScenarioNotFoundError
from idraa.models.control import Control
from idraa.models.enums import EntityStatus
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl


@dataclass(frozen=True)
class MitigatingControlsDiff:
    """Result of :meth:`ScenarioRepo.set_mitigating_controls` (issue #79 L6).

    The repo stays audit-agnostic (``AuditWriter`` calls belong at the
    service/route layer, mirroring ``ScenarioService`` owning audit), but
    callers that need to emit a ``scenario.controls_changed`` audit row —
    or decide whether a companion field-update was a true no-op — need to
    know (a) whether the join actually changed and (b) the full before/after
    id sets to put in the diff.
    """

    changed: bool
    before_ids: frozenset[uuid.UUID]
    after_ids: frozenset[uuid.UUID]


class ScenarioRepo:
    """Read/lookup methods for Scenario rows.

    Mirrors OverlayRepo / CalibrationOverrideRepo: db in ``__init__``,
    methods take only entity-specific kwargs.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_for_org(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        lock: bool = False,
    ) -> Scenario | None:
        """Org-scoped lookup. Returns None if the row exists in another org.

        ``lock=True`` adds ``SELECT ... FOR UPDATE`` on Postgres; on SQLite
        the dialect silently ignores it. ``expected_row_version`` form
        field on the route layer is the primary concurrency primitive
        in both environments — FOR UPDATE is a Postgres-only contention
        optimization for the read-then-write window.
        """
        stmt: Select[tuple[Scenario]] = select(Scenario).where(
            Scenario.id == scenario_id,
            Scenario.organization_id == organization_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self,
        *,
        organization_id: uuid.UUID,
        status: EntityStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Scenario], int]:
        """Paginated list with optional filters. Returns (rows, total_count)."""
        base_stmt = select(Scenario).where(Scenario.organization_id == organization_id)
        if status is not None:
            base_stmt = base_stmt.where(Scenario.status == status)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = (await self._db.execute(count_stmt)).scalar_one()

        rows_stmt = (
            base_stmt.order_by(Scenario.updated_at.desc())
            .limit(limit)
            .offset(offset)
            .options(selectinload(Scenario.organization))
        )
        rows = list((await self._db.execute(rows_stmt)).scalars().all())
        return rows, total

    async def count_for_org(
        self,
        *,
        organization_id: uuid.UUID,
        status: EntityStatus | None = None,
    ) -> int:
        """Org-scoped count, optionally filtered by status."""
        stmt = select(func.count(Scenario.id)).where(Scenario.organization_id == organization_id)
        if status is not None:
            stmt = stmt.where(Scenario.status == status)
        return (await self._db.execute(stmt)).scalar_one()

    async def list_pinned_library_entry_ids_for_org(
        self,
        organization_id: uuid.UUID,
        *,
        statuses: tuple[EntityStatus, ...] = (EntityStatus.ACTIVE,),
    ) -> list[str]:
        """Distinct ``library_pin.entry_id`` values across the org's scenarios.

        Feeds the dashboard scenario-coverage aggregate (Task 3, #478):
        covered = these ids, reference = the sector-applicable library
        entries. Scenarios with no library pin don't count — this excludes
        BOTH the SQL-NULL case (column never written) and the JSON-null
        case (SQLAlchemy's JSON type encodes an explicit Python ``None`` as
        the JSON literal ``'null'``, not SQL NULL; mirrors the NULLIF guard
        in ``scenario_library_repo.py``'s ``_is_json_null``).

        ``statuses`` defaults to ACTIVE-only (epic #34 P1a): a DRAFT
        scenario's library pin is a review-pending prior, not yet counted
        as coverage. Callers that ever need drafts included must pass
        ``statuses`` explicitly.
        """
        stmt = (
            select(func.json_extract(Scenario.library_pin, "$.entry_id"))
            .where(Scenario.organization_id == organization_id)
            .where(Scenario.status.in_(statuses))
            .where(func.nullif(Scenario.library_pin, "null").isnot(None))
            .distinct()
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        return [r for r in rows if r is not None]

    async def set_mitigating_controls(
        self,
        *,
        scenario_id: uuid.UUID,
        organization_id: uuid.UUID,
        control_ids: list[uuid.UUID],
        eligible_control_ids: set[uuid.UUID] | None = None,
    ) -> MitigatingControlsDiff:
        """Reconcile the scenario_controls join.

        Validates every ``control_id`` belongs to the org; raises
        ControlNotFoundForRunError on miss (including cross-org).
        Inserts new rows for added IDs. Caller commits.

        Removal semantics depend on ``eligible_control_ids`` (issue #217):

        - ``eligible_control_ids is None`` (default — create/wizard paths,
          which start with no pre-existing links): full diff-apply. Any
          existing link not in ``control_ids`` is removed.
        - ``eligible_control_ids`` provided (the scenario *edit* path): the
          submission only spoke for the controls the form could render, so
          removals are scoped to ``current ∩ eligible``. Existing links to
          controls OUTSIDE the eligible set (e.g. a DRAFT/DEPRECATED control
          whose checkbox the ACTIVE-only edit form never showed) are
          PRESERVED rather than silently wiped. ``control_ids`` should
          itself be a subset of the eligible set; any id outside it is still
          inserted if missing (defensive — the caller controls eligibility).

        The eligible set is the set the form rendered (its ACTIVE controls),
        NOT a status filter applied here — keeping the repo agnostic to the
        control-lifecycle policy and letting the route own "what the form
        could show".

        Returns a :class:`MitigatingControlsDiff` — ``changed`` is
        ``bool(to_add or to_remove)`` (issue #79 L6); ``before_ids`` /
        ``after_ids`` are the FULL link sets (not just the delta), mirroring
        the before/after snapshot shape ``set_scenario_attack_mappings``
        already emits into ``scenario.update`` audit rows. This method never
        writes an audit row itself — that stays at the service/route layer.
        """
        if control_ids:
            existing = (
                (
                    await self._db.execute(
                        select(Control.id)
                        .where(Control.id.in_(control_ids))
                        .where(Control.organization_id == organization_id)
                    )
                )
                .scalars()
                .all()
            )
            existing_set = set(existing)
            missing = [cid for cid in control_ids if cid not in existing_set]
            if missing:
                raise ControlNotFoundForRunError(
                    f"control_ids not in org={organization_id} inventory: {missing}"
                )

        current = (
            (
                await self._db.execute(
                    select(ScenarioControl.control_id).where(
                        ScenarioControl.scenario_id == scenario_id
                    )
                )
            )
            .scalars()
            .all()
        )
        current_set = set(current)
        target_set = set(control_ids)

        if eligible_control_ids is None:
            # Full diff-apply (create/wizard): nothing pre-existing to protect.
            to_remove = current_set - target_set
        else:
            # Edit path: only reconcile within the set the form could render.
            # Links to controls outside `eligible_control_ids` are untouched.
            to_remove = (current_set & eligible_control_ids) - target_set
        to_add = target_set - current_set

        if to_remove:
            await self._db.execute(
                delete(ScenarioControl)
                .where(ScenarioControl.scenario_id == scenario_id)
                .where(ScenarioControl.control_id.in_(to_remove))
            )

        for cid in to_add:
            self._db.add(
                ScenarioControl(
                    scenario_id=scenario_id,
                    control_id=cid,
                )
            )

        await self._db.flush()

        return MitigatingControlsDiff(
            changed=bool(to_add or to_remove),
            before_ids=frozenset(current_set),
            after_ids=frozenset((current_set - to_remove) | to_add),
        )

    async def get_for_org_or_raise(
        self,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
    ) -> Scenario:
        """Wraps get_for_org; raises ScenarioNotFoundError on miss."""
        scenario = await self.get_for_org(
            organization_id=organization_id,
            scenario_id=scenario_id,
        )
        if scenario is None:
            raise ScenarioNotFoundError(f"scenario id={scenario_id} not in org={organization_id}")
        return scenario

    async def fetch_by_ids_for_org(
        self,
        organization_id: uuid.UUID,
        scenario_ids: list[uuid.UUID],
    ) -> list[Scenario]:
        """Fetch Scenarios scoped to org; rejects cross-org IDs silently.

        Eagerly loads mitigating_controls (calibration needs them downstream).
        ``populate_existing=True`` forces SQLAlchemy to refresh already-loaded
        instances in the session's identity map, including re-firing the
        ``mitigating_controls`` selectinload. Without it, a Scenario cached
        from a prior call with ``mitigating_controls=[]`` would mask
        freshly-committed ScenarioControl M2M rows (issue #101).

        Caller contract: do NOT mutate a returned Scenario and then re-call
        this method within the same session — ``populate_existing`` will
        overwrite the local in-memory changes with database state.
        """
        if not scenario_ids:
            return []
        stmt = (
            select(Scenario)
            .where(Scenario.organization_id == organization_id)
            .where(Scenario.id.in_(scenario_ids))
            .options(selectinload(Scenario.mitigating_controls))
            .execution_options(populate_existing=True)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())
