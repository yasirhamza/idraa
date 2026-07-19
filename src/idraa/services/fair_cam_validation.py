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

    PERT/uniform/triangular: low/mode/high must be finite.
    Lognormal: mean must be finite; sigma must be finite AND 0 < sigma <= 10
    (an unbounded right tail makes a non-finite mean or non-positive sigma
    catastrophic; an extreme-but-finite sigma is a user-controllable OOM/DoS
    path to the engine sampler at the 100k cap — Sec-I2. sigma=10 already spans
    ~17 orders of magnitude p5->p95, beyond any defensible cyber-loss range).
    lognormal_mixture: same per-component finiteness + sigma bound as
    lognormal, PLUS weight > 0 per component, weights summing to 1 (±1e-9),
    and 1 <= len(components) <= Settings.max_smes_per_fieldset (Sec-N1: the
    component count is deliberately coupled to the same cap that already
    bounds SME-estimate fan-out into the wizard finalize pipeline — a mixture
    can never carry more components than a single fieldset could ever
    legitimately produce).
    """
    errs: list[str] = []
    kind = str(dist.get("distribution", "pert")).lower()
    if kind == "lognormal":
        mean = dist.get("mean")
        sigma = dist.get("sigma")
        if (
            isinstance(mean, (int, float))
            and not isinstance(mean, bool)
            and not math.isfinite(mean)
        ):
            errs.append(f"{field_name}.mean must be finite, got {mean!r}")
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
            if (
                isinstance(c_mean, (int, float))
                and not isinstance(c_mean, bool)
                and not math.isfinite(c_mean)
            ):
                errs.append(f"{field_name}.components[{i}].mean must be finite, got {c_mean!r}")
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
    return errs


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
) -> FAIRCAMValidationResult:
    """Validate the four FAIR distributions through fair_cam's validator.

    Raises FAIRCAMValidationError if any result has severity == ERROR.
    Returns FAIRCAMValidationResult with non-blocking warnings otherwise.

    ``vulnerability`` is optional; passing it is forward-compatible with
    future fair_cam vulnerability validation without breaking callers that
    already pass it via keyword (services/scenarios.py).
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
