"""RiskAnalysisRun ORM — record of a Monte Carlo simulation execution.

A run is the unit of risk-quantification output. It captures:

- The frozen run-trigger inputs (mc_iterations, controls_snapshot,
  inputs_hash, control_ids_used) — what produced this output.
- The full Monte Carlo payload (base + residual sample arrays, VaR/ES,
  loss exceedance curve, per-control adjustments) — per master design
  decision 6: persist FULL output, never summaries.
- Lifecycle metadata (status enum + timestamps + error_message).

Runs are immutable once created; updates only modify status + lifecycle
timestamps + simulation_results. The created/edited semantics of CRUD
do NOT apply here — runs are append-only.
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from idraa.db import Base
from idraa.models._types import UtcDateTime
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin

if TYPE_CHECKING:
    from idraa.models.run_samples import RunSamples


class RunType(StrEnum):
    SINGLE = "single"
    AGGREGATE = "aggregate"  # multi-scenario portfolio; PR xi


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskAnalysisRun(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "risk_analysis_runs"

    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="RESTRICT"),
        nullable=True,
    )
    run_type: Mapped[RunType] = mapped_column(
        SAEnum(RunType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=RunType.SINGLE,
        nullable=False,
    )
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=RunStatus.QUEUED,
        nullable=False,
    )

    # Q15 (omicron-1): user-supplied label for the run. Nullable so existing
    # rows stay valid without backfill; the dashboard view-model falls back
    # to display_name_fallback() when name is None.
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Frozen run-trigger inputs (immutable after create)
    mc_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    # Phase 1 MC-seed reproducibility: user-supplied base seed for the RNG.
    # Nullable so existing runs (which used no explicit seed) remain valid.
    random_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    controls_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    # Frozen at run-create time (BEFORE controls_snapshot is filled at RUNNING flip).
    # Issue #89: derived from each selected scenario's mitigating_controls — no
    # per-run override exists. For SINGLE: the one scenario's controls. For
    # AGGREGATE: the deduplicated union (universe); see
    # aggregate_control_ids_per_scenario for the per-scenario breakdown. The
    # executor reads THIS list (not the live scenario_controls join), so the
    # run's controls are stable even if scenarios are edited later.
    control_ids_used: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    # AGGREGATE: list of constituent scenario IDs (UUID strings).
    # Invariant: SINGLE -> scenario_id set, aggregate_scenario_ids=NULL;
    #            AGGREGATE -> scenario_id=NULL, aggregate_scenario_ids set with len >= 2.
    # PR xi activates the AGGREGATE path; PR rho may normalize to a join table.
    aggregate_scenario_ids: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    @validates("aggregate_scenario_ids")
    def _validate_aggregate_scenario_ids_minimum_length(
        self, key: str, value: list[str] | None
    ) -> list[str] | None:
        """Defense-in-depth: when aggregate_scenario_ids is set, len must be >= 2.

        Limited scope: SQLAlchemy @validates fires on Python __init__ + attribute
        assignment only -- does NOT fire on raw SQL INSERT or row load from DB.
        Catches programmer error in service-layer + test-layer Python construction;
        cross-field invariant (scenario_id IS NULL when value IS NOT NULL) and
        raw-INSERT defense are deferred to PR rho's DB CHECK constraints.
        """
        if value is not None and len(value) < 2:
            raise ValueError(f"aggregate_scenario_ids must have len>=2 when set; got {len(value)}")
        return value

    # Issue #89: per-scenario active controls for AGGREGATE runs.
    # SINGLE -> NULL; AGGREGATE -> dict keyed by str(scenario_id), values are
    # list[str(control_id)] subsets of control_ids_used. The cross-field
    # invariant is enforced by @validates below. fair_cam consumes this via
    # per_scenario_active_control_ids param.
    aggregate_control_ids_per_scenario: Mapped[dict[str, list[str]] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    @validates("aggregate_control_ids_per_scenario")
    def _validate_aggregate_control_ids_per_scenario(
        self, key: str, value: dict[str, list[str]] | None
    ) -> dict[str, list[str]] | None:
        """Defense-in-depth — shape + cross-field invariant (M2 plan-gate finding).

        Cross-field: when set together with aggregate_scenario_ids, dict keys MUST
        equal set(aggregate_scenario_ids). Values MUST be subsets of control_ids_used.
        Per CLAUDE.md "Data contract enforcement" — push the check down to the
        model; do not defer.

        Same SQLAlchemy @validates scope-limit applies (Python attribute assignment,
        not raw INSERT/DB load).
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(
                f"aggregate_control_ids_per_scenario must be dict | None; got {type(value)}"
            )
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"aggregate_control_ids_per_scenario keys must be str; got {type(k)} for {k!r}"
                )
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError(f"aggregate_control_ids_per_scenario[{k!r}] must be list[str]")
        # Cross-field: keys ⊆ aggregate_scenario_ids when both set.
        if self.aggregate_scenario_ids is not None and set(value.keys()) != set(
            self.aggregate_scenario_ids
        ):
            raise ValueError(
                f"aggregate_control_ids_per_scenario keys {set(value.keys())} "
                f"must equal aggregate_scenario_ids {set(self.aggregate_scenario_ids)}"
            )
        # Cross-field: values ⊆ control_ids_used when set.
        if self.control_ids_used:
            universe = set(self.control_ids_used)
            for k, v in value.items():
                if not set(v).issubset(universe):
                    raise ValueError(
                        f"aggregate_control_ids_per_scenario[{k!r}] contains ids "
                        f"not in control_ids_used universe"
                    )
        return value

    # T2 (#351): Scenario input snapshot captured by the executor AT EXECUTION
    # TIME from the scenario objects the executor actually loads, written BEFORE
    # the engine call. This is the "as-executed" record: scenarios can be edited
    # between queue and execution, so a run-create snapshot would capture stale
    # queue-time values; the executor's read is the as-executed truth.
    # Shape: {"scenarios": [{scenario_id, scenario_name, threat_event_frequency,
    #          vulnerability, primary_loss, secondary_loss}, ...]}
    # server_default=NULL; runs predating this column render "Current scenario
    # values (run predates input snapshots — values may differ from as-executed)".
    scenario_inputs_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )

    # Issue #419 (control-weight-robustness): persisted weight-uncertainty
    # ensemble output -- per-control reduction-$ p5/p50/p95 ranges, rank-stability
    # metrics, the sampling band {logit_sigma, distribution, seed, draws,
    # eval_budget, min_draws}, and the canonical reference {control_id: float}.
    # NULL on FAILED/lost-race runs and on legacy (pre-#419) runs (column default).
    # Mirrors scenario_inputs_snapshot (Arch-N4): nullable + default=None +
    # persisted only inside the guarded COMPLETED UPDATE so it never marks the ORM
    # dirty mid-run.
    #
    # Reproducibility guarantee (BOTH paths -- Sec-I2): on re-run the full band
    # {logit_sigma, seed, draws, eval_budget, min_draws} is read back from
    # band here, NOT from live Settings. This means:
    #   - Normal K-draw path: the same seed + sigma reproduce the exact draws.
    #   - Degraded/insufficient-budget path: band_endpoint_draws uses the
    #     PINNED sigma (not live Settings), and the K-degrade gate uses the
    #     PINNED eval_budget + min_draws. Both paths produce identical ranges
    #     even if canonical weights or Settings drift between runs.
    weight_robustness: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    # Multi-currency P3: the org reporting-currency FX rate frozen at run
    # calculation time — {code, usd_rate (str), as_of_date (str), source}.
    # NULL for USD-reporting runs and legacy (pre-P3) runs. Lives in the
    # frozen-inputs cluster (NOT simulation_results) so it is cheap to read on
    # every report render and survives even a FAILED run.
    presentation_fx_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    # Output payload — full MC samples + computed stats per design decision 6.
    # Carries a "schema_version" key (stamped at the run_executor persist
    # site; legacy rows lack it == version 0). Read via
    # services/simulation_payload.results_schema_version; bump policy lives
    # next to SIMULATION_RESULTS_SCHEMA_VERSION in the same module.
    simulation_results: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,  # populated on COMPLETED only
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Issue #437/#438: re-curation/re-sync staleness flag. A COMPLETED run is
    # marked is_stale=True when a library entry it used is re-curated (version
    # bumped) and the deployed control later re-syncs (#438). The run stays
    # COMPLETED + visible; #438 adds a badge so the UI can prompt a re-run.
    is_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0", default=False
    )

    # Lifecycle timestamps
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Heavy per-iteration MC arrays, split into run_samples (1:1) — #294/#297.
    # passive_deletes=True defers to the DB-level ON DELETE CASCADE rather than
    # emitting a separate ORM DELETE; cascade="all, delete-orphan" still purges
    # the child on ORM-driven session.delete(run) when the row is loaded.
    samples: Mapped[RunSamples | None] = relationship(
        "RunSamples",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
