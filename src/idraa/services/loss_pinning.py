"""Analyst loss-quantile pin/unpin (sigma-recal PR3 Task 3, D20/D21).

An ``analyst_pin`` is an explicit, audited override: the analyst types a
p50/p95 dollar pair for a single-lognormal ``primary_loss`` /
``secondary_loss`` field, this module fits it through fair_cam's own
``lognormal_from_quantiles`` (fair_cam stays the sole source of FAIR math --
CLAUDE.md "Never re-derive FAIR calculations in the app layer"), and stamps
the result ``sigma_recalibration.source == "analyst_pin"``. That stamp is
the SAME field both prior migrations (``c4e4d441087c`` sigma-recalibration
sweep, ``b3f8a2d94c1e`` D12 TEF/PERT collapse) already treat as a skip-guard
-- a pinned field is permanently exempt from any future blind-replay sweep
(see ``test_loss_pinning.py::test_pinned_dist_is_untouched_by_both_
migration_helpers``).

Two write paths, both optimistic-locked on ``Scenario.row_version``
(mirrors ``ScenarioService.update`` exactly) and both flush-only -- commit
is the caller's (route/``get_db``) responsibility, same as every other
service in this codebase (Arch-N5: a mid-service commit would break
rollback-on-conflict).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fair_cam.quantile_pooling import lognormal_from_quantiles
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.errors import (
    LibraryEntryNotFoundError,
    LibraryEntryStatusError,
    NotFoundError,
    ValidationError,
)
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.services.audit import AuditWriter
from idraa.services.fair_cam_validation import validate_fair_distributions
from idraa.services.library_calibration import library_calibrated_pre_fill
from idraa.services.loss_capacity import capacity_max_for_org
from idraa.services.scenario_library import ResolvedLibraryEntry, ScenarioLibraryService
from idraa.services.scenarios import ScenarioVersionConflictError

# Copied VERBATIM from alembic/versions/b3f8a2d94c1e_d12_tef_pert_collapse.py's
# MODULE-LEVEL ``_FIT_RECORD_KEYS`` constant. The byte-identical copy also
# lives inside c4e4d441087c_sigma_recalibration.py's ``_recalibrate_dist``
# function body (a LOCAL variable there, not a module attribute, so it is
# not importable for a direct equality assert) -- both migrations carry the
# same 16-key tuple in the same order; b3f8a2d94c1e is the one with a
# module-level name to pin against. tests/integration/test_loss_pinning.py's
# ``test_pinned_dist_is_untouched_by_both_migration_helpers`` loads that
# migration module via ``importlib.util.spec_from_file_location`` (the
# project's spec_from_file_location idiom -- tests/migrations/
# test_sigma_recalibration_migration.py:99-105) and asserts
# ``FIT_RECORD_KEYS == mod._FIT_RECORD_KEYS`` so the two tuples can never
# silently drift (N-4). Order matters: it drives ``superseded`` iteration.
FIT_RECORD_KEYS: tuple[str, ...] = (
    "pooled_meanlog",
    "pooled_sdlog",
    "component_meanlogs",
    "component_sdlogs",
    "pooling_method",
    "pooled_min_support",
    "pooled_max_support",
    "q_low_quantile",
    "q_high_quantile",
    "n_smes",
    "sme_ids",
    "weights",
    "source",
    "fitter",
    "fitted_at",
    "schema_version",
)


class LossPinError(ValidationError):
    """Analyst-pin/unpin request failed domain validation (HTTP 422).

    Raised for: non-lognormal field kind, non-finite/non-positive/inverted
    p50/p95, or an unpin attempted on a field that is not currently
    ``analyst_pin``-stamped. fair_cam's own ``FAIRCAMValidationError``
    (raised directly by ``validate_fair_distributions`` -- e.g. the D19
    capacity floor) is NOT re-wrapped into this class: both subclass
    :class:`idraa.errors.ValidationError`, so route-layer callers catch the
    base class uniformly and dispatch on ``isinstance`` only when they need
    the D19-specific operator copy (mirrors ``update_scenario``'s existing
    ``except ValidationError`` block in routes/scenarios.py).
    """


def _field_dist(scenario: Scenario, field: Literal["primary", "secondary"]) -> dict[str, Any]:
    """The scenario's CURRENT dict for ``field`` -- ``{}`` when secondary is
    unset (``None``/absent), never a bare ``None`` (every subsequent
    ``.get`` call assumes a dict)."""
    raw = scenario.primary_loss if field == "primary" else scenario.secondary_loss
    return dict(raw) if isinstance(raw, dict) else {}


def _check_lock(scenario: Scenario, expected_row_version: int) -> None:
    if scenario.row_version != expected_row_version:
        raise ScenarioVersionConflictError(
            f"scenario row_version conflict: expected_row_version="
            f"{expected_row_version} but actual row_version={scenario.row_version}; "
            f"another user updated this scenario — reload and retry"
        )


async def pin_loss(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    scenario_id: uuid.UUID,
    field: Literal["primary", "secondary"],
    p50: float,
    p95: float,
    expected_row_version: int,
    actor: User,
    ip_address: str | None = None,
) -> Scenario:
    """Pin ``field`` (``"primary"``/``"secondary"``) to the lognormal fit of
    the given (p50, p95) dollar pair.

    Raises :class:`idraa.errors.NotFoundError` (org-scoped lookup miss),
    :class:`idraa.services.scenarios.ScenarioVersionConflictError`
    (optimistic-lock mismatch, a :class:`idraa.errors.ConflictError`),
    :class:`LossPinError` (non-lognormal field / bad quantiles), or
    fair_cam's :class:`idraa.errors.FAIRCAMValidationError` (D19 capacity
    floor / other FAIR-CAM validation) on failure. Flushes on success —
    commit is the caller's responsibility (``AuditWriter.log``'s own
    docstring; a mid-service commit would break rollback-on-conflict).
    """
    repo = ScenarioRepo(db)
    scenario = await repo.get_for_org(
        organization_id=organization_id, scenario_id=scenario_id, lock=True
    )
    if scenario is None:
        raise NotFoundError(f"scenario_id={scenario_id} not found")
    _check_lock(scenario, expected_row_version)

    dist = _field_dist(scenario, field)
    kind = str(dist.get("distribution", "")).lower()
    if kind == "lognormal_mixture":
        # #27 precedent (mixture_replace_warning): a mixture is a pooled
        # multi-expert estimate — pinning a single p50/p95 pair would
        # silently discard the pooled components exactly like an expert-
        # form resave does. Point at the sanctioned lossy-replace path
        # (Re-estimate) instead of accepting a pin here.
        raise LossPinError(
            "This field is a pooled multi-expert mixture — pins apply to "
            "single-lognormal loss fields only. Use Re-estimate to "
            "re-elicit from experts instead."
        )
    if kind != "lognormal":
        raise LossPinError("Pins apply to single-lognormal loss fields only.")
    # Boundary gate BEFORE the fit (Sec-I2/N-M5): lognormal_from_quantiles
    # RAISES ValueError on low<=0 / high<=0 / high<low, which would 500
    # where the tests demand 422. Route-layer float() parsing already
    # rejected non-parseable strings; this gates the PARSED values.
    if not (math.isfinite(p50) and math.isfinite(p95)) or p50 <= 0 or p95 <= p50:
        raise LossPinError("Pin needs finite dollar quantiles with p95 > p50 > 0.")
    try:
        # D2 median-anchor semantics: q_low=0.5 makes ``mean`` ANALYTICALLY
        # ln(p50) (norm.ppf(0.5) == 0.0 exactly) — float association can
        # still leave 1-ULP cases, so callers compare with rel=1e-12, never
        # ``==``. fair_cam stays the only source of FAIR math; no
        # hand-derived fit here. Return keys machine-verified: {mean, sigma}.
        fit = lognormal_from_quantiles(p50, p95, q_low=0.5, q_high=0.95)
    except ValueError as exc:  # belt: any future fit-domain change stays a 422
        raise LossPinError(str(exc)) from exc
    mu, sigma = fit["mean"], fit["sigma"]

    meta = dict(dist.get("distribution_fit_metadata") or {})
    prior_stamp = meta.get("sigma_recalibration") or {}
    # Nesting rule (N-9): pop top-level fit keys ALWAYS; set superseded_fit
    # only if absent (first supersession wins — no current writer produces
    # both top-level fit keys AND superseded_fit simultaneously). Popped
    # keys are never lost either way: the full prior dist dict rides in the
    # audit row's changes.
    #
    # T3.a NTH N-3: this is ``setdefault`` (first-wins, never overwrites an
    # existing ``superseded_fit``), while both migrations
    # (c4e4d441087c/b3f8a2d94c1e) write ``meta["superseded_fit"] = superseded``
    # unconditionally (last-wins). The divergence is safe because it is
    # structurally unreachable in practice: a SECOND pin only ever follows a
    # first pin/unpin/migration sweep, and none of those writers ever
    # re-populate top-level FIT_RECORD_KEYS on an already-superseded dist —
    # ``superseded`` is empty on every re-pin, so this line is a no-op guard
    # against a case that cannot recur, not a live behavioral choice.
    superseded = {k: meta.pop(k) for k in FIT_RECORD_KEYS if k in meta}
    if superseded:
        meta.setdefault("superseded_fit", superseded)
    meta["sigma_recalibration"] = {
        "source": "analyst_pin",
        "pinned_at": datetime.now(UTC).isoformat(),
        "actor_id": str(actor.id),
        "input": {"p50": p50, "p95": p95},
        "prior_sigma": dist.get("sigma"),
        "prior_source": prior_stamp.get("source"),
    }
    new_dist: dict[str, Any] = {
        "distribution": "lognormal",
        "mean": mu,
        "sigma": sigma,
        "distribution_fit_metadata": meta,
    }
    existing_max = dist.get("max")
    organization = await db.get(Organization, organization_id)
    minted = capacity_max_for_org(
        organization.annual_revenue if organization is not None else None,
        get_settings().capacity_k,
    )
    new_dist["max"] = existing_max if existing_max is not None else minted

    prior_dist = dict(dist)
    field_col = "primary_loss" if field == "primary" else "secondary_loss"
    # validate_fair_distributions raises FAIRCAMValidationError (a
    # ValidationError subclass) directly on D19-floor / other FAIR-CAM
    # failures — deliberately NOT wrapped into LossPinError here (see the
    # class docstring); route layer catches the ValidationError base.
    validate_fair_distributions(
        threat_event_frequency=scenario.threat_event_frequency,
        vulnerability=scenario.vulnerability,
        primary_loss=new_dist if field == "primary" else scenario.primary_loss,
        secondary_loss=new_dist if field == "secondary" else scenario.secondary_loss,
        require_loss_max=True,
    )

    if field == "primary":
        scenario.primary_loss = new_dist
    else:
        scenario.secondary_loss = new_dist

    prev_row_version = scenario.row_version
    scenario.row_version = prev_row_version + 1

    await AuditWriter(db).log(
        organization_id=organization_id,
        entity_type="scenario",
        entity_id=scenario.id,
        action="scenario.loss_pinned",
        changes={
            "field": [None, field],
            "expected_row_version": [None, expected_row_version],
            field_col: [prior_dist, new_dist],
            "row_version": [prev_row_version, scenario.row_version],
        },
        user_id=actor.id,
        ip_address=ip_address,
    )
    await db.flush()
    return scenario


async def unpin_loss(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    scenario_id: uuid.UUID,
    field: Literal["primary", "secondary"],
    expected_row_version: int,
    actor: User,
    ip_address: str | None = None,
) -> Scenario:
    """Remove the ``analyst_pin`` stamp from ``field``. ``mean``/``sigma``/
    ``max`` and every other metadata key are left BYTE-UNCHANGED — only the
    ``sigma_recalibration`` key itself is popped (unpin restores tripwire
    eligibility per D20; it does not restore a superseded fit, which would
    silently resurrect a possibly-stale pooled estimate).

    Raises :class:`LossPinError` (422) when the field is not currently
    ``analyst_pin``-stamped. Same NotFound/Conflict contract as
    :func:`pin_loss`.
    """
    repo = ScenarioRepo(db)
    scenario = await repo.get_for_org(
        organization_id=organization_id, scenario_id=scenario_id, lock=True
    )
    if scenario is None:
        raise NotFoundError(f"scenario_id={scenario_id} not found")
    _check_lock(scenario, expected_row_version)

    dist = _field_dist(scenario, field)
    meta = dict(dist.get("distribution_fit_metadata") or {})
    stamp = meta.get("sigma_recalibration")
    if not isinstance(stamp, dict) or stamp.get("source") != "analyst_pin":
        raise LossPinError("This field is not currently pinned.")

    prior_dist = dict(dist)
    meta.pop("sigma_recalibration")
    new_dist = dict(dist)
    new_dist["distribution_fit_metadata"] = meta
    field_col = "primary_loss" if field == "primary" else "secondary_loss"

    if field == "primary":
        scenario.primary_loss = new_dist
    else:
        scenario.secondary_loss = new_dist

    prev_row_version = scenario.row_version
    scenario.row_version = prev_row_version + 1

    await AuditWriter(db).log(
        organization_id=organization_id,
        entity_type="scenario",
        entity_id=scenario.id,
        action="scenario.loss_unpinned",
        changes={
            "field": [None, field],
            "expected_row_version": [None, expected_row_version],
            field_col: [prior_dist, new_dist],
            "row_version": [prev_row_version, scenario.row_version],
        },
        user_id=actor.id,
        ip_address=ip_address,
    )
    await db.flush()
    return scenario


# ---------------------------------------------------------------------------
# Refresh (PR3 Task 4, D23) -- replaces PL/SL wholesale from the scenario's
# pinned library entry's CURRENT published dicts. Routed through the exact
# same pair fresh adoption already uses (``resolve_for_clone`` +
# ``library_calibrated_pre_fill``, Arch-N2) so a refreshed scenario cannot
# drift from what a brand-new clone of the same entry would produce.
# ---------------------------------------------------------------------------


def _stamp_source(dist: Any) -> str | None:
    """The ``sigma_recalibration.source`` string stamped on ONE field's
    dist dict, or ``None`` when absent/malformed at any layer. Same
    defensive walk as ``routes.scenarios._field_has_provenance`` -- kept as
    a separate, smaller helper here (only the ``analyst_pin`` value matters
    to the refuse-on-pinned check below; the routes-layer helper additionally
    treats ``migration_recalibration`` as provenance for the tripwire, which
    is out of scope for refresh's own pin-refusal rule).
    """
    if not isinstance(dist, dict):
        return None
    meta = dist.get("distribution_fit_metadata")
    if not isinstance(meta, dict):
        return None
    stamp = meta.get("sigma_recalibration")
    if not isinstance(stamp, dict):
        return None
    source = stamp.get("source")
    return source if isinstance(source, str) else None


@dataclass(frozen=True)
class RefreshPlan:
    """Read-only result of resolving a scenario against its pinned library
    entry -- everything needed to EITHER render the two-step confirm page
    (``routes.scenarios.refresh_scenario_loss``, no mutation) OR perform the
    write (:func:`refresh_loss_from_library`). Built by
    :func:`_resolve_refresh` exactly once per call so preview and write can
    never see two different resolutions of "the entry's current dicts".
    """

    scenario: Scenario
    resolved: ResolvedLibraryEntry
    new_primary_loss: dict[str, Any]
    new_secondary_loss: dict[str, Any] | None


async def _resolve_refresh(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    scenario_id: uuid.UUID,
    expected_row_version: int,
    lock: bool,
) -> RefreshPlan:
    """Shared validation + resolution for both the read-only preview
    (``lock=False``) and the real write (``lock=True``). Raises
    :class:`idraa.errors.NotFoundError` (missing/wrong-org scenario),
    :class:`idraa.services.scenarios.ScenarioVersionConflictError`
    (optimistic-lock mismatch), or :class:`LossPinError` (no library
    linkage, an analyst-pinned field, or the linked entry no longer being
    resolvable -- draft/deprecated/deleted, D23 "never 500").

    Never mutates ``scenario`` or the session -- callers apply the plan.
    """
    repo = ScenarioRepo(db)
    scenario = await repo.get_for_org(
        organization_id=organization_id, scenario_id=scenario_id, lock=lock
    )
    if scenario is None:
        raise NotFoundError(f"scenario_id={scenario_id} not found")
    _check_lock(scenario, expected_row_version)

    library_pin = scenario.library_pin
    if not isinstance(library_pin, dict) or not library_pin.get("entry_id"):
        raise LossPinError("This scenario has no library entry to refresh from.")

    # D23: refresh refuses outright on ANY analyst-pinned loss field (the
    # scenario-level rule -- distinct from pin/unpin's per-field scope).
    # An analyst who deliberately pinned a field's dispersion must unpin it
    # first, same "unpin before you can move on" discipline the D20 skip-
    # guard already enforces against blind migration sweeps.
    for dist in (scenario.primary_loss, scenario.secondary_loss):
        if _stamp_source(dist) == "analyst_pin":
            raise LossPinError(
                "This scenario has an analyst-pinned loss field — unpin it "
                "before refreshing from the library."
            )

    try:
        resolved = await ScenarioLibraryService(db).resolve_for_clone(
            uuid.UUID(str(library_pin["entry_id"])), organization_id
        )
    except (LibraryEntryNotFoundError, LibraryEntryStatusError) as exc:
        raise LossPinError(f"This scenario's library entry is no longer available: {exc}") from exc

    form_dict, _meta = library_calibrated_pre_fill(resolved.entry, resolved.override)
    organization = await db.get(Organization, organization_id)
    minted = capacity_max_for_org(
        organization.annual_revenue if organization is not None else None,
        get_settings().capacity_k,
    )
    # D14: entries carry no ``max`` of their own -- capacity is minted fresh
    # at instantiation, same as any other new adoption of this entry. A
    # shallow copy is required (not just alias) -- form_dict's leaves are the
    # entry/override's OWN persisted dicts; mutating them in place would
    # corrupt the library row through the ORM's shared object reference.
    new_pl: dict[str, Any] = dict(form_dict["pl"]) if form_dict["pl"] is not None else {}
    new_sl: dict[str, Any] | None = dict(form_dict["sl"]) if form_dict["sl"] is not None else None
    for new_dist in (new_pl, new_sl):
        if isinstance(new_dist, dict) and str(new_dist.get("distribution", "")).lower() in (
            "lognormal",
            "lognormal_mixture",
        ):
            new_dist["max"] = minted

    return RefreshPlan(
        scenario=scenario, resolved=resolved, new_primary_loss=new_pl, new_secondary_loss=new_sl
    )


async def preview_loss_refresh(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    scenario_id: uuid.UUID,
    expected_row_version: int,
) -> RefreshPlan:
    """Read-only variant for the two-step confirm page
    (``routes.scenarios.refresh_scenario_loss``'s first, unconfirmed POST).

    Runs the SAME validation ``refresh_loss_from_library`` will re-run under
    a row lock at confirm time (deliberately not trusted across the two
    requests -- another edit/pin/refresh can land in between) but takes no
    lock and performs no write, so a scenario that would fail to refresh
    (no linkage, a pinned field, an unresolvable entry) surfaces its error
    on the FIRST POST rather than rendering a confirm page for an action
    that can only 422 on confirm.
    """
    return await _resolve_refresh(
        db,
        organization_id=organization_id,
        scenario_id=scenario_id,
        expected_row_version=expected_row_version,
        lock=False,
    )


async def refresh_loss_from_library(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    scenario_id: uuid.UUID,
    expected_row_version: int,
    actor: User,
    ip_address: str | None = None,
) -> Scenario:
    """Replace ``scenario``'s primary/secondary loss wholesale with its
    pinned library entry's CURRENT published dicts (D23).

    Raises the same exception set as :func:`preview_loss_refresh`, plus
    fair_cam's :class:`idraa.errors.FAIRCAMValidationError` (D19 capacity
    floor / other FAIR-CAM validation) when the entry's own distributions
    fail to validate against this org's capacity. Flushes on success --
    commit is the caller's responsibility, same as :func:`pin_loss`/
    :func:`unpin_loss`.

    No step-up (Arch-N4, decided): parity with ``ScenarioService.update``,
    which can equally overwrite PL/SL without ``StepUpCategory.DESTRUCTIVE``
    -- delete remains the only destructive step-up action on this router.
    """
    plan = await _resolve_refresh(
        db,
        organization_id=organization_id,
        scenario_id=scenario_id,
        expected_row_version=expected_row_version,
        lock=True,
    )
    scenario = plan.scenario

    # validate_fair_distributions raises FAIRCAMValidationError (a
    # ValidationError subclass) directly on D19-floor / other FAIR-CAM
    # failures -- deliberately NOT wrapped into LossPinError here, mirroring
    # pin_loss's own docstring rationale.
    validate_fair_distributions(
        threat_event_frequency=scenario.threat_event_frequency,
        vulnerability=scenario.vulnerability,
        primary_loss=plan.new_primary_loss,
        secondary_loss=plan.new_secondary_loss,
        require_loss_max=True,
    )

    prior_pl = dict(scenario.primary_loss) if isinstance(scenario.primary_loss, dict) else None
    prior_sl = dict(scenario.secondary_loss) if isinstance(scenario.secondary_loss, dict) else None
    prior_pin = dict(scenario.library_pin) if isinstance(scenario.library_pin, dict) else None

    scenario.primary_loss = plan.new_primary_loss
    scenario.secondary_loss = plan.new_secondary_loss
    scenario.library_pin = plan.resolved.pin

    prev_row_version = scenario.row_version
    scenario.row_version = prev_row_version + 1

    await AuditWriter(db).log(
        organization_id=organization_id,
        entity_type="scenario",
        entity_id=scenario.id,
        action="scenario.loss_refreshed_from_library",
        changes={
            "expected_row_version": [None, expected_row_version],
            "primary_loss": [prior_pl, plan.new_primary_loss],
            "secondary_loss": [prior_sl, plan.new_secondary_loss],
            "library_pin": [prior_pin, plan.resolved.pin],
            "row_version": [prev_row_version, scenario.row_version],
        },
        user_id=actor.id,
        ip_address=ip_address,
    )
    await db.flush()
    return scenario
