"""Per-fieldset fit -> pool -> collapse pipeline. See spec section 5.2 + 7.3.

Pure-ish service layer between the wizard route handler (T11) and
fair_cam.quantile_pooling. Synchronous on purpose so the scipy.optimize
loop runs off the event loop via run_in_threadpool at the route layer
(Sec-20 R3). Persistence + ScenarioSMEEstimate writes live in
``persist_estimates``; audit emission for the SME-estimate rows is
deferred to T10 (Spec-5 PR1).

Module-level ``_FINALIZE_SEMAPHORE`` (Arch-30 R4) caps in-flight finalize
runs at 1 per Python process == 1 per uvicorn worker. Acquire/release
is the route handler's responsibility (T11).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_DNS, UUID, uuid5

from fair_cam.quantile_pooling import (
    ClampEvent,
    LogNormalTruncFit,
    LognormMixture,
    ModeClampReason,
    NormalTruncFit,
    NormMixture,
    PertTriple,
    QuantilePoolingError,  # noqa: F401  -- re-exported for callers that catch fit failures
    clean_quantile_pair,
    combine_lognorm_trunc,
    combine_norm,
    fit_norm_trunc,
    lognormal_from_quantiles,
    lognormal_mixture_to_pert_approx,
    normal_mixture_to_pert_approx,
)
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models.enums import ScenarioFieldset
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.services.audit import AuditWriter, bucket_amount
from idraa.services.wizard_state import WizardState

# Arch-30 R4: module-level == per-Python-process == per-worker on uvicorn.
# Single in-flight finalize per worker keeps the synchronous scipy.optimize
# loop from saturating CPU when multiple analysts hit finalize at once.
_FINALIZE_SEMAPHORE: asyncio.Semaphore = asyncio.Semaphore(1)


# MD-6: tef/vuln/pl are required; sl is optional.
REQUIRED_FIELDSETS: tuple[str, ...] = ("tef", "vuln", "pl")
# Typed so the loop var carries the Literal clean_quantile_pair() expects.
_ALL_FIELDSETS: tuple[Literal["tef", "vuln", "pl", "sl"], ...] = ("tef", "vuln", "pl", "sl")


# MD-1 / MD-4a: vuln uses normal-truncated (because it's a bounded
# probability), all others use lognormal-truncated. Consolidated from
# 3 parallel dispatch dicts into a single pipeline-per-fieldset so the
# fit/combine/collapse triple cannot drift apart on future edits.
@dataclass(frozen=True)
class _FieldsetPipeline:
    """fit -> pool -> (optional) collapse triple for one fieldset (MD-1/MD-4a).

    ``collapser=None`` (Epic B #326 D5/D6) means the pooled fit is stored
    NATIVELY (lognormal {mean, sigma}) rather than collapsed to a PERT
    triple. Lognormal fieldsets (tef/pl/sl) take this path; vuln keeps its
    normal->PERT collapser because it is a bounded probability.
    """

    fitter: Callable[..., Any]
    combiner: Callable[..., Any]
    collapser: Callable[..., Any] | None


def _fit_lognorm_native(
    low: float,
    high: float,
    q_low: float = 0.05,
    q_high: float = 0.95,
    min_support: float = 0.0,
    max_support: float = math.inf,
    **_ignored: Any,
) -> LogNormalTruncFit:
    """Closed-form untruncated lognormal fit for the native-storage path.

    Epic B #326 D5/D6 moved tef/pl/sl to native ``{mean, sigma}`` storage, but
    the wizard pooling pipeline kept routing the fit through ``fit_lognorm_trunc``
    (the truncated, scipy-optimized fitter) — exactly what
    ``_lognormal_native``'s module docstring warns against. That fitter diverges
    for large/wide anchors: from its fixed ``x0=[0.01, 1.0]`` seed, Nelder-Mead
    perturbs each coordinate by ~5% to build its initial simplex, i.e. ~0.0005
    on the ``0.01`` meanlog seed — far too small a step to traverse to a true
    meanlog of ~12 within ``maxiter``, so it stalls at a garbage
    ``meanlog~=0, sdlog~=10.76`` (e.g. a $1k-$50M range fit to an implied median
    of ~$1). The ``sigma<=10`` storage guard then rejected the garbage,
    surfacing as a 500.

    The two-quantile fit of an *untruncated* lognormal is closed-form and exact
    (see ``lognormal_from_quantiles``), and the wizard's support is the
    non-binding ``[0, inf]`` — so the truncated optimizer was never needed here.
    This wrapper makes the wizard consistent with the form-create + import paths
    (``scenario_form_helpers`` / ``scenario_import_parsers``), which already use
    ``lognormal_from_quantiles``. It returns a ``LogNormalTruncFit`` so the
    unchanged ``combine_lognorm_trunc`` pooler keeps working. ``maxiter`` /
    ``wall_clock_ms`` kwargs are accepted-and-ignored (no optimizer to bound).

    CONTRACT: the closed form is for an *untruncated* lognormal, so it is only
    valid where the support is non-binding (``min_support <= 0`` and
    ``max_support == +inf``) — which is all the lognormal fieldsets (tef/pl/sl).
    The assertion below makes that latent assumption loud: a future fieldset with
    genuinely binding support must NOT be routed through this wrapper (it would
    silently ignore the bound and mis-fit) — use a truncated fitter instead.

    A degenerate ``low == high`` (e.g. both PL anchors below the $1000 floor →
    cleaned to ``(1000, 1000)``) yields ``sigma == 0``; that is left to the
    downstream ``sigma > 0`` storage guard (gracefully surfaced as a review-page
    flash, not a 500), matching the rejection contract for an unstorable point
    mass.
    """
    if min_support > 0 or math.isfinite(max_support):  # pragma: no cover
        # No current caller violates this (tef/pl/sl all use [0, inf]); the
        # guard exists so a future binding-support fieldset fails loud here
        # rather than silently mis-fitting via the untruncated closed form.
        raise ValueError(
            "_fit_lognorm_native requires non-binding support (the closed form "
            f"is untruncated); got min_support={min_support}, max_support={max_support}"
        )
    fit = lognormal_from_quantiles(low, high, q_low, q_high)
    return LogNormalTruncFit(
        meanlog=fit["mean"],
        sdlog=fit["sigma"],
        min_support=min_support,
        max_support=max_support,
    )


# Native-lognormal storage (collapser=None): the fit is the closed-form
# untruncated two-quantile solution (_fit_lognorm_native) — NOT the truncated
# scipy fitter, which diverges for large/wide anchors (see _fit_lognorm_native).
# The pooled mixture's components carry the native untruncated params (issue
# #27 Task 5: combine_lognorm_trunc now returns a LognormMixture, not a single
# averaged fit — build_scenario_payload reads the mixture's components).
# Post-Milestone-B (#loss-pert-overhaul) this is the CATASTROPHIC pl/sl path
# only (Epic B #326 D5/D6 had made it the universal tef/pl/sl path).
_LOGNORMAL_PIPELINE = _FieldsetPipeline(
    fitter=_fit_lognorm_native,
    combiner=combine_lognorm_trunc,
    collapser=None,
)
_NORMAL_PIPELINE = _FieldsetPipeline(
    fitter=fit_norm_trunc,
    combiner=combine_norm,
    # issue #27 Task 5: mixture-aware collapser. Single-component mixtures
    # delegate to the same closed-form/clamp code path as normal_to_pert_approx
    # by construction (see lognormal_mixture_to_pert_approx's docstring for the
    # byte-identity argument, mirrored here for the normal counterpart).
    collapser=normal_mixture_to_pert_approx,
)
# Lognormal fit COLLAPSED to a bounded right-skewed PERT. Used by tef
# (#tef-pert-revert, Milestone A) and by capped pl/sl (#loss-pert-overhaul,
# Milestone B — the default loss shape). This RESTORES the genuine pre-Epic-B
# authoring shape: #247 (7b417e0) authored TEF as a lognormal fit COLLAPSED to
# PERT (fit_lognorm_trunc -> lognormal_to_pert_approx); Epic B (#326 D5/D6)
# merely dropped the collapser (native lognormal storage). The CLOSED-FORM
# _fit_lognorm_native avoids the scipy truncated-fit divergence documented in
# its module docstring. Yields a right-skewed PERT (mode in the lower half of
# [low, high], clamped to low for very wide anchors) consistent with the
# curated library; a normal fit + normal_to_pert_approx would give a WRONG
# symmetric mode and is ill-conditioned on [0, +inf) (plan-gate methodology
# BLOCKER, Milestone A). _fit_lognorm_native requires non-binding support
# (min_support<=0, max_support=+inf); tef's and pl/sl's [0, +inf) both fit.
# issue #27 Task 5: collapser swapped to the mixture-aware
# lognormal_mixture_to_pert_approx — combine_lognorm_trunc now returns a
# LognormMixture, and a single-component mixture collapses byte-identically
# to the pre-mixture lognormal_to_pert_approx by construction (same clamp
# code path — see that function's docstring).
_LOGNORMAL_TO_PERT_PIPELINE = _FieldsetPipeline(
    fitter=_fit_lognorm_native,
    combiner=combine_lognorm_trunc,
    collapser=lognormal_mixture_to_pert_approx,
)
_PIPELINE_BY_FIELDSET: dict[str, _FieldsetPipeline] = {
    "tef": _LOGNORMAL_TO_PERT_PIPELINE,  # was _LOGNORMAL_PIPELINE (#tef-pert-revert)
    "vuln": _NORMAL_PIPELINE,
    # Milestone B: capped (default) collapses to PERT; process_sme_estimates
    # swaps in _LOGNORMAL_PIPELINE when state.loss_shape == "catastrophic".
    "pl": _LOGNORMAL_TO_PERT_PIPELINE,
    "sl": _LOGNORMAL_TO_PERT_PIPELINE,
}


def fieldset_support(fieldset: str) -> dict[str, float]:
    """Support bounds per fieldset for the truncated fit.

    - tef/pl/sl: positive reals (lognormal).
    - vuln: probability in [0, 1] (truncated normal).
    """
    return {
        "tef": {"min_support": 0.0, "max_support": math.inf},
        "vuln": {"min_support": 0.0, "max_support": 1.0},
        "pl": {"min_support": 0.0, "max_support": math.inf},
        "sl": {"min_support": 0.0, "max_support": math.inf},
    }[fieldset]


def row_identity_uuid(row: dict[str, Any]) -> UUID:
    """Return a stable UUID per estimate-row identity.

    FK row (sme_id set) → that UUID.
    Free-text row (sme_name set) → uuid5(NAMESPACE_DNS, "freetext:" + name.casefold()).

    The synth UUID lets AuditClampEvent.sme_id and build_scenario_payload's
    sidecar `sme_ids: list[str]` stay typed as UUID-shaped values whether
    the row came from the directory or from free text. Casefold makes the
    derivation case-insensitive so "Alice" and "alice" map to the same id.
    """
    sme_id = row.get("sme_id")
    if sme_id is not None:
        return sme_id if isinstance(sme_id, UUID) else UUID(str(sme_id))
    name = row["sme_name"]
    return uuid5(NAMESPACE_DNS, f"freetext:{name.casefold()}")


@dataclass(frozen=True)
class AuditClampEvent:
    """Audit-shaped clamp event with scenario context attached.

    Wraps fair_cam's narrow ``ClampEvent`` (rule/before/after) with the
    ``fieldset`` + ``sme_id`` (always UUID — synth for free-text rows via
    ``row_identity_uuid``) + ``sme_name`` (nullable, populated only for
    free-text rows) columns the audit log needs. Emission is the
    ``persist_estimates`` responsibility.
    """

    rule: str
    before: tuple[float, float]
    after: tuple[float, float]
    fieldset: str
    sme_id: UUID
    sme_name: str | None = None
    scenario_id: UUID | None = None  # filled at persist time per spec §7.1

    @classmethod
    def from_narrow(
        cls,
        narrow: ClampEvent,
        *,
        fieldset: str,
        sme_id: UUID,
        sme_name: str | None = None,
        scenario_id: UUID | None = None,
    ) -> AuditClampEvent:
        return cls(
            rule=narrow.rule,
            before=narrow.before,
            after=narrow.after,
            fieldset=fieldset,
            sme_id=sme_id,
            sme_name=sme_name,
            scenario_id=scenario_id,
        )


@dataclass(frozen=True)
class PerFieldsetResult:
    """Per-fieldset pipeline result: pooled mixture + PERT triple + clamp trail.

    ``pooled`` is either ``LognormMixture`` or ``NormMixture`` (vuln) — issue
    #27 Task 5: a linear-opinion-pool mixture over the per-SME fits, not a
    single averaged fit. Single-SME pooling is a single-component mixture by
    construction (``combine_lognorm_trunc``/``combine_norm``), so every
    downstream consumer that only ever handles one SME sees the identical
    numeric result it always did — see ``build_scenario_payload``'s
    single-component branch.
    ``mode_clamp_reason`` is unpacked from the collapser's tuple return per
    Spec-24 PR3 and lands in the sidecar metadata of the PERT-stored nodes.
    Native-lognormal fieldsets (catastrophic pl/sl) have no collapser, so
    their ``pert`` is a placeholder zero-triple and ``mode_clamp_reason`` is
    None. ``rows`` is the post-dedup list of {"sme_id", "low", "high"} dicts
    the fit consumed (used to count n_smes + replay sme_ids/weights in the
    sidecar) and is also what ``persist_estimates`` inserts.
    """

    pooled: LognormMixture | NormMixture
    pert: PertTriple
    # spec §7.3 says str | None; we use the enum here for type-safety;
    # build_scenario_payload serializes via .value into the JSON sidecar
    mode_clamp_reason: ModeClampReason | None
    rows: list[dict[str, Any]]
    clamp_events: list[AuditClampEvent]
    # Milestone B (#loss-pert-overhaul): True when the pooled fit was collapsed
    # to PERT for storage. build_scenario_payload dispatches on THIS (not the
    # static registry) — pl/sl shape is per-scenario now.
    collapsed: bool = False


class FinalizationError(ValueError):
    """Wizard-finalize failure surface.

    ``field_errors`` is the {fieldset: message} map the route layer
    re-renders alongside the step-3 form (HTTP 422-style).
    ``aggregate_timeout`` flags the Sec-12 R3 aggregate-budget bust so the
    handler can emit a distinct 504-ish surface rather than masquerading
    as a validation error. Retained on the parent class for back-compat
    with any existing consumers; new code should catch
    ``FinalizeBudgetExceededError`` for the timeout-specific surface.
    """

    def __init__(
        self,
        message: str = "",
        *,
        field_errors: dict[str, str] | None = None,
        aggregate_timeout: bool = False,
    ) -> None:
        super().__init__(message or "Finalization failed")
        self.field_errors = field_errors or {}
        self.aggregate_timeout = aggregate_timeout


class FinalizeBudgetExceededError(FinalizationError):
    """Sec-12 PR3 aggregate finalize-budget exceeded. Distinct subclass so
    the route layer can dispatch on class rather than sniffing an
    ``aggregate_timeout`` flag. Parent ``aggregate_timeout=True`` is
    preserved for back-compat with any existing consumers.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, aggregate_timeout=True)


def _dedup_latest_per_sme(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the last submission per identity (later writes win).

    Identity is the synth UUID from ``row_identity_uuid`` — same for FK and
    free-text rows. Two typed "alice" rows collapse to one (casefolded);
    a typed "Alice" and an FK Alice are distinct identities by construction
    (different UUID derivations).
    """
    seen: dict[UUID, dict[str, Any]] = {}
    for r in rows:
        seen[row_identity_uuid(r)] = r
    return list(seen.values())


def process_sme_estimates(state: WizardState) -> dict[str, PerFieldsetResult]:
    """Run fit -> pool -> collapse for every fieldset present in ``state.sme_estimates``.

    Synchronous on purpose. The route layer (T11) wraps this in
    ``run_in_threadpool(...)`` so the scipy.optimize loop runs off the
    event loop (Sec-20 R3).

    Returns a dict keyed by fieldset name with one ``PerFieldsetResult``
    per fieldset that had >=1 estimate. Required fieldsets (tef, vuln,
    pl per MD-6) with 0 estimates raise ``FinalizationError`` with a
    ``field_errors`` map; optional ``sl`` with 0 estimates is silently
    skipped.

    Spec-10/Arch-11 PR1: the aggregate-budget check (``settings.finalize_wall_clock_ms``)
    is interleaved INSIDE the per-fit loop, not after a list comprehension
    -- so a single divergent fieldset cannot bust the aggregate budget by
    4x before raising.

    Spec-24 PR3: the collapser returns a ``(PertTriple, ModeClampReason | None)``
    tuple; both elements are unpacked and propagated through the result.
    """
    settings = get_settings()
    start_ms = time.monotonic_ns() // 1_000_000
    aggregate_budget = settings.finalize_wall_clock_ms
    per_fit_budget = settings.quantile_fit_wall_clock_ms
    per_fit_maxiter = settings.quantile_fit_maxiter

    results: dict[str, PerFieldsetResult] = {}
    for fieldset in _ALL_FIELDSETS:
        raw = state.sme_estimates.get(fieldset, [])
        rows = _dedup_latest_per_sme(raw)
        if not rows:
            if fieldset in REQUIRED_FIELDSETS:
                raise FinalizationError(
                    field_errors={
                        fieldset: f"Need >=1 SME estimate for {fieldset}",
                    },
                )
            continue

        clamp_events: list[AuditClampEvent] = []
        cleaned: list[tuple[float, float]] = []
        for r in rows:
            (lo, hi), narrow = clean_quantile_pair(r["low"], r["high"], fieldset)
            cleaned.append((lo, hi))
            if narrow is not None:
                clamp_events.append(
                    AuditClampEvent.from_narrow(
                        narrow,
                        fieldset=fieldset,
                        sme_id=row_identity_uuid(r),
                        sme_name=r.get("sme_name"),
                    )
                )

        pipeline = _PIPELINE_BY_FIELDSET[fieldset]
        if fieldset in ("pl", "sl") and state.loss_shape == "catastrophic":
            # Uncapped native lognormal storage (#loss-pert-overhaul). Fails
            # closed: ANY other value (including a tampered/unknown loss_shape)
            # falls through to the bounded capped/PERT default above.
            pipeline = _LOGNORMAL_PIPELINE
        # Spec-10/Arch-11 PR1 fix: budget guard INTERLEAVED with each per-fit so
        # a divergent fieldset cannot bust the aggregate by 4x before raising.
        fits: list[LogNormalTruncFit | NormalTruncFit] = []
        for lo, hi in cleaned:
            now_ms = time.monotonic_ns() // 1_000_000
            if now_ms - start_ms > aggregate_budget:
                raise FinalizeBudgetExceededError(
                    f"Finalize exceeded {aggregate_budget}ms aggregate budget",
                )
            fits.append(
                pipeline.fitter(
                    lo,
                    hi,
                    **fieldset_support(fieldset),
                    maxiter=per_fit_maxiter,
                    wall_clock_ms=per_fit_budget,
                )
            )

        pooled = pipeline.combiner(fits)
        # Native-lognormal fieldsets (catastrophic pl/sl) store {mean, sigma},
        # so there is no PERT collapse (collapser=None) -- placeholder triple +
        # no clamp. Collapsing pipelines (tef, capped pl/sl, vuln) MUST unpack
        # the (PertTriple, ModeClampReason | None) tuple (Spec-24 PR3).
        if pipeline.collapser is None:
            pert, mode_clamp_reason = PertTriple(low=0.0, mode=0.0, high=0.0), None
        else:
            pert, mode_clamp_reason = pipeline.collapser(pooled)

        results[fieldset] = PerFieldsetResult(
            pooled=pooled,
            pert=pert,
            mode_clamp_reason=mode_clamp_reason,
            rows=rows,
            clamp_events=clamp_events,
            collapsed=pipeline.collapser is not None,
        )
    return results


def _pooled_support(pooled: LognormMixture | NormMixture) -> tuple[float, float]:
    """Support bounds for a mixture's sidecar (issue #27 Task 5 binding
    amendment): every component of a wizard-produced mixture was fit against
    the SAME fieldset support (``fieldset_support``), so the mixture's
    support is per-fieldset-uniform by construction — component[0]'s support
    IS the mixture's support. Asserted here (not silently assumed) so a
    future caller that hand-builds a mixture with divergent per-component
    supports fails loud rather than silently reporting component[0]'s bounds
    for the whole pool.
    """
    components = pooled.components
    min_support, max_support = components[0].min_support, components[0].max_support
    for c in components[1:]:
        if c.min_support != min_support or c.max_support != max_support:
            # S101: raise explicitly (bare `assert` is stripped under -O and
            # banned in application code by the repo's ruff config) rather
            # than asserting.
            raise AssertionError(
                "mixture components must share support bounds (per-fieldset-"
                f"uniform by construction); got "
                f"{[(c.min_support, c.max_support) for c in components]}"
            )
    return min_support, max_support


def pooling_component_fields(r: PerFieldsetResult) -> dict[str, list[float]]:
    """Per-component location/scale lists for the sidecar + audit summary
    (issue #27 Task 5).

    Pre-mixture, ``r.pooled`` was a single averaged fit, so the sidecar
    stored ONE ``pooled_meanlog``/``pooled_sdlog`` (or ``pooled_mean``/
    ``pooled_sd``) scalar pair. ``r.pooled`` is now always a
    ``LognormMixture``/``NormMixture`` with 1..N components (single-SME
    pooling is a single-component mixture by construction), so there is no
    longer one scalar to report — this returns the per-component lists
    instead (single-element for n=1). Shared by ``build_scenario_payload``'s
    ``distribution_fit_metadata`` sidecar AND the finalize route's
    ``per_fieldset_pooling_summary`` audit fields (routes/scenarios.py) so
    both surfaces report the identical shape.

    No scalar back-compat keys: a repo-wide grep (issue #27 Task 5) found
    the retired ``pooled_meanlog``/``pooled_sdlog``/``pooled_mean``/
    ``pooled_sd`` sidecar keys read ONLY by tests (never by application
    code), so they are dropped outright rather than kept alongside the new
    list keys — see the Task 5 commit message for the grep evidence.
    """
    if isinstance(r.pooled, LognormMixture):
        return {
            "component_meanlogs": [c.meanlog for c in r.pooled.components],
            "component_sdlogs": [c.sdlog for c in r.pooled.components],
        }
    return {
        "component_means": [c.mean for c in r.pooled.components],
        "component_sds": [c.sd for c in r.pooled.components],
    }


def build_scenario_payload(
    results: dict[str, PerFieldsetResult], state: WizardState
) -> dict[str, Any]:
    """Convert per-fieldset results into the ScenarioForm FAIR-distribution
    payload + sidecar metadata.

    schema_version 3 (issue #27 Task 5, true mixture pooling): ``r.pooled``
    is now a linear-opinion-pool mixture (``LognormMixture``/``NormMixture``,
    T1) rather than a single averaged fit. Three distribution shapes:

      Lognormal node COLLAPSED to PERT (tef always; capped pl/sl, the
      default) — the mixture is collapsed via
      ``lognormal_mixture_to_pert_approx`` (T2), unchanged {low, mode, high}
      shape:
        {"distribution": "PERT", "low", "mode", "high",
         "distribution_fit_metadata": {
            source, fitter, component_meanlogs, component_sdlogs,
            mode_boundary_clamped, mode_clamp_reason, schema_version,
            pooling_method, q_low_quantile, q_high_quantile,
            pooled_min_support, pooled_max_support, n_smes, sme_ids, weights,
            fitted_at}}

      Lognormal node stored NATIVE, single-SME (catastrophic pl/sl,
      ``len(components) == 1``) — plain lognormal, BYTE-IDENTICAL to the
      pre-mixture single-SME output (the identity-pin scope: this
      distribution dict only, NOT the sidecar):
        {"distribution": "lognormal", "mean": meanlog, "sigma": sdlog,
         "distribution_fit_metadata": {...}}

      Lognormal node stored NATIVE, multi-SME (catastrophic pl/sl,
      ``len(components) > 1``) — the genuine #27 fix: divergent experts are
      represented as a mixture, not averaged into a distribution covering
      neither expert's stated range:
        {"distribution": "lognormal_mixture",
         "components": [{"mean", "sigma", "weight"}, ...],
         "distribution_fit_metadata": {...}}

      Normal node (vuln) — always collapsed to PERT (unchanged {low, mode,
      high} shape from schema_version 1):
        {"low", "mode", "high", "distribution_fit_metadata": {
            source, fitter, component_means, component_sds,
            mode_boundary_clamped, mode_clamp_reason, schema_version,
            pooling_method, q_low_quantile, q_high_quantile,
            pooled_min_support, pooled_max_support, n_smes, sme_ids, weights,
            fitted_at}}

    ``weights`` in the sidecar are the REAL linear-opinion-pool weights
    normalized to sum to 1 (``[1.0]`` for n=1; e.g. ``[0.5, 0.5]`` for two
    equally-weighted SMEs) — replacing the pre-mixture hardcoded
    ``[1.0] * n_smes``, which was not itself a normalized weight vector for
    n>1. ``pooled_min_support``/``pooled_max_support`` are asserted
    per-fieldset-uniform across components (``_pooled_support``).

    Per Spec-11 PR1 the sidecar field-set test asserts key equality, not
    just length, so future additions / drops are loud.

    ``n_smes`` reflects the post-dedup row count, not the number of distinct
    humans. Free-text rows with the same casefolded name collapse to one; a
    typed "Alice" and a directory-FK Alice count as two distinct identities
    because their synth-vs-real UUIDs differ.
    """
    payload: dict[str, Any] = {}
    fitted_at = datetime.now(UTC).isoformat()
    for fieldset, r in results.items():
        min_support, max_support = _pooled_support(r.pooled)
        common_meta: dict[str, Any] = {
            # v3 (issue #27): true linear-opinion-pool mixture, replacing the
            # v2 parameter-averaged single fit.
            "schema_version": 3,
            "pooling_method": "linear_opinion_pool_v1",
            "q_low_quantile": 0.05,
            "q_high_quantile": 0.95,
            "pooled_min_support": min_support,
            # JSON has no +inf; serialise unbounded support as null.
            "pooled_max_support": max_support if math.isfinite(max_support) else None,
            "n_smes": len(r.rows),
            "sme_ids": [str(row_identity_uuid(row)) for row in r.rows],
            # Real normalized linear-opinion-pool weights (sum to 1), not the
            # pre-mixture hardcoded [1.0] * n_smes.
            "weights": list(r.pooled.weights),
            "fitted_at": fitted_at,
        }
        component_fields = pooling_component_fields(r)
        if isinstance(r.pooled, LognormMixture) and r.collapsed:
            # tef (#tef-pert-revert, Milestone A) + capped pl/sl (Milestone B
            # #loss-pert-overhaul, the default): the lognormal mixture is
            # COLLAPSED to a right-skewed PERT and STORED AS PERT
            # {low, mode, high}. Storage dispatches on r.collapsed — pl/sl
            # shape is per-scenario (state.loss_shape), so the static
            # registry can no longer decide. Provenance keeps the pooled
            # log-params (now per-component lists) for traceability.
            payload[fieldset] = {
                "distribution": "PERT",
                "low": r.pert.low,
                "mode": r.pert.mode,
                "high": r.pert.high,
                "distribution_fit_metadata": {
                    "source": "quantile_lognormal_pool",
                    "fitter": "lognorm_native",
                    **component_fields,
                    "mode_boundary_clamped": r.mode_clamp_reason is not None,
                    "mode_clamp_reason": (
                        r.mode_clamp_reason.value if r.mode_clamp_reason else None
                    ),
                    **common_meta,
                },
            }
        elif isinstance(r.pooled, LognormMixture):
            # CATASTROPHIC pl/sl only (#loss-pert-overhaul): non-binding
            # [0, inf] truncation => each component's (meanlog, sdlog) IS its
            # native untruncated {mean, sigma}. Store native, uncapped by
            # intent; no PERT approximation.
            if len(r.pooled.components) == 1:
                # Single-SME identity pin (issue #27 Task 5): byte-identical
                # to the pre-mixture plain-lognormal output — the dominant
                # production case never regresses to the new mixture shape.
                c = r.pooled.components[0]
                payload[fieldset] = {
                    "distribution": "lognormal",
                    "mean": c.meanlog,
                    "sigma": c.sdlog,
                    "distribution_fit_metadata": {
                        "source": "quantile_lognormal_pool",
                        # Closed-form untruncated two-quantile fit (Epic B
                        # native path) — see _fit_lognorm_native; NOT the
                        # truncated scipy fitter (which diverged for wide
                        # anchors).
                        "fitter": "lognorm_native",
                        **component_fields,
                        **common_meta,
                    },
                }
            else:
                # Genuine #27 fix: divergent multi-SME catastrophic losses
                # are represented as a mixture, not parameter-averaged into a
                # distribution covering neither expert's stated range.
                payload[fieldset] = {
                    "distribution": "lognormal_mixture",
                    "components": [
                        {"mean": c.meanlog, "sigma": c.sdlog, "weight": w}
                        for c, w in zip(r.pooled.components, r.pooled.weights, strict=True)
                    ],
                    "distribution_fit_metadata": {
                        "source": "quantile_lognormal_pool",
                        "fitter": "lognorm_native",
                        **component_fields,
                        **common_meta,
                    },
                }
        else:
            # vuln: bounded probability — unchanged PERT collapse, mixture
            # collapsed via normal_mixture_to_pert_approx.
            payload[fieldset] = {
                "low": r.pert.low,
                "mode": r.pert.mode,
                "high": r.pert.high,
                "distribution_fit_metadata": {
                    "source": "quantile_normal_pool",
                    "fitter": "norm_trunc",
                    **component_fields,
                    "mode_boundary_clamped": r.mode_clamp_reason is not None,
                    "mode_clamp_reason": (
                        r.mode_clamp_reason.value if r.mode_clamp_reason else None
                    ),
                    **common_meta,
                },
            }
    return payload


async def persist_estimates(
    db: AsyncSession,
    scenario_id: UUID,
    *,
    results: dict[str, PerFieldsetResult],
    actor_id: UUID,
    organization_id: UUID,
) -> None:
    """Insert one ``ScenarioSMEEstimate`` row per (fieldset, identity) post-dedup.

    Identity is FK-or-free-text per the 2026-05-25 design; the ORM CHECK
    enforces XOR. Audit emission gains a nullable ``sme_name`` field
    alongside ``sme_id``; downstream consumers tolerate either being null.
    """
    now = datetime.now(UTC)
    writer = AuditWriter(db)
    for fieldset, r in results.items():
        for row in r.rows:
            sme_id_val = row.get("sme_id")
            sme_name_val = row.get("sme_name")
            db.add(
                ScenarioSMEEstimate(
                    organization_id=organization_id,
                    scenario_id=scenario_id,
                    fieldset=ScenarioFieldset(fieldset),
                    sme_id=UUID(str(sme_id_val)) if sme_id_val is not None else None,
                    sme_name=sme_name_val,
                    low=row["low"],
                    high=row["high"],
                    recorded_at=now,
                    recorded_by=actor_id,
                )
            )
    await db.flush()
    for fieldset, r in results.items():
        for row in r.rows:
            sme_id_val = row.get("sme_id")
            sme_name_val = row.get("sme_name")
            await writer.log(
                organization_id=organization_id,
                entity_type="scenario_sme_estimate",
                entity_id=scenario_id,
                action="sme_estimate.recorded",
                changes={
                    "scenario_id": str(scenario_id),
                    "fieldset": fieldset,
                    "sme_id": str(sme_id_val) if sme_id_val is not None else None,
                    "sme_name": sme_name_val,
                    "low": row["low"],
                    "high": row["high"],
                    "low_bucket": bucket_amount(float(row["low"])),
                    "high_bucket": bucket_amount(float(row["high"])),
                },
                user_id=actor_id,
            )
        for ev in r.clamp_events:
            await writer.log(
                organization_id=organization_id,
                entity_type="scenario_sme_estimate",
                entity_id=scenario_id,
                action="sme_estimate.sanity_clamped",
                changes={
                    "scenario_id": str(scenario_id),
                    "fieldset": ev.fieldset,
                    "sme_id": str(ev.sme_id),
                    "sme_name": ev.sme_name,
                    "before": list(ev.before),
                    "after": list(ev.after),
                    "rule": ev.rule,
                },
                user_id=actor_id,
            )
