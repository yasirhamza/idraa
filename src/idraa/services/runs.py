"""RunService — orchestrates RiskAnalysisRun lifecycle.

create_and_dispatch validates the trigger inputs, freezes them on a
new RiskAnalysisRun row, writes audit, and dispatches:
- mc_iterations<1000 → execute synchronously inline (returns COMPLETED)
- mc_iterations>=1000 → queue BG task via FastAPI BackgroundTasks
                         (returns QUEUED)

cancel is idempotent: terminal runs are returned unchanged.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, cast

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.errors import (
    RunBusyError,
    RunNotFoundError,
    RunValidationError,
    ScenarioNotFoundError,
)
from idraa.models._types import now_utc
from idraa.models.risk_analysis_run import (
    RiskAnalysisRun,
    RunStatus,
    RunType,
)
from idraa.models.run_samples import RunSamples
from idraa.repositories.run_repo import RunRepo
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.services.audit import AuditWriter
from idraa.services.run_executor import execute_run
from idraa.services.run_inputs_hash import (
    ScenarioLike,
    build_aggregate_inputs_hash,
    build_inputs_hash,
)
from idraa.services.sample_codec import decode_sample_arrays

_MIN_ITERATIONS = 100
_SYNC_THRESHOLD = 1000
_SEED_MAX = 2**32 - 1


def _sqlite_db_dir(database_url: str) -> Path | None:
    """Return the parent directory of a file-backed SQLite DB URL, else None.

    Sec-I2: Settings exposes ``database_url`` (a SQLAlchemy DSN), not a bare
    filesystem path — e.g. ``sqlite+aiosqlite:////abs/path/idraa.db`` or
    ``sqlite:///relative.db``. Only file-backed sqlite/sqlite+aiosqlite URLs
    have a volume worth checking; a ``:memory:`` database or a non-sqlite
    backend (Postgres, managed elsewhere) returns None so the caller skips
    the disk-free check entirely.
    """
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database).resolve().parent


class RunService:
    def __init__(self, db: AsyncSession) -> None:
        """Mirrors ScenarioService — single-arg construction. Audit writes
        use AuditWriter(self._db) per-call (not injected)."""
        self._db = db

    async def create_and_dispatch(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_ids: list[uuid.UUID],
        mc_iterations_override: int | None,
        created_by: uuid.UUID,
        background_tasks: BackgroundTasks,
        name: str | None = None,
        random_seed: int = 42,
    ) -> RiskAnalysisRun:
        """Create + dispatch a run.

        Issue #89: controls are strictly coupled to scenarios. No per-run
        override exists — controls are always derived from each scenario's
        own ``mitigating_controls``. For AGGREGATE, ``control_ids_used`` is
        the deduplicated union (universe for snapshot loader); the per-
        scenario sets are frozen on ``aggregate_control_ids_per_scenario``.
        """
        if not scenario_ids:
            raise RunValidationError("scenario_ids must be non-empty")

        scenario_repo = ScenarioRepo(self._db)
        is_single = len(scenario_ids) == 1

        if is_single:
            # SINGLE path: fetch the one scenario; preserve existing behaviour
            scenario = await scenario_repo.get_for_org_or_raise(
                organization_id,
                scenario_ids[0],
            )
            scenarios = [scenario]
        else:
            # AGGREGATE path: bulk-fetch; raise if any IDs are unknown/cross-org
            scenarios = await scenario_repo.fetch_by_ids_for_org(
                organization_id,
                scenario_ids,
            )
            fetched_ids = {s.id for s in scenarios}
            missing = [sid for sid in scenario_ids if sid not in fetched_ids]
            if missing:
                raise ScenarioNotFoundError(f"scenario_ids not found in org: {missing}")

        # PR π: explicit mc_iterations is required; the Scenario.mc_iterations
        # fallback was retired (Scenario default removed in F14). The DoS guard
        # below also closes round-1 security review MINOR-4.
        if mc_iterations_override is None:
            raise RunValidationError(
                "RunService.create_and_dispatch requires mc_iterations_override; "
                "Scenario.mc_iterations was removed in PR π."
            )
        effective_iterations = mc_iterations_override
        # Issue #259: the upper bound is the Settings.mc_iterations_max OOM cap
        # (default 100_000, tunable via MC_ITERATIONS_MAX), NOT a hardcoded 1M
        # ceiling. Enforcing it here at the service boundary means every dispatch
        # path — POST /analyses and the legacy POST /scenarios/{id}/run adapter —
        # shares one gate, closing the legacy bypass that could OOM the worker.
        iter_max = get_settings().mc_iterations_max
        if not (_MIN_ITERATIONS <= effective_iterations <= iter_max):
            raise RunValidationError(
                f"mc_iterations={effective_iterations} out of range [{_MIN_ITERATIONS}, {iter_max}]"
            )

        # Sec-I2 (2026-06-29 outage class): reject dispatch outright when the
        # DB volume is already low on free space, so a burst of high-N/high-M
        # runs cannot refill the 3 GB volume between 14-day retention purges.
        # Check-then-dispatch, no byte reservation — closes sequential
        # accumulation, not a concurrent burst (documented out-of-scope; a
        # single-flight cap is the future fix for that residual).
        db_dir = _sqlite_db_dir(get_settings().database_url)
        if db_dir is not None:
            free = shutil.disk_usage(db_dir).free
            if free < get_settings().min_free_disk_bytes:
                raise RunValidationError(
                    "Insufficient disk space to store simulation samples — "
                    "free up space or wait for retention to purge old runs."
                )

        # Issue #508 (PR2 final-gate Sec-I): single-flight cap for high-fidelity
        # runs. Raising mc_iterations_max to 1M scaled each max-N run's peak RSS
        # ~10x (~700 MB), so unbounded concurrent high-N dispatch could OOM the
        # 4 GB VM. Reject a new high-N run when the cap of concurrent in-flight
        # (RUNNING + QUEUED) high-N runs is already met. Counted from the DB, so
        # it self-heals via the run reaper on an OOM-orphaned RUNNING row — no
        # in-process counter to leak on SIGKILL. GLOBAL, not org-scoped: the VM
        # RAM is shared across orgs. A small TOCTOU between two truly-simultaneous
        # dispatches is acceptable (a rare off-by-one still fits at ~700 MB/run).
        settings = get_settings()
        if effective_iterations >= settings.high_fidelity_iterations_threshold:
            inflight_high_n = await self._db.scalar(
                select(func.count())
                .select_from(RiskAnalysisRun)
                .where(
                    RiskAnalysisRun.status.in_((RunStatus.RUNNING, RunStatus.QUEUED)),
                    RiskAnalysisRun.mc_iterations >= settings.high_fidelity_iterations_threshold,
                )
            )
            if (inflight_high_n or 0) >= settings.max_concurrent_high_fidelity_runs:
                raise RunValidationError(
                    "High-fidelity capacity is busy: at most "
                    f"{settings.max_concurrent_high_fidelity_runs} runs of "
                    f"{settings.high_fidelity_iterations_threshold:,}+ iterations can "
                    "run at once (memory limit). Wait for one to finish, or lower the "
                    "iteration count."
                )

        if not isinstance(random_seed, int) or not (0 <= random_seed <= _SEED_MAX):
            raise RunValidationError(f"random_seed must be an integer in [0, {_SEED_MAX}]")

        # Issue #89: derive controls from scenarios. No override path.
        # Issue #395: only controls whose implementation_stage contributes to
        # the composition are gathered here, so control_ids_used reflects what
        # actually composed (and run_executor's fetch never sees the rest).
        # The picker (ControlRepo.list_for_org) already hides non-active
        # controls; this gate also covers controls attached while active and
        # later demoted. Single decision point: the enum predicate.
        per_scenario_dict: dict[str, list[str]] | None
        if is_single:
            control_ids = [
                c.id
                for c in scenarios[0].mitigating_controls
                if c.implementation_stage.contributes_to_composition
            ]
            per_scenario_dict = None
        else:
            per_scenario_dict = {
                str(s.id): [
                    str(c.id)
                    for c in s.mitigating_controls
                    if c.implementation_stage.contributes_to_composition
                ]
                for s in scenarios
            }
            seen: set[uuid.UUID] = set()
            control_ids = []
            for s in scenarios:
                for c in s.mitigating_controls:
                    if c.implementation_stage.contributes_to_composition and c.id not in seen:
                        seen.add(c.id)
                        control_ids.append(c.id)

        if is_single:
            # Mapped[str] attributes on Scenario satisfy ScenarioLike's str
            # protocol at runtime; cast bridges the mypy v1.11 strict check.
            inputs_hash = build_inputs_hash(
                cast(ScenarioLike, scenarios[0]),
                control_ids=control_ids,
                mc_iterations=effective_iterations,
                random_seed=random_seed,
            )
            run = RiskAnalysisRun(
                id=uuid.uuid4(),
                organization_id=organization_id,
                scenario_id=scenarios[0].id,
                run_type=RunType.SINGLE,
                status=RunStatus.QUEUED,
                mc_iterations=effective_iterations,
                random_seed=random_seed,
                inputs_hash=inputs_hash,
                controls_snapshot=[],
                control_ids_used=[str(cid) for cid in control_ids],
                created_by=created_by,
                name=name,
            )
            audit_changes: dict[str, object] = {
                "scenario_id": str(scenarios[0].id),
                "mc_iterations": effective_iterations,
                "random_seed": random_seed,
                "control_count": len(control_ids),
            }
            if name is not None:
                audit_changes["name"] = name
        else:
            inputs_hash = build_aggregate_inputs_hash(
                [cast(ScenarioLike, s) for s in scenarios],
                control_ids=control_ids,
                mc_iterations=effective_iterations,
                random_seed=random_seed,
            )
            sorted_ids = sorted(str(s.id) for s in scenarios)
            run = RiskAnalysisRun(
                id=uuid.uuid4(),
                organization_id=organization_id,
                scenario_id=None,
                run_type=RunType.AGGREGATE,
                status=RunStatus.QUEUED,
                mc_iterations=effective_iterations,
                random_seed=random_seed,
                inputs_hash=inputs_hash,
                controls_snapshot=[],
                control_ids_used=[str(cid) for cid in control_ids],
                aggregate_scenario_ids=sorted_ids,
                aggregate_control_ids_per_scenario=per_scenario_dict,
                created_by=created_by,
                name=name,
            )
            audit_changes = {
                "scenario_ids": sorted_ids,
                "mc_iterations": effective_iterations,
                "random_seed": random_seed,
                "control_count": len(control_ids),
                # M3: per-scenario forensics — captures which controls applied to
                # which scenario at run-create time. Frozen column is the source
                # of truth; this is the immutable audit-log echo.
                "aggregate_control_ids_per_scenario": per_scenario_dict,
            }
            if name is not None:
                audit_changes["name"] = name

        await RunRepo(self._db).create(run)
        await AuditWriter(self._db).log(
            organization_id=organization_id,
            user_id=created_by,
            action="risk_analysis_run.create",
            entity_type="risk_analysis_run",
            entity_id=run.id,
            changes=audit_changes,
        )
        await self._db.commit()

        if effective_iterations < _SYNC_THRESHOLD:
            run_id = run.id  # save before expire() marks it stale
            await execute_run(run_id)
            # execute_run owns its own session; expire our copy so the next attribute
            # access re-queries via our session and sees the executor's commits.
            self._db.expire(run)
            reloaded = await self._db.get(RiskAnalysisRun, run_id)
            if reloaded is None:
                raise RunNotFoundError(f"run id={run_id} vanished after execute_run")
            return reloaded
        else:
            background_tasks.add_task(execute_run, run.id)
            return run

    async def cancel(
        self,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        cancelled_by: uuid.UUID,
    ) -> RiskAnalysisRun:
        run = await RunRepo(self._db).get_for_org_or_raise(
            organization_id,
            run_id,
        )
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return run  # idempotent on terminal

        prev_status = run.status
        run.status = RunStatus.CANCELLED
        run.completed_at = now_utc()
        await AuditWriter(self._db).log(
            organization_id=organization_id,
            user_id=cancelled_by,
            action="risk_analysis_run.cancel",
            entity_type="risk_analysis_run",
            entity_id=run.id,
            changes={"status": [prev_status.value, RunStatus.CANCELLED.value]},
        )
        await self._db.commit()
        return run

    async def delete_run(
        self,
        run_id: uuid.UUID,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        force: bool = False,
    ) -> None:
        """Hard-delete a run row (#297).

        Org-scoped: ``get_for_org_or_raise`` resolves ownership FIRST
        (raises ``RunNotFoundError`` -> route 404 on cross-org / unknown).

        In-flight guard: QUEUED / RUNNING runs raise ``RunBusyError``
        (-> route 409) unless ``force=True`` — deleting a row the
        background executor still holds can orphan the executor's commit.

        The 1:1 ``run_samples`` row is removed by the DB-level
        ``ON DELETE CASCADE`` FK, so no explicit child delete here.

        Audit is written BEFORE ``db.delete`` so the audit row's
        ``entity_id`` references a row that still exists at flush time
        (mirrors ScenarioService.delete).
        """
        run = await RunRepo(self._db).get_for_org_or_raise(org_id, run_id)
        if run.status in (RunStatus.RUNNING, RunStatus.QUEUED) and not force:
            raise RunBusyError(
                f"run id={run_id} is {run.status.value}; cancel it first or "
                f"re-submit with force=True"
            )
        await AuditWriter(self._db).log(
            organization_id=org_id,
            user_id=user_id,
            action="risk_analysis_run.delete",
            entity_type="risk_analysis_run",
            entity_id=run_id,
            changes={"status": [run.status.value, "deleted"]},
        )
        await self._db.delete(run)
        await self._db.commit()

    async def purge_samples(
        self,
        run_id: uuid.UUID,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Delete just the heavy ``run_samples`` row, keep the run + summary (#297).

        Org-scoped: ``get_for_org_or_raise`` resolves ownership FIRST so
        this is never an existence oracle.

        Idempotent: if the ``run_samples`` row is already absent (never
        persisted, or previously purged) this is a silent no-op with NO
        audit row — documented decision so re-purges don't spam the audit
        log with no-state-change entries.
        """
        await RunRepo(self._db).get_for_org_or_raise(org_id, run_id)
        # Bare-PK RunSamples fetch is exempt from the no-bare-PK rule: run_id
        # was just org-verified above, and run_samples is 1:1 with the run.
        row = await self._db.get(RunSamples, run_id)
        if row is not None:
            await AuditWriter(self._db).log(
                organization_id=org_id,
                user_id=user_id,
                action="risk_analysis_run.purge_samples",
                entity_type="risk_analysis_run",
                entity_id=run_id,
                changes={"samples": ["present", "purged"]},
            )
            await self._db.delete(row)
        await self._db.commit()

    async def get_for_org(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RiskAnalysisRun:
        return await RunRepo(self._db).get_for_org_or_raise(
            organization_id,
            run_id,
        )

    async def load_samples(
        self,
        run_id: uuid.UUID,
        *,
        org_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Lazily load the heavy per-iteration sample arrays for a run.

        The arrays were split off ``simulation_results`` into the 1:1
        ``run_samples`` table (#294/#297) and are loaded only by the full-
        distribution / CSV-export paths — never on list/dashboard reads.

        Org-scoped: ``get_for_org_or_raise`` resolves ownership FIRST (raises
        ``RunNotFoundError`` on cross-org / unknown), so this is never an
        existence oracle. Returns None when the ``run_samples`` row is absent
        (never persisted, or purged) — callers treat None as "samples purged".
        """
        # Ownership gate: 404/NotFound on cross-org before touching run_samples.
        await RunRepo(self._db).get_for_org_or_raise(org_id, run_id)
        # Bare-PK RunSamples fetch is exempt from the no-bare-PK rule: run_id was
        # just org-verified above, and run_samples is 1:1 with the run (PK=FK).
        row = await self._db.get(RunSamples, run_id)
        if row is None:
            return None
        if row.arrays_codec is not None:
            return decode_sample_arrays(row.arrays_codec)
        return row.arrays  # legacy JSON row, not yet aged out

    async def list_history(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[RiskAnalysisRun], int]:
        offset = max(0, (page - 1) * page_size)
        repo = RunRepo(self._db)
        rows = await repo.list_for_scenario(
            organization_id,
            scenario_id,
            limit=page_size,
            offset=offset,
        )
        total = await repo.count_for_scenario(organization_id, scenario_id)
        return rows, total

    async def list_history_for_org(
        self,
        *,
        organization_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[RiskAnalysisRun], int]:
        """Org-wide paginated run history (all scenarios, all statuses)."""
        offset = max(0, (page - 1) * page_size)
        repo = RunRepo(self._db)
        rows = await repo.list_for_org(
            organization_id,
            limit=page_size,
            offset=offset,
        )
        total = await repo.count_for_org(organization_id)
        return rows, total
