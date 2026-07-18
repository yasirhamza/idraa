"""Scenario CRUD service.

Lifecycle:

- ``create``: validates FK refs and stamps the scenario row. PR pi
  excised the calibration runtime: ``calibration_override_pin``,
  ``overlay_pins``, ``sub_sector_pin``, ``iris_calibration_year``,
  ``mc_iterations``, ``last_simulated_at`` and
  ``last_simulation_inputs_hash`` are gone (F14 dropped both the columns
  and the corresponding stamping defaults). ``_stamp_new_scenario`` now
  only writes the descriptive + IRIS-metadata fields the wizard collects.

- ``update``: descriptive-fields-only mutation. Optimistic-lock on
  ``expected_row_version: int`` (P9 paranoid-review fix; the str
  ``version`` field is the analyst's descriptive label, NOT the lock
  primitive). Emits ``scenario.update`` audit.

- ``delete``: hard delete. Optimistic-lock on ``expected_row_version``.
  Emits ``scenario.delete`` audit BEFORE the row delete so the audit
  row's ``entity_id`` references a row that still exists at flush time.

Audit ``action`` strings follow the project-wide ``<entity>.<verb>``
taxonomy: ``scenario.create`` / ``.update`` / ``.delete``. NEVER bare
verbs — diverges from the legacy ``services/controls.py`` pattern
which predates the preamble fold-in.

``ip_address`` is threaded through every mutation (P10 paranoid-review
fix) so the route layer can pass ``client_ip(request)`` and the audit
row carries the originating IP. Tests pass ``None`` (the default)
where they don't care about IP.

PR pi cleanup notes:
- ``_resolve_scenario_pins`` was deleted alongside the
  ``CalibrationOverride`` model query path; the calibration-override
  runtime was excised in F6/F10.
- ``refresh_calibration`` was deleted: with no pins to refresh, there
  is no diff to compute and no audit event to emit. Routes /
  templates / tests for that path were removed in F12.
- ``ScenarioOverlayTagNotFoundError`` is preserved in the public
  surface but the create path no longer raises it (no overlay
  resolution). ``ScenarioForm.overlay_tags`` was dropped in F14; the
  exception class survives for callers that wish to validate overlay
  tags out-of-band.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import (
    ConflictError,
    IDORError,
    LibraryEntryStatusError,
    NotFoundError,
    RunBusyError,
    ScenarioInUseError,
    ValidationError,
)
from idraa.models.enums import EntityStatus, ScenarioSource
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.schemas.scenario import ScenarioForm
from idraa.services.audit import AuditWriter
from idraa.services.fair_cam_validation import validate_fair_distributions


class ScenarioOverlayTagNotFoundError(ValidationError):
    """A submitted overlay_tag does not match any OverlayDefinition row in this org.

    Inherits from ``ValidationError`` (HTTP 422) per spec §5.4 row-scoped
    validation semantics. Do NOT catch this via ``NotFoundError`` — that
    would route to HTTP 404, which would imply the scenario itself doesn't
    exist when in fact the form's overlay_tags list is the problem.

    PR pi F12: the create path no longer raises this exception (overlay
    resolution was removed alongside the calibration runtime). The class
    is preserved so out-of-band callers / future overlay-validation code
    can reuse the existing typed error.
    """


class ScenarioVersionConflictError(ConflictError):
    """Optimistic-lock failure on update / delete.

    Triggered when ``expected_row_version`` does not match the current
    ``Scenario.row_version`` value, indicating another mutation landed
    between the caller's read and the attempted write. Maps to HTTP 409
    via :class:`idraa.errors.ConflictError`.
    """


class ScenarioService:
    """Scenario CRUD + audit emission.

    Mutations land in the caller's session without committing — the
    route layer (E5/E6) wraps the call in ``async with db.begin()`` and
    commits atomically with any sibling writes. Audit rows are flushed
    in the same session via ``AuditWriter``, so a caller rollback
    discards both halves together.

    Takes ``db`` in ``__init__`` — matches the precedent set by
    ``OverlayService`` (at the same layer in this codebase). Methods
    take only entity-specific kwargs. Repos this service uses are
    constructed per-call with the held ``self._db``.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Primitive helpers
    # ------------------------------------------------------------------

    async def _stamp_new_scenario(
        self,
        *,
        organization_id: uuid.UUID,
        user: User,
        form: ScenarioForm,
        source: ScenarioSource,
        library_pin: dict[str, Any] | None,
        ip_address: str | None = None,
        per_fieldset_pooling_summary: dict[str, Any] | None = None,
    ) -> Scenario:
        """Row-builder primitive — stamps every NOT NULL Scenario field.

        Both ``create()`` and ``create_from_wizard()`` converge here so
        FAIRCAM validation, audit emission, and pin-default writing
        happen exactly once regardless of entry path.

        Order of operations:
        1. IDOR guard: user.organization_id must equal organization_id.
        2. FAIRCAM validate_fair_distributions (raises FAIRCAMValidationError
           on ERROR severity — closes GH #2 in the wizard path too).
        3. If library_pin is non-null, re-fetch the library entry with
           for_update=True to assert published status (closes TOCTOU race
           where librarian deprecates between wizard step-1 and finalize).
        4. Construct Scenario row with the descriptive + FAIR-distribution
           fields the wizard collects. Calibration-runtime columns are gone
           post-PR-pi (no pins, no iris_calibration_year, no mc_iterations,
           no last_simulated_at / last_simulation_inputs_hash).
           industry/revenue_tier ORM columns are gone post-issue-#88 Task 12.
        5. session.add + flush.
        6. AuditWriter.log with [None, value] diff shape for the
           descriptive fields actually populated.
        7. Return scenario.
        """
        # 1. IDOR guard.
        if user.organization_id != organization_id:
            raise IDORError(
                f"user.organization_id={user.organization_id} does not match "
                f"organization_id={organization_id} — cross-org create blocked"
            )

        # 2. FAIRCAM validation before any DB write.
        validate_fair_distributions(
            threat_event_frequency=form.threat_event_frequency,
            vulnerability=form.vulnerability,
            primary_loss=form.primary_loss,
            secondary_loss=form.secondary_loss,
        )

        # 2b. Epic #34 P1a (plan-gate SEC-R2-2, placement per SEC-R3-NTH):
        # create-path status domain. A new scenario may only be created as
        # ACTIVE (default) or DRAFT (pending review) — DEPRECATED/DELETED are
        # lifecycle end-states reached only via their own dedicated paths, not
        # at creation. This chokepoint covers both create() and
        # create_from_wizard() since both converge here.
        if form.status not in (EntityStatus.ACTIVE, EntityStatus.DRAFT):
            raise ValidationError("new scenarios may only be created as active or draft")

        # 3. Library entry status re-validation (TOCTOU guard).
        library_source_provenance: str | None = None
        if library_pin is not None:
            from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo

            entry_id = uuid.UUID(library_pin["entry_id"])
            entry_version: int = library_pin["version"]
            lib_repo = ScenarioLibraryRepo(self._db)
            entry = await lib_repo.get_by_id_version(entry_id, entry_version, for_update=True)
            if entry is None or entry.status != "published":
                status_val = entry.status if entry is not None else "not found"
                raise LibraryEntryStatusError(
                    f"library entry {entry_id} v{entry_version} is no longer "
                    f"published (status={status_val!r}); cannot stamp scenario"
                )
            # Meth-I1: capture the source entry's provenance so an
            # ``imported``-origin scenario stays traceable in the audit log
            # even after the source library entry is deleted (Option B
            # safety — what makes runtime delete-imported epistemically OK).
            library_source_provenance = entry.source

        # 4. (issue #88 Task 12) industry/revenue_tier ORM columns are gone;
        # CalibrationContext is now derived from the live org row at call-time
        # by the run executor — not stamped on the scenario row.

        # 5. Construct row with the descriptive + FAIR-distribution + IRIS
        # metadata fields the wizard collects. The calibration runtime
        # columns are gone post-PR-pi.
        scenario = Scenario(
            organization_id=organization_id,
            name=form.name,
            description=form.description,
            scenario_type=form.scenario_type,
            threat_category=form.threat_category,
            threat_actor_type=form.threat_actor_type,
            attack_vector=form.attack_vector,
            asset_class=form.asset_class,
            effect=form.effect,
            threat_event_frequency=form.threat_event_frequency,
            vulnerability=form.vulnerability,
            primary_loss=form.primary_loss,
            secondary_loss=form.secondary_loss,
            library_pin=library_pin,
            source=source,
            status=form.status,
            version=form.version,
            row_version=1,
            created_by=user.id,
        )

        # 6. Persist.
        self._db.add(scenario)
        await self._db.flush()

        # 7. Audit with full [None, value] diff shape for the descriptive
        # fields actually populated. industry/revenue_tier columns are gone
        # (issue #88 Task 12); CalibrationContext is now derived from the org at
        # run time and is not a scenario attribute.
        changes: dict[str, Any] = {
            "name": [None, scenario.name],
            "library_pin": [None, scenario.library_pin],
            "asset_class": [None, getattr(scenario.asset_class, "value", scenario.asset_class)],
            "effect": [None, getattr(scenario.effect, "value", scenario.effect)],
            "source": [None, scenario.source.value],
            "status": [None, scenario.status.value],
            "version": [None, scenario.version],
            "row_version": [None, scenario.row_version],
        }
        # Meth-I1: record the source library entry's provenance ('seed' /
        # 'imported') so the scenario's origin survives entry deletion.
        if library_source_provenance is not None:
            changes["library_source_provenance"] = [None, library_source_provenance]
        # T5 (wizard step-3 evaluator-style finalize): pooled-fit summary lands
        # in the create-audit row's changes dict for forensic reproducibility.
        # None on the expert-form path and on the wizard-with-no-SME-pooling
        # transition path; populated when wizard_finalize.process_sme_estimates
        # ran upstream.
        if per_fieldset_pooling_summary is not None:
            changes["per_fieldset_pooling_summary"] = [None, per_fieldset_pooling_summary]

        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=scenario.id,
            action="scenario.create",
            changes=changes,
            user_id=user.id,
            ip_address=ip_address,
        )

        # 8. Return.
        return scenario

    # ------------------------------------------------------------------
    # Public create paths
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        form: ScenarioForm,
        current_user: User,
        ip_address: str | None = None,
    ) -> Scenario:
        """Create a scenario via the expert form.

        - If ``form.library_entry_id`` is set, resolves the library entry
          via :class:`~idraa.services.scenario_library.ScenarioLibraryService`
          to obtain a ``library_pin`` dict and flips ``source`` to
          ``LIBRARY_DERIVED``. Otherwise ``library_pin=None`` and
          ``source`` comes from the form.
        - Validates FAIR distributions via fair_cam before any DB write
          (closes GH #2).
        - Writes a ``scenario.create`` audit row in the same session.

        PR pi: pin auto-resolution removed; the calibration-override
        runtime was excised. ``ScenarioForm`` no longer carries
        ``overlay_tags`` (dropped with the rest of the runtime in F14).
        """
        library_pin: dict[str, Any] | None = None
        source: ScenarioSource = form.source

        if form.library_entry_id is not None:
            from idraa.services.scenario_library import ScenarioLibraryService

            resolved = await ScenarioLibraryService(self._db).resolve_for_clone(
                entry_id=form.library_entry_id,
                organization_id=organization_id,
            )
            library_pin = resolved.pin
            source = ScenarioSource.LIBRARY_DERIVED
            # Org revenue-tier loss calibration was removed 2026-07-07 (the IRIS
            # sector envelope IS the calibration). No calibration metadata is
            # computed or audited.

        return await self._stamp_new_scenario(
            organization_id=organization_id,
            user=current_user,
            form=form,
            source=source,
            library_pin=library_pin,
            ip_address=ip_address,
        )

    async def create_from_wizard(
        self,
        *,
        organization_id: uuid.UUID,
        form: ScenarioForm,
        library_pin: dict[str, Any] | None,
        current_user: User | None = None,
        actor: User | None = None,
        ip_address: str | None = None,
        per_fieldset_pooling_summary: dict[str, Any] | None = None,
    ) -> Scenario:
        """Wizard-finalize delegate — called by the wizard route at step 2.

        Source is ``LIBRARY_DERIVED`` when ``library_pin`` is set;
        ``EXPERT_JUDGMENT`` otherwise (blank-slate wizard path).

        ``per_fieldset_pooling_summary`` (T5 / Arch-21 R2): when non-None,
        a summary of the pooled-fit metadata for each fieldset (n_smes +
        pooled distribution params). Threaded through to the
        ``scenario.create`` audit row's ``changes`` dict for forensic
        reproducibility of the evaluator-style finalize path. Defaults to
        ``None`` so legacy callers (existing create_from_wizard sites, all
        of `tests/unit/test_create_from_wizard.py`) keep working unchanged.

        ``actor`` is accepted as an alias for ``current_user`` per Arch-17
        PR2 -- the wizard_finalize route handler in T11 idiomatically uses
        ``actor=user``. Exactly one of the two MUST be supplied (a
        ``ValueError`` is raised when both are None).

        Delegates to ``_stamp_new_scenario`` — the same row-builder used
        by ``create()``. Both expert-form and wizard paths converge on
        the same primitive (spec §7.3).
        """
        user = current_user if current_user is not None else actor
        if user is None:
            raise ValueError("create_from_wizard requires `current_user` (or its `actor` alias)")
        source = (
            ScenarioSource.LIBRARY_DERIVED
            if library_pin is not None
            else ScenarioSource.EXPERT_JUDGMENT
        )
        return await self._stamp_new_scenario(
            organization_id=organization_id,
            user=user,
            form=form,
            source=source,
            library_pin=library_pin,
            ip_address=ip_address,
            per_fieldset_pooling_summary=per_fieldset_pooling_summary,
        )

    async def update(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        form: ScenarioForm,
        expected_row_version: int,
        current_user: User,
        ip_address: str | None = None,
    ) -> Scenario:
        """Update descriptive fields.

        - Raises :class:`idraa.errors.NotFoundError` if no scenario
          matches (org_id, scenario_id) — IDOR-safe lookup via the repo.
        - Raises :class:`ScenarioVersionConflictError` on
          ``expected_row_version`` mismatch (P9 optimistic lock; the
          row_version int is the lock primitive, not the descriptive
          ``version`` str).
        - Bumps ``scenario.row_version`` by 1 on success.
        - Emits a ``scenario.update`` audit row only when at least one
          tracked field actually changed (no-op edits are silent).

        PR pi: the calibration_override_pin / overlay_pins re-resolve
        path was removed; the wizard no longer collects
        ``iris_calibration_year`` and the column was dropped in F14.
        """
        repo = ScenarioRepo(self._db)
        scenario = await repo.get_for_org(
            organization_id=organization_id,
            scenario_id=scenario_id,
            lock=True,
        )
        if scenario is None:
            raise NotFoundError(f"scenario_id={scenario_id} not found")
        if scenario.row_version != expected_row_version:
            raise ScenarioVersionConflictError(
                f"scenario row_version conflict: expected_row_version="
                f"{expected_row_version} but actual row_version={scenario.row_version}; "
                f"another user updated this scenario — reload and retry"
            )

        # Epic #34 P1a (plan-gate B-1): status transitions go ONLY through
        # the audited promote flow — the edit path must not be a second,
        # unguarded promote/demote surface. This guard MUST be the first
        # statement after the row_version check and BEFORE any before-dict
        # capture / FAIRCAM validation / vuln_framing flip / field
        # assignment: the route catches ValidationError and RETURNS a 422
        # re-render (a successful handler exit), and get_db auto-commits
        # pending dirty state on any successful exit (Sec2-I2) — a later
        # guard would turn "status rejected" into a silently-committed,
        # unaudited, non-row-version-bumped edit of every other field.
        if form.status != scenario.status:
            raise ValidationError(
                "status cannot be changed here — use Promote on the scenario page"
            )

        # Capture before-state for audit. Enum-valued fields are
        # serialised to .value so the JSON audit payload stays plain.
        # industry/revenue_tier columns are gone (issue #88 Task 12).
        before: dict[str, Any] = {
            "name": scenario.name,
            "description": scenario.description,
            "scenario_type": scenario.scenario_type.value,
            "threat_category": getattr(scenario.threat_category, "value", scenario.threat_category),
            "threat_actor_type": getattr(
                scenario.threat_actor_type, "value", scenario.threat_actor_type
            ),
            "attack_vector": scenario.attack_vector,
            "asset_class": getattr(scenario.asset_class, "value", scenario.asset_class),
            "effect": getattr(scenario.effect, "value", scenario.effect),
            "threat_event_frequency": scenario.threat_event_frequency,
            "vulnerability": scenario.vulnerability,
            "primary_loss": scenario.primary_loss,
            "secondary_loss": scenario.secondary_loss,
            "status": scenario.status.value,
            "version": scenario.version,
        }

        # Sec-1: FAIRCAM validation before any FAIR-distribution write —
        # the create / _stamp_new_scenario paths already gate this, but the
        # edit path previously bypassed it, letting an edit store non-finite
        # PERT (inf) or an unbounded lognormal (sigma>10) that create/import
        # reject (corruption / OOM vectors). Match _stamp_new_scenario exactly.
        validate_fair_distributions(
            threat_event_frequency=form.threat_event_frequency,
            vulnerability=form.vulnerability,
            primary_loss=form.primary_loss,
            secondary_loss=form.secondary_loss,
        )

        # Audit-F2: an edit that CHANGES the vulnerability values was made
        # under the post-#339 inherent framing — flip the provenance stamp.
        # Compare the (low, mode, high) NUMERIC TRIPLE ONLY (plan-gate SC-B1):
        # wizard-created rows carry a distribution_fit_metadata sidecar and no
        # "distribution" key, so dict equality would false-positive-flip on
        # every edit that merely round-trips the values unchanged.
        def _vuln_triple(d: dict[str, Any] | None) -> tuple[Any, Any, Any] | None:
            if not isinstance(d, dict):
                return None
            return (d.get("low"), d.get("mode"), d.get("high"))

        if _vuln_triple(scenario.vulnerability) != _vuln_triple(form.vulnerability):
            scenario.vuln_framing = "inherent"

        # Apply descriptive-only changes (pin columns untouched per F12).
        scenario.name = form.name
        scenario.description = form.description
        scenario.scenario_type = form.scenario_type
        scenario.threat_category = form.threat_category  # type: ignore[assignment]
        scenario.threat_actor_type = form.threat_actor_type  # type: ignore[assignment]
        scenario.attack_vector = form.attack_vector
        scenario.asset_class = form.asset_class  # type: ignore[assignment]
        scenario.effect = form.effect  # type: ignore[assignment]
        scenario.threat_event_frequency = form.threat_event_frequency
        scenario.vulnerability = form.vulnerability
        scenario.primary_loss = form.primary_loss
        scenario.secondary_loss = form.secondary_loss
        scenario.status = form.status
        scenario.version = form.version

        def _val(k: str) -> Any:
            v = getattr(scenario, k)
            if k in (
                "scenario_type",
                "status",
                "threat_category",
                "threat_actor_type",
                "asset_class",
                "effect",
            ):
                return getattr(v, "value", v)
            return v

        after = {k: _val(k) for k in before}
        changes: dict[str, list[Any]] = {
            k: [before[k], after[k]] for k in before if before[k] != after[k]
        }

        if not changes:
            # No-op edit: don't bump row_version, don't emit audit.
            return scenario

        prev_row_version = scenario.row_version
        scenario.row_version = prev_row_version + 1
        changes["row_version"] = [prev_row_version, scenario.row_version]

        await self._db.flush()

        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=scenario.id,
            action="scenario.update",
            changes=changes,
            user_id=current_user.id,
            ip_address=ip_address,
        )
        return scenario

    async def confirm_vuln_framing(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        current_user: User,
        ip_address: str | None = None,
    ) -> Scenario:
        """Audit-F2: analyst affirms the stored vulnerability values already
        represent INHERENT (control-naive) susceptibility — flip the
        ``legacy_residual`` provenance stamp to ``inherent``.

        Intentionally takes NO ``expected_row_version`` (plan-gate
        Sec-F2-NTH1): the flip is idempotent (re-confirming writes the same
        value), and its row_version bump is precisely the mechanism by which
        a concurrently-open edit form 409s into a reload that shows the
        banner resolved. No confirm/update interleaving corrupts data.

        Raises NotFoundError on cross-org / missing id (route maps to 404,
        NOT 403 — no existence oracle, per the project IDOR convention).
        """
        repo = ScenarioRepo(self._db)
        scenario = await repo.get_for_org(
            organization_id=organization_id,
            scenario_id=scenario_id,
            lock=True,
        )
        if scenario is None:
            raise NotFoundError(f"scenario_id={scenario_id} not found")
        if scenario.vuln_framing == "inherent":
            return scenario  # idempotent no-op: no bump, no audit
        prev_row_version = scenario.row_version
        scenario.vuln_framing = "inherent"
        scenario.row_version = prev_row_version + 1
        await self._db.flush()
        # Epic #34 P1b Task 5b (spec §3 Meth-I1): a converted register row's
        # vulnerability is a fixed neutral pass-through — the genuine
        # epistemic act being confirmed here is acceptance of the FREQUENCY
        # baseline (the register-likelihood-derived LEF band), not a review
        # of stored vulnerability values. The mechanics (flag, confirm flip)
        # are reused verbatim from the F2 flow; only the recorded audit
        # action differs, so a converted row's confirm history is never
        # misread as "vulnerability values were reviewed". Non-converted
        # scenarios keep the original action string unchanged.
        action = (
            "scenario.confirm_frequency_baseline"
            if scenario.source == ScenarioSource.QUALITATIVE_REGISTER_IMPORT
            else "scenario.confirm_vuln_framing"
        )
        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=scenario.id,
            action=action,
            changes={
                "vuln_framing": ["legacy_residual", "inherent"],
                "row_version": [prev_row_version, scenario.row_version],
            },
            user_id=current_user.id,
            ip_address=ip_address,
        )
        return scenario

    async def promote(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        current_user: User,
        ip_address: str | None = None,
    ) -> Scenario:
        """DRAFT → ACTIVE after review (epic #34 P1a). Idempotent on ACTIVE.

        Refuses while vuln_framing == "legacy_residual": the reviewer must
        confirm inherent framing first (spec §4 — P1a implements the strict
        subset; the acknowledge-in-dialog path arrives with P1c).
        """
        repo = ScenarioRepo(self._db)
        scenario = await repo.get_for_org(
            organization_id=organization_id,
            scenario_id=scenario_id,
            lock=True,
        )
        if scenario is None:
            raise NotFoundError(f"scenario {scenario_id} not found")
        if scenario.status == EntityStatus.ACTIVE:
            return scenario
        if scenario.status != EntityStatus.DRAFT:
            raise ValidationError(
                f"only draft scenarios can be promoted (status={scenario.status.value})"
            )
        if scenario.vuln_framing == "legacy_residual":
            # Epic #34 P1c Task 8: a converted register row's confirm gate is
            # the FREQUENCY baseline (spec §3 Meth-I1) — vuln stays neutral —
            # so the refusal message must not tell the reviewer to confirm
            # "vulnerability framing" when there is no vulnerability review
            # to do. Non-converted scenarios keep the original string.
            message = (
                "confirm the frequency baseline before promoting — see the banner on this scenario"
                if scenario.source == ScenarioSource.QUALITATIVE_REGISTER_IMPORT
                else "confirm vulnerability framing before promoting — see the "
                "banner on this scenario"
            )
            raise ValidationError(message)
        prev_row_version = scenario.row_version
        scenario.status = EntityStatus.ACTIVE
        scenario.row_version = prev_row_version + 1
        await self._db.flush()
        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=scenario.id,
            action="scenario.promote",
            changes={
                "status": ["draft", "active"],
                "row_version": [prev_row_version, scenario.row_version],
            },
            user_id=current_user.id,
            ip_address=ip_address,
        )
        return scenario

    async def delete(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        expected_row_version: int,
        current_user: User,
        cascade_runs: bool = False,
        ip_address: str | None = None,
    ) -> None:
        """Hard delete a scenario.

        - Raises :class:`idraa.errors.NotFoundError` if no scenario
          matches (org_id, scenario_id).
        - Raises :class:`ScenarioVersionConflictError` on
          ``expected_row_version`` mismatch (P9).
        - Audit row is written BEFORE the delete so ``entity_id``
          references a row that still exists at flush time.

        Cascade-on-delete (RESTRICT-FK fix): the SINGLE runs that reference
        this scenario via ``risk_analysis_run.scenario_id`` carry a
        ``ondelete="RESTRICT"`` FK and would raise IntegrityError → HTTP 500
        on an unguarded delete. To avoid that:

        - If the scenario has >=1 SINGLE run and ``cascade_runs`` is False,
          raise :class:`idraa.errors.ScenarioInUseError` (carries
          ``run_count``) WITHOUT deleting anything — the route renders a
          confirmation step.
        - If ``cascade_runs`` is True, cascade-delete those SINGLE runs in
          the SAME transaction (one audit row per run, BEFORE each delete),
          then delete the scenario. ``run_samples`` auto-cascades via its
          own ``ON DELETE CASCADE`` FK.

        AGGREGATE runs (``scenario_id IS NULL``, referencing scenarios via
        the ``aggregate_scenario_ids`` JSON list — NOT a FK) are NOT in
        scope: they don't block the delete and render from their own saved
        snapshot, so they survive a deleted member.

        In-flight guard: if any SINGLE run is RUNNING / QUEUED, raise
        :class:`idraa.errors.RunBusyError` (checked across ALL runs
        BEFORE deleting any, so we never partially delete then fail).

        Order: resolve+lock scenario → row_version check → resolve runs →
        (block or cascade-delete runs) → delete scenario → flush.
        """
        repo = ScenarioRepo(self._db)
        scenario = await repo.get_for_org(
            organization_id=organization_id,
            scenario_id=scenario_id,
            lock=True,
        )
        if scenario is None:
            raise NotFoundError(f"scenario_id={scenario_id} not found")
        if scenario.row_version != expected_row_version:
            raise ScenarioVersionConflictError(
                f"scenario row_version conflict: expected_row_version="
                f"{expected_row_version} but actual row_version={scenario.row_version}; "
                f"another user updated this scenario — reload and retry"
            )

        # Resolve the SINGLE runs that hold the blocking RESTRICT FK. Only
        # rows WHERE scenario_id == this scenario block the delete; AGGREGATE
        # runs (scenario_id IS NULL) reference scenarios via a JSON list, not
        # an FK, so they are excluded by this WHERE clause.
        runs = list(
            (
                await self._db.execute(
                    select(RiskAnalysisRun)
                    .where(RiskAnalysisRun.organization_id == organization_id)
                    .where(RiskAnalysisRun.scenario_id == scenario_id)
                )
            )
            .scalars()
            .all()
        )

        if runs and not cascade_runs:
            # Has runs but no confirmation — signal the route to render the
            # cascade-confirmation step. Delete nothing.
            raise ScenarioInUseError(
                f"scenario_id={scenario_id} has {len(runs)} analysis run(s); "
                f"deleting it will also delete those runs — confirm to proceed",
                run_count=len(runs),
            )

        if runs:  # cascade_runs is True here
            # In-flight guard FIRST across ALL runs — never partially delete
            # and then fail on a later in-flight row.
            for run in runs:
                if run.status in (RunStatus.RUNNING, RunStatus.QUEUED):
                    raise RunBusyError(
                        f"cannot delete: scenario has an in-flight run "
                        f"(id={run.id}, status={run.status.value}); cancel it first"
                    )
            audit = AuditWriter(self._db)
            for run in runs:
                # Audit BEFORE delete so entity_id references an extant row at
                # flush time (mirrors RunService.delete_run ordering). Inline
                # in THIS transaction — we do NOT call RunService.delete_run,
                # which commits per-run; we want one atomic transaction.
                await audit.log(
                    organization_id=organization_id,
                    user_id=current_user.id,
                    action="risk_analysis_run.delete",
                    entity_type="risk_analysis_run",
                    entity_id=run.id,
                    changes={"status": [run.status.value, "deleted (scenario cascade)"]},
                    ip_address=ip_address,
                )
                await self._db.delete(run)

        # Audit BEFORE delete so the row still exists for entity_id reference.
        # industry/revenue_tier columns are gone (issue #88 Task 12).
        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=scenario.id,
            action="scenario.delete",
            changes={
                "name": [scenario.name, None],
                "row_version": [scenario.row_version, None],
            },
            user_id=current_user.id,
            ip_address=ip_address,
        )

        await self._db.delete(scenario)
        await self._db.flush()
