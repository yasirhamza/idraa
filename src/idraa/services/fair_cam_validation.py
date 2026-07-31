"""fair_cam validator boundary wrapper. Closes GH #2.

Severity == ERROR -> raise FAIRCAMValidationError (caught by routes -> 422).
Severity == WARNING -> returned in FAIRCAMValidationResult for rendering.

F10 ships the minimal validator function. F12 expands FAIRCAMValidationResult
to expose both errors (always []) and warnings for template rendering, wires
6 unit tests, and confirms inheritance fallthrough at the route boundary.

Real fair_cam API (verified against fair_cam/validation/input_validator.py):
- ``from fair_cam.validation import FAIRCAMValidator, ValidationSeverity``
- ``FAIRCAMValidator().validate_risk_parameters(risk_data: dict)`` returns
  ``ValidationSummary(is_valid, results, error_count, warning_count, info_count)``.
- ``risk_data`` keys consumed: ``threat_event_frequency``, ``primary_loss``,
  ``secondary_loss`` (optional). ``vulnerability`` and ``distribution_type``
  are also checked if present.
- Each ``ValidationResult`` has ``severity: ValidationSeverity`` enum,
  ``message: str``, ``field_name: str``.
- ``ValidationSeverity`` enum members: ERROR / WARNING / INFO / SUCCESS
  (string values lowercase: 'error', 'warning', 'info', 'success').
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from fair_cam.validation.input_validator import (
    FAIRCAMValidator,
    ValidationResult,
    ValidationSeverity,
)
from scipy.stats import norm

from idraa.config import get_settings
from idraa.errors import FAIRCAMValidationError as FAIRCAMValidationError

# Module-level singleton (avoids re-allocating per call).
# FAIRCAMValidator is stateless after construction — each validate_risk_parameters
# call materialises its own ValidationSummary accumulator.
_FAIR_CAM_VALIDATOR = FAIRCAMValidator()

# Sec-I2 upper bound for lognormal sigma: sigma=10 already spans ~17 orders of
# magnitude p5->p95, beyond any defensible cyber-loss range. An extreme-but-finite
# sigma is a user-controllable OOM/DoS path to the engine sampler at the 100k cap.
_SIGMA_MAX: float = 10.0

# Canonical 95th-percentile standard-normal z-score, used by the D19
# ``max > p95`` floor below (``services/loss_capacity.py``'s PR2 capacity
# bound). Matches the constant every other p95 reader in the codebase uses
# (``routes/scenarios.py``'s ``_Z_0_95``, ``services/pdf_report.py``'s
# ``_Z_P95``, ``fair_cam/quantile_pooling/_lognormal_native.py``'s
# ``Z_0_95``) -- computed via scipy here rather than re-hardcoding the
# literal, since this module already has a real (non-test) scipy transitive
# dependency via ``fair_cam.validation.input_validator``.
_Z95: float = float(norm.ppf(0.95))

# Sec-L8/#84: float32-representable ceiling (np.float32 max finite ~3.4028e38);
# no legitimate FAIR frequency or USD loss approaches this. A finite-but-enormous
# distribution parameter overflows the float32 sample-array codec (sample_codec.py)
# on encode, producing inf/nan and silently corrupting the stored run. This is a
# representability guard, NOT a semantic loss ceiling.
#
# BASIS MATTERS (the sigma-recalibration epic's root defect class): this constant
# applies directly only to LINEAR-space numerics — PERT/uniform/triangular
# low/mode/high (USD or events/yr) and the D13-D19 capacity ``max`` (USD;
# analyst-typeable per D17, floored by ``_validate_capacity_floor``, ceilinged
# here). The stored lognormal ``mean`` is LOG-space meanlog (fed unconverted to
# ``rng.lognormal(mean, sigma)`` in fair_cam/risk_engine/fair_core.py, and
# compared as ``mean + z95*sigma`` against ``ln(max)`` by the D19 floor), so its
# representability bound is _MEANLOG_MAX below — a 1e38 cap on a meanlog would
# be vacuous (meanlog=300 => median e^300 ~ 1.9e130 USD sails under it).
_MAGNITUDE_MAX: float = 1e38

# Log-space companion bound: exp(_MEANLOG_MAX) == _MAGNITUDE_MAX, i.e. the
# lognormal's MEDIAN (exp(meanlog)) must itself be float32-representable.
# Purely the same representability constant expressed in the correct basis —
# still not a semantic loss ceiling. Draws above the median are bounded by the
# capacity ``max`` clip when present (truncated_lognormal), and by the codec's
# fail-closed overflow guard (sample_codec.py) for legacy no-``max`` rows.
_MEANLOG_MAX: float = math.log(_MAGNITUDE_MAX)


def _validate_vulnerability(vuln: dict[str, Any]) -> list[str]:
    """Return a list of human-readable error strings for an invalid vuln PERT.

    Vulnerability ∈ [0,1] with low ≤ mode ≤ high. Empty list == valid.
    """
    errs: list[str] = []
    vals = []
    for key in ("low", "mode", "high"):
        v = vuln.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errs.append(f"vulnerability.{key} must be numeric, got {v!r}")
        else:
            vals.append(v)
    if len(vals) == 3:
        low, mode, high = vals
        if not (0.0 <= low <= 1.0 and 0.0 <= mode <= 1.0 and 0.0 <= high <= 1.0):
            errs.append("vulnerability low/mode/high must each be within [0, 1]")
        if not (low <= mode <= high):
            errs.append(f"vulnerability must satisfy low ≤ mode ≤ high, got {low}/{mode}/{high}")
    return errs


def _validate_finite(field_name: str, dist: dict[str, Any]) -> list[str]:
    """Reject non-finite (inf/nan) distribution parameters for unbounded-above
    distributions before storage (Meth-B1 #307; Epic B #326 extends to lognormal;
    #27 extends to lognormal_mixture).

    PERT/uniform/triangular: low/mode/high must be finite AND |v| <= _MAGNITUDE_MAX
    (linear-space values, so the linear ceiling applies directly).
    Lognormal: mean is LOG-space meanlog (see _MAGNITUDE_MAX's basis note), so it
    must be finite AND mean <= _MEANLOG_MAX (median exp(mean) representable;
    negative meanlog — sub-$1 medians — is representationally harmless and NOT
    bounded below); sigma must be finite AND 0 < sigma <= 10 (an unbounded right
    tail makes a non-finite mean or non-positive sigma catastrophic; an
    extreme-but-finite sigma is a user-controllable OOM/DoS path to the engine
    sampler at the 100k cap — Sec-I2. sigma=10 already spans ~17 orders of
    magnitude p5->p95, beyond any defensible cyber-loss range).
    lognormal_mixture: same per-component finiteness + magnitude + sigma bound
    as lognormal, PLUS weight > 0 per component, weights summing to 1 (±1e-9),
    and 1 <= len(components) <= Settings.max_smes_per_fieldset (Sec-N1: the
    component count is deliberately coupled to the same cap that already
    bounds SME-estimate fan-out into the wizard finalize pipeline — a mixture
    can never carry more components than a single fieldset could ever
    legitimately produce).

    The magnitude cap (Sec-L8/#84) rejects finite-but-enormous values that
    would overflow the float32 sample-array codec on write (sample_codec.py),
    which otherwise silently corrupts the stored run with inf. Applied to ALL
    fields validated here (TEF, primary_loss, secondary_loss) since it is a
    float32-representability guard, not a semantic loss ceiling — a 1e38
    events/yr TEF is equally absurd and also feeds the codec via risk=LEF*LM.
    """
    errs: list[str] = []
    kind = str(dist.get("distribution", "pert")).lower()
    if kind == "lognormal":
        mean = dist.get("mean")
        sigma = dist.get("sigma")
        if isinstance(mean, (int, float)) and not isinstance(mean, bool):
            if not math.isfinite(mean):
                errs.append(f"{field_name}.mean must be finite, got {mean!r}")
            elif mean > _MEANLOG_MAX:
                errs.append(
                    f"{field_name}.mean (log-space meanlog) implies a median exp(mean) "
                    f"beyond the representable ceiling {_MAGNITUDE_MAX:g}; mean must be "
                    f"<= {_MEANLOG_MAX:.4f}, got {mean!r}"
                )
        if isinstance(sigma, (int, float)) and not isinstance(sigma, bool):
            if not math.isfinite(sigma):
                errs.append(f"{field_name}.sigma must be finite, got {sigma!r}")
            elif sigma <= 0:
                errs.append(f"{field_name}.sigma must be > 0, got {sigma!r}")
            elif sigma > _SIGMA_MAX:
                errs.append(f"{field_name}.sigma must be <= {_SIGMA_MAX}, got {sigma!r}")
        return errs
    if kind == "lognormal_mixture":
        components = dist.get("components")
        if not isinstance(components, list):
            # Shape (missing/non-list "components") is the exact-key-set /
            # numeric-type guard's job — scenario_import._structural_dist_problem
            # on the import path. This function is the semantic gate and runs
            # on EVERY path (not just import), so it stays defensive rather
            # than raising on a shape it can't interpret.
            return errs
        max_components = get_settings().max_smes_per_fieldset
        if not (1 <= len(components) <= max_components):
            errs.append(
                f"{field_name}.components must have between 1 and {max_components} "
                f"components, got {len(components)}"
            )
        weight_sum = 0.0
        weight_sum_valid = True
        for i, comp in enumerate(components):
            if not isinstance(comp, dict):
                weight_sum_valid = False
                continue
            c_mean = comp.get("mean")
            c_sigma = comp.get("sigma")
            c_weight = comp.get("weight")
            # Finiteness FIRST for mean/sigma/weight — NaN passes any range
            # comparison («NaN <= 0» and «NaN > 10» are both False), so a NaN
            # sigma or weight would silently corrupt Monte Carlo if a range
            # check ran before the finiteness check (Sec-B1 BLOCKER; mirrors
            # the scalar lognormal branch's finite-first ordering above
            # exactly, applied per component).
            if isinstance(c_mean, (int, float)) and not isinstance(c_mean, bool):
                if not math.isfinite(c_mean):
                    errs.append(f"{field_name}.components[{i}].mean must be finite, got {c_mean!r}")
                elif c_mean > _MEANLOG_MAX:
                    errs.append(
                        f"{field_name}.components[{i}].mean (log-space meanlog) implies "
                        f"a median exp(mean) beyond the representable ceiling "
                        f"{_MAGNITUDE_MAX:g}; mean must be <= {_MEANLOG_MAX:.4f}, "
                        f"got {c_mean!r}"
                    )
            if isinstance(c_sigma, (int, float)) and not isinstance(c_sigma, bool):
                if not math.isfinite(c_sigma):
                    errs.append(
                        f"{field_name}.components[{i}].sigma must be finite, got {c_sigma!r}"
                    )
                elif c_sigma <= 0:
                    errs.append(f"{field_name}.components[{i}].sigma must be > 0, got {c_sigma!r}")
                elif c_sigma > _SIGMA_MAX:
                    errs.append(
                        f"{field_name}.components[{i}].sigma must be <= {_SIGMA_MAX}, "
                        f"got {c_sigma!r}"
                    )
            if isinstance(c_weight, (int, float)) and not isinstance(c_weight, bool):
                if not math.isfinite(c_weight):
                    errs.append(
                        f"{field_name}.components[{i}].weight must be finite, got {c_weight!r}"
                    )
                    weight_sum_valid = False
                elif c_weight <= 0:
                    errs.append(
                        f"{field_name}.components[{i}].weight must be > 0, got {c_weight!r}"
                    )
                    weight_sum_valid = False
                else:
                    weight_sum += c_weight
            else:
                weight_sum_valid = False
        if weight_sum_valid and abs(weight_sum - 1.0) > 1e-9:
            errs.append(
                f"{field_name}.components weights must sum to 1 (±1e-9), got {weight_sum!r}"
            )
        return errs
    for key in ("low", "mode", "high"):
        v = dist.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue  # non-numeric handled by fair_cam's type validation
        if not math.isfinite(v):
            errs.append(f"{field_name}.{key} must be finite, got {v!r}")
        elif abs(v) > _MAGNITUDE_MAX:
            errs.append(
                f"{field_name}.{key} magnitude exceeds the representable ceiling "
                f"{_MAGNITUDE_MAX:g}, got {v!r}"
            )
    return errs


def _p95_repr(ln_p95: float) -> str:
    """Real-space p95 for a factual error message, guarded against the
    ``OverflowError`` an extreme-but-validator-permitted stored ``mean``
    would raise from ``math.exp``. Display-only: the D19 floor comparison
    itself NEVER calls ``exp()`` (see ``_validate_capacity_floor``) -- this
    helper only formats a value that has ALREADY been compared in log space,
    so a failure here can only ever affect message prettiness, never the
    block/accept decision.
    """
    try:
        return repr(math.exp(ln_p95))
    except OverflowError:
        return f"exp({ln_p95!r}) [too large to represent as a float]"


def _validate_capacity_floor(field_name: str, dist: dict[str, Any]) -> list[str]:
    """D19 (owner-signed 2026-07-25): reject a stored PR2 capacity-bound
    ``max`` that sits at or below the loss distribution's p95.

    Scope and gating (both must hold for the check to fire; otherwise NO-OP):
    - ``max`` must be present (not ``None``). No existing row carries ``max``
      until the producer surfaces (Tasks 4a/5) mint one, so landing this
      check early changes NO existing behaviour -- proven by a full-suite
      run at this task's commit.
    - the distribution's ``distribution`` kind must be ``lognormal`` or
      ``lognormal_mixture``. Per the design (``docs/superpowers/specs/
      2026-07-25-capacity-bound-design.md`` §Problem): "``max`` is applied
      to lognormal / lognormal_mixture loss fields ONLY" -- a PERT field is
      never compared against capacity at all, so a spurious ``max`` key on
      a non-lognormal kind is inert here, not an error.

    Compares in LOG SPACE ONLY: reject when ``ln(max) <= meanlog +
    z95*sigma``. This function and its mixture branch NEVER compute
    ``exp(meanlog + z95*sigma)`` to perform the comparison -- an
    operator-entered ``mean`` need not be small (D17 lets an analyst type
    an explicit cap; nothing bounds the paired mean/sigma at this layer),
    and ``math.exp`` of a large-but-finite log-space value raises
    ``OverflowError`` (500), not a validation error. ``z95`` is
    ``_Z95 = float(norm.ppf(0.95))``, the same constant used everywhere
    else in the codebase a p95 is read off a lognormal.

    Mixture semantics (the crux -- get this reading wrong and
    ``B-CAP-MIX``'s published mixture-deviation bounds are void): the
    accept condition is ``max > max_i p95_i`` -- i.e. reject if ``max`` is
    at or below EVERY SINGLE component's own p95 (equivalently: reject if
    ANY component's p95 is at or above ``max``). This is DELIBERATELY NOT
    the largest-meanlog component (contrast
    ``run_executor._validated_capacity_bound``, a separate, median-based,
    read-adapter underflow guard that correctly uses the largest-meanlog
    component for ITS OWN purpose -- medians are ``exp(mu_i)``, so the
    largest-meanlog component has the largest median). p95 is
    sigma-driven, not just mu-driven, and each mixture component carries
    an INDEPENDENT sigma: ``argmax_i mu_i`` need not be ``argmax_i p95_i``.
    A component with a smaller meanlog but a much larger sigma can have the
    largest p95 and be the binding one. Using the largest-meanlog
    component here would silently drop the floor for every other
    component.

    Sec-L8/#84 ceiling: a finite ``max`` above ``_MAGNITUDE_MAX`` is ALSO
    rejected here (collected alongside the floor verdict, not
    short-circuiting it) -- ``max`` is the upper bound actually enforced on
    lognormal draws, so it must itself be float32-representable.

    Malformed input -- missing/non-numeric ``mean``/``sigma``,
    non-list/empty/non-dict ``components``, or a non-numeric/non-finite/
    non-positive ``max`` -- returns an error string rather than raising or
    letting an exception (``TypeError``/``ValueError`` from ``math.log`` of
    a non-positive value, or iterating a non-list) escape. The caller
    (``validate_fair_distributions``) folds every returned string into ONE
    ``FAIRCAMValidationError`` (-> 422), so malformed input here is always a
    validation error, never a 500. This is intentionally STRICTER than
    ``_validate_finite``'s own non-list-``components`` handling (which
    defers to the import-path shape gate and stays silent) -- THAT gate
    runs unconditionally on every write, so it can afford to defer; THIS
    check only activates when ``max`` is present, and once active it cannot
    safely skip a components shape it cannot interpret, because "cannot
    interpret" and "the floor holds" are not the same claim. Fails closed.

    Only the FACTUAL error string (p95 vs cap) is produced here. Surface
    remedy copy (three actionable alternatives for the operator) is added
    by the three producer surfaces (Tasks 4a/4b/4c), which wrap this error.
    """
    if dist.get("max") is None:
        return []
    kind = str(dist.get("distribution", "pert")).lower()
    if kind not in ("lognormal", "lognormal_mixture"):
        return []  # D19 floor governs lognormal loss shapes only

    max_raw = dist["max"]
    if not isinstance(max_raw, (int, float)) or isinstance(max_raw, bool):
        return [
            f"{field_name}.max must be numeric to validate the max > p95 floor, got {max_raw!r}"
        ]
    if not math.isfinite(max_raw) or max_raw <= 0:
        return [
            f"{field_name}.max must be a finite positive number to validate the max > p95 "
            f"floor, got {max_raw!r}"
        ]
    # Sec-L8/#84 ceiling: the capacity cap is the upper bound actually
    # enforced on lognormal draws (truncated_lognormal clips at ``max``),
    # so it must itself be float32-representable — a max above the codec
    # ceiling clips nothing into representable range and the stored run
    # would still overflow on encode. COLLECTED, not short-circuited: the
    # floor comparison below still runs (it is log-space and finite-safe for
    # any finite float64 max), so the huge-max tests that pin the
    # no-OverflowError floor behaviour keep exercising it, and the operator
    # sees both defects in one 422 batch.
    ceiling_errs: list[str] = []
    if max_raw > _MAGNITUDE_MAX:
        ceiling_errs.append(
            f"{field_name}.max magnitude exceeds the representable ceiling "
            f"{_MAGNITUDE_MAX:g}, got {max_raw!r}"
        )
    ln_max = math.log(float(max_raw))

    if kind == "lognormal":
        mean = dist.get("mean")
        sigma = dist.get("sigma")
        if not isinstance(mean, (int, float)) or isinstance(mean, bool):
            return [
                *ceiling_errs,
                f"{field_name}.mean must be numeric to validate the max > p95 floor, got {mean!r}",
            ]
        if not isinstance(sigma, (int, float)) or isinstance(sigma, bool):
            return [
                *ceiling_errs,
                f"{field_name}.sigma must be numeric to validate the max > p95 floor, got {sigma!r}",
            ]
        if not math.isfinite(mean) or not math.isfinite(sigma):
            # Non-finite mean/sigma is already rejected by _validate_finite
            # in the same error batch -- skip here rather than emit a
            # second, less-precise message about the same root cause.
            return ceiling_errs
        ln_p95 = float(mean) + _Z95 * float(sigma)
        if ln_max <= ln_p95:
            return [
                *ceiling_errs,
                f"{field_name}.max={max_raw!r} must exceed the distribution's p95 "
                f"({_p95_repr(ln_p95)}); a cap at or below the p95 violates the D19 "
                f"capacity floor",
            ]
        return ceiling_errs

    # kind == "lognormal_mixture"
    components = dist.get("components")
    if not isinstance(components, list) or not components:
        return [
            *ceiling_errs,
            f"{field_name}.components must be a non-empty list to validate the max > p95 "
            f"floor, got {components!r}",
        ]
    errs: list[str] = []  # component malformations only; ceiling_errs composed at terminals
    worst_ln_p95: float | None = None
    worst_i: int | None = None
    for i, comp in enumerate(components):
        if not isinstance(comp, dict):
            errs.append(
                f"{field_name}.components[{i}] must be an object to validate the max > p95 "
                f"floor, got {comp!r}"
            )
            continue
        c_mean = comp.get("mean")
        c_sigma = comp.get("sigma")
        if not isinstance(c_mean, (int, float)) or isinstance(c_mean, bool):
            errs.append(
                f"{field_name}.components[{i}].mean must be numeric to validate the max > "
                f"p95 floor, got {c_mean!r}"
            )
            continue
        if not isinstance(c_sigma, (int, float)) or isinstance(c_sigma, bool):
            errs.append(
                f"{field_name}.components[{i}].sigma must be numeric to validate the max > "
                f"p95 floor, got {c_sigma!r}"
            )
            continue
        if not math.isfinite(c_mean) or not math.isfinite(c_sigma):
            continue  # _validate_finite already raises for this component
        ln_p95_i = float(c_mean) + _Z95 * float(c_sigma)
        # EVERY component walked via max() over ln_p95_i (an adapter-iter
        # contract: no component is skipped, mirroring
        # _dict_to_fair_distribution's own "walks EVERY component" note) --
        # this is the every-component reading, NOT an argmax over c_mean.
        if worst_ln_p95 is None or ln_p95_i > worst_ln_p95:
            worst_ln_p95 = ln_p95_i
            worst_i = i
    if errs:
        return ceiling_errs + errs
    if worst_ln_p95 is not None and ln_max <= worst_ln_p95:
        return [
            *ceiling_errs,
            f"{field_name}.max={max_raw!r} must exceed EVERY component's p95 "
            f"(components[{worst_i}] p95={_p95_repr(worst_ln_p95)} is the binding one); "
            f"a cap at or below any component's p95 violates the D19 capacity floor",
        ]
    return ceiling_errs


def _validate_loss_max_required(field_name: str, dist: dict[str, Any]) -> list[str]:
    """D15 (Task 6): reject a lognormal/lognormal_mixture LOSS field that
    carries no ``max`` at all, when the caller opts in via
    ``require_loss_max=True`` on ``validate_fair_distributions``.

    PRESENCE check only -- deliberately separate from
    ``_validate_capacity_floor``'s MAGNITUDE check (``max > p95``) just
    above. "No usable max" mirrors that function's own definition of
    absence exactly (``dist.get("max") is None``): a malformed-but-present
    ``max`` (0, negative, inf, nan, a string, a bool) is NOT a requiredness
    failure -- it is caught by ``_validate_capacity_floor``'s own
    type/finite/positive guards, which run unconditionally in the same
    error batch regardless of ``require_loss_max``. Two separate concerns,
    two separate functions; no duplicate floor logic here.

    PERT (the distribution-kind default) is out of scope: only
    lognormal/lognormal_mixture loss fields are ever compared against
    capacity at all (D19), and per D12 (lognormal is strictly a loss
    distribution) TEF/vulnerability never reach this function in the
    first place -- callers only invoke this for primary_loss/secondary_loss.
    """
    kind = str(dist.get("distribution", "pert")).lower()
    if kind not in ("lognormal", "lognormal_mixture"):
        return []
    if dist.get("max") is None:
        return [
            f"{field_name}.max is required for a {kind} loss distribution "
            "(D15 capacity-bound requiredness) but was not provided"
        ]
    return []


@dataclass(frozen=True)
class FAIRCAMValidationResult:
    """Returned when validation passes (severity == ERROR raises, not returns).

    ``errors`` is always [] on the returned path — errors raise FAIRCAMValidationError.
    ``warnings`` is a list of ValidationResult objects for template flash rendering.
    ``info`` carries informational results (not typically rendered).
    """

    errors: list[ValidationResult] = field(
        default_factory=list
    )  # always empty on return path; ERROR severity raises instead. Forward-compat for soft-error mode.
    warnings: list[ValidationResult] = field(default_factory=list)
    info: list[ValidationResult] = field(default_factory=list)


def validate_fair_distributions(
    *,
    threat_event_frequency: dict[str, Any],
    vulnerability: dict[str, Any] | None,
    primary_loss: dict[str, Any],
    secondary_loss: dict[str, Any] | None,
    require_loss_max: bool = False,
) -> FAIRCAMValidationResult:
    """Validate the four FAIR distributions through fair_cam's validator.

    Raises FAIRCAMValidationError if any result has severity == ERROR.
    Returns FAIRCAMValidationResult with non-blocking warnings otherwise.

    ``vulnerability`` is optional; passing it is forward-compatible with
    future fair_cam vulnerability validation without breaking callers that
    already pass it via keyword (services/scenarios.py).

    ``require_loss_max`` (D15, Task 6): when True, a lognormal/
    lognormal_mixture ``primary_loss``/``secondary_loss`` MUST carry a
    ``max`` key (see ``_validate_loss_max_required``) -- a presence check,
    additive to (not a replacement of) the ``_validate_capacity_floor``
    magnitude check below. Default False. Only the scenario-write and
    scenario-import call sites opt in (verified caller census):
    ``services/scenarios.py`` lines 162/507/605 (create / update /
    re-estimate) and ``services/scenario_import.py`` line 427 (CSV/JSON
    import apply). ``services/library_bundle_import.py`` line 278 and
    ``services/scenario_library.py`` line 375 (override writes) stay on
    the default False (D14) -- those callers author org-agnostic
    templates/overrides that carry no ``max``, and flipping them would
    reject every existing library/override write.
    """
    risk_data: dict[str, Any] = {
        "threat_event_frequency": threat_event_frequency,
        "primary_loss": primary_loss,
    }
    if secondary_loss is not None:
        risk_data["secondary_loss"] = secondary_loss
    if vulnerability is not None:
        risk_data["vulnerability"] = vulnerability

    # Meth-B1: reject non-finite (inf / nan) low/mode/high in the unbounded-above
    # distributions BEFORE handing to fair_cam — its validators let inf through
    # (inf < 0 is False; low ≤ mode ≤ inf holds), so an infinite value would be
    # durably stored and corrupt pyfair Monte Carlo. Placing it in
    # validate_fair_distributions closes BOTH the import path AND the
    # form-create path (same rationale as the vulnerability block below).
    _finite_errors: list[str] = []
    _finite_errors += _validate_finite("threat_event_frequency", threat_event_frequency)
    _finite_errors += _validate_finite("primary_loss", primary_loss)
    if secondary_loss is not None:
        _finite_errors += _validate_finite("secondary_loss", secondary_loss)
    # D12 (owner, 2026-07-25): "lognormal is strictly a loss distribution."
    # lognormal / lognormal_mixture are permitted ONLY on primary/secondary
    # loss; TEF and vulnerability are PERT-only in v3 storage. Enforced HERE
    # (not only at the import allow-tables) because this chokepoint covers
    # every write path — direct create, update, wizard finalize, scenario
    # import, and library-bundle import. Kind resolution mirrors
    # run_executor._dict_to_fair_distribution: the key is optional
    # (defaults PERT — prod vulnerability dicts carry no kind key) and
    # matching is case-insensitive.
    for _fname, _dist in (
        ("threat_event_frequency", threat_event_frequency),
        ("vulnerability", vulnerability),
    ):
        if not isinstance(_dist, dict):
            continue
        _kind = str(_dist.get("distribution", "pert")).lower()
        if _kind in ("lognormal", "lognormal_mixture"):
            _finite_errors.append(
                f"{_fname}.distribution {_kind} not allowed: lognormal is "
                "strictly a loss distribution (TEF and vulnerability are PERT-only)"
            )
    # D15 (Task 6): requiredness -- opt-in only (see docstring). A presence
    # check, evaluated BEFORE the floor's magnitude check below so a
    # completely max-less lognormal loss gets the clearer "max is required"
    # message rather than silently NO-OP'ing through the floor too.
    if require_loss_max:
        _finite_errors += _validate_loss_max_required("primary_loss", primary_loss)
        if secondary_loss is not None:
            _finite_errors += _validate_loss_max_required("secondary_loss", secondary_loss)
    # D19 (Task 3b): the `max > p95` floor -- loss fields only (the only
    # fields lognormal/lognormal_mixture are allowed on, per D12 above).
    # NO-OP until `max` is present on a stored dict (Tasks 4a/5 mint it).
    _finite_errors += _validate_capacity_floor("primary_loss", primary_loss)
    if secondary_loss is not None:
        _finite_errors += _validate_capacity_floor("secondary_loss", secondary_loss)
    if _finite_errors:
        raise FAIRCAMValidationError(
            "FAIRCAM validation failed: " + "; ".join(_finite_errors),
            errors=[(e.split(".", 1)[0], None) for e in _finite_errors],
        )

    summary = _FAIR_CAM_VALIDATOR.validate_risk_parameters(risk_data)

    # B1 (Meth-B1): fair_cam's validate_risk_parameters does NOT validate
    # vulnerability. Vulnerability is a probability in [0,1]; enforce it here
    # in the v3 wrapper so BOTH import and form-create paths reject impossible
    # values. (Placed in v3, not fair_cam, per "fix at the consuming layer".)
    if vulnerability is not None:
        _vuln_errors = _validate_vulnerability(vulnerability)
        if _vuln_errors:
            raise FAIRCAMValidationError(
                "FAIRCAM validation failed: " + "; ".join(_vuln_errors),
                errors=[("vulnerability", None)],  # match the (field, result) shape; None result ok
            )

    errors: list[ValidationResult] = []
    warnings: list[ValidationResult] = []
    info: list[ValidationResult] = []
    for result in summary.results:
        if result.severity == ValidationSeverity.ERROR:
            errors.append(result)
        elif result.severity == ValidationSeverity.WARNING:
            warnings.append(result)
        elif result.severity == ValidationSeverity.INFO:
            info.append(result)
        # ValidationSeverity.SUCCESS: drop on the floor.

    if errors:
        msg = "; ".join(f"{r.field_name}: {r.message}" for r in errors)
        raise FAIRCAMValidationError(
            f"FAIRCAM validation failed: {msg}",
            errors=[(r.field_name, r) for r in errors],
        )
    return FAIRCAMValidationResult(warnings=warnings, info=info)
