"""Domain-error hierarchy for Idraa v3.

Class hierarchy maps cleanly to HTTP semantics so the route layer can
catch by base class:

    IdraaError
        ConflictError       → HTTP 409 (state version mismatch, etc.)
        NotFoundError       → HTTP 404 (lookup failed; not visible to caller)
        ValidationError     → HTTP 422 (semantically invalid input)

Domain modules subclass these to carry their own messages while keeping
the HTTP-mapping intent explicit.
"""

from __future__ import annotations


class IdraaError(Exception):
    """Root of the Idraa v3 domain-error hierarchy."""


class ConflictError(IdraaError):
    """A state mismatch the caller can resolve by reloading + retrying.

    Maps to HTTP 409.
    """


class NotFoundError(IdraaError):
    """A lookup that should have succeeded but didn't.

    Maps to HTTP 404. Raise this instead of LookupError when the caller is
    a route handler that needs to render a 404 page.
    """


class ValidationError(IdraaError):
    """Domain validation failure (HTTP 422 — semantically invalid input).

    Use for request-shape problems that survive Pydantic schema validation
    but fail business-logic checks at the service layer (e.g., a referenced
    tag doesn't exist for this organization). The route layer maps to
    HTTP 422 by catching this base class.

    Distinct from ``NotFoundError`` (HTTP 404 — entity by ID was not
    addressable to this caller). Don't conflate the two: 'unknown overlay
    tag in this org' is a *form-validation* failure, not a missing-row
    addressing failure.
    """


class ScenarioNotFoundError(NotFoundError):
    """Scenario row not found in the org's scope (HTTP 404)."""


class RunNotFoundError(NotFoundError):
    """Run row not found in the org's scope (HTTP 404)."""


class RunValidationError(ValidationError):
    """Run-trigger inputs (mc_iterations, etc.) failed validation (HTTP 422)."""


class RunBusyError(ConflictError):
    """Attempt to delete a run that is still in-flight (QUEUED / RUNNING).

    Maps to HTTP 409 (ConflictError). The caller can resolve by cancelling
    the run first, or by re-submitting with ``force=True`` to delete the
    in-flight row anyway (#297)."""


class ScenarioInUseError(ConflictError):
    """A scenario delete was attempted while the scenario still has >=1
    SINGLE analysis run referencing it via the RESTRICT FK
    (``risk_analysis_run.scenario_id``).

    Carries ``run_count`` so the route layer can render a confirmation
    step ("This scenario has N analysis run(s); deleting it will also
    delete those runs") before re-POSTing with ``confirm_cascade=1`` to
    cascade-delete the runs. Maps to HTTP 200 (confirmation page) at the
    route layer — NOT a hard error — but subclasses ``ConflictError`` so
    any caller that does not special-case it still gets sane 409 semantics
    rather than a 500.

    AGGREGATE runs (``scenario_id IS NULL``, referencing scenarios via the
    ``aggregate_scenario_ids`` JSON list, NOT a FK) do NOT count toward
    ``run_count`` — they don't block the delete and render from their own
    saved snapshot, so they survive a deleted member."""

    def __init__(self, message: str, *, run_count: int) -> None:
        super().__init__(message)
        self.run_count = run_count


class UserDeleteError(ConflictError):
    """A user hard-delete is refused for a state reason the caller cannot
    override by retrying (self-delete, last-admin). Maps to HTTP 409 via the
    ``ConflictError`` base. Distinct from :class:`UserHasHistoryError`, which
    is the specific "authored entities -> deactivate instead" case (#296)."""


class UserHasHistoryError(UserDeleteError):
    """Attempt to hard-delete a user who authored business entities (runs,
    scenarios, or controls). The admin must deactivate (``is_active=False``)
    instead so the authorship attribution is preserved. Maps to HTTP 409
    (subclass of :class:`UserDeleteError`/``ConflictError``) (#296)."""


class ControlNotFoundForRunError(ValidationError):
    """control_ids override references an ID not in the org's Control inventory.

    Treated as a form-validation failure (HTTP 422), not a 404 — the route
    re-renders the trigger form with an error.
    """


class RetentionConfigError(IdraaError):
    """Run-retention policy settings are internally inconsistent (#297).

    Raised by ``Settings.validate_retention()`` when
    ``retention_run_delete_days`` is enabled but does not strictly exceed
    ``retention_sample_purge_days`` — a configuration where the auto-delete
    window would fire on (or before) the sample-purge window, making the
    purge phase pointless. Surfaced at app startup as a fail-fast boot
    error (NOT swallowed by the reaper's broad except), so a misconfigured
    deploy crashes loudly rather than silently mis-retaining data."""


class IDORError(IdraaError):
    """Cross-org access attempt — caller's organization_id does not match
    the resource's organization_id. Maps to HTTP 404 at the route layer
    (NOT 403; 403 leaks resource existence — same defensive posture as
    routes/calibration_overrides.py B9/B10 fix)."""


class LibraryEntryNotFoundError(NotFoundError):
    """Library entry not found by id+version (or slug+version)."""


class LibraryEntryVersionNotFoundError(NotFoundError):
    """Library entry exists by id but the requested version does not exist
    (e.g., requesting a version higher than current latest)."""


class LibraryOverrideAlreadyExistsError(ConflictError):
    """Attempt to create an override when (org, entry) already has one;
    use update instead to bump version."""


class LibraryOverrideVersionConflictError(ConflictError):
    """Optimistic-lock failure on update_override.

    Mirrors :class:`ScenarioVersionConflictError` and
    :class:`CalibrationOverrideVersionConflictError` shape so the route
    layer's 409 mapping pattern is consistent across all three lock-
    conflict cases. Replaces the original plan's bare ``OptimisticLockError``
    reference (no such class on main per paranoid review)."""


class QualitativeBandVersionConflictError(ConflictError):
    """Optimistic-lock failure on ``QualitativeBandService.update_org_band``
    (epic #34 P1b).

    Mirrors :class:`LibraryOverrideVersionConflictError`'s shape so the route
    layer's 409 mapping pattern stays consistent across all lock-conflict
    cases. Distinct lock field, by design: unlike ``ScenarioLibraryOverride``
    (whose service-layer check compares the descriptive ``version`` counter),
    ``QualitativeMappingOrgBand`` checks against ``row_version`` — the
    dedicated optimistic-lock primitive — per the P1b plan's
    ``expected_row_version`` parameter name."""


class LibraryEntryStatusError(ValidationError):
    """Attempt to clone from a draft or deprecated entry."""


class LibraryEntryDeleteRefusedError(IdraaError):
    """Runtime delete of a library entry was refused by a safety guard.

    Two refusal cases (P3 Task 6 delete-imported):
    - Arch-I2: the entry is ``source != "imported"`` (seed entries are
      code-managed and never deletable via the runtime path).
    - Arch-I1: a ``ScenarioLibraryOverride`` (live OR tombstoned) still
      holds the composite FK to the entry; deleting would raise
      IntegrityError, so the override must be removed first.

    Maps to HTTP 403 at the route layer (a deliberate, permission-like
    refusal — distinct from IDORError's 404 existence-hiding posture; the
    admin is allowed to see the entry, just not delete it in this state)."""


class FAIRCAMValidationError(ValidationError):
    """fair_cam FAIRCAMValidator returned ERROR severity. Routes catch and
    map to HTTP 422 via ``ValidationError`` base.

    Carries the structured ``errors`` list so templates can render per-
    field messages (each entry is ``(label: str, ValidationResult)``)."""

    def __init__(self, message: str, errors: list[tuple[str, object]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class StepUpRequired(IdraaError):  # noqa: N818 (name is a P2 plan-gate-locked interface; later tasks import it verbatim)
    """Sensitive action attempted with a stale session (step-up / sudo mode).

    Raised by routes/deps.py::require_recent_auth; translated by
    app.py::_step_up_handler into the /auth/step-up challenge. Carries the
    URL to return to after re-verification.
    """

    def __init__(self, next_url: str) -> None:
        super().__init__(next_url)
        self.next_url = next_url
