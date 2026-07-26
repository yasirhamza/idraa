"""View-model builder for the run-detail page.

Pure transformation: takes a RiskAnalysisRun ORM row (with simulation_results
JSON pre-loaded) and returns a dict consumed by templates/runs/_results_panel.html.

Mirrors services/run_executor.py:_build_results_payload on the read side:
the executor converts fair-cam DTOs → persisted dict; this module converts
persisted dict → template view-model.

No DB, no HTTP imports at module level — pure dict/list manipulation. ONE
exception (PR2 Task 8b): ``_lognormal_retention`` performs a
FUNCTION-LOCAL import of ``fair_cam.quantile_pooling.truncated_lognormal_mean``
/ ``lognormal_mean`` — the truncated/untruncated mean-ratio identity used
here is FAIR math (fair_cam is CLAUDE.md's single source of truth for it),
so importing the shared kernel de-duplicates the formula rather than
re-deriving it inline a second time (``idraa.app.lognormal_display_rows``
imports the SAME kernel, the same function-local way, for its own
web-display rendering). The import is deliberately function-local, NOT
module-level: ``services/reports.py`` imports this module's
``_build_control_effectiveness_rows`` at ITS OWN module level, and
``services/pdf_report.py`` imports ``services/reports.py`` at module
level — a module-level fair_cam import here would transitively land
fair_cam in ``sys.modules`` on a bare ``import idraa.services.pdf_report``,
breaking that module's test-enforced fair_cam-free purity boundary
(``tests/unit/test_pdf_report.py::test_pdf_report_purity_...``). The
local-import style is this module's own established idiom already used
throughout ``fair_cam/quantile_pooling/_lognormal_native.py`` ("local
import keeps module import cheap") — not a workaround invented for this
constraint.

Currency: callers pass ``rc`` (a ReportingCurrency from
``services/reporting_currency.py``).  The default is ``_USD_IDENTITY``
(no conversion) so un-threaded callers stay correct.  Templates and
formatters receive already-converted values and only format; they never
multiply by a rate again (convert-once invariant).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from babel.numbers import get_currency_symbol

# Was: def _strip_samples(...) and def _has_ci_band(...)
# Now (preserve underscore aliases for PR nu backward compat):
from idraa.services._view_model_helpers import (
    DIST_STATS_DEFINITIONAL_NOTE,  # Task 1 (#353): defined in _view_model_helpers to avoid circular import
    _build_tail_risk,  # T2 (#351): moved to _view_model_helpers; re-exported here for backward compat
    build_dist_stats_rows,  # Task 1 (#353): shared row builder
    if_removed_by_control_single,  # leave-one-out "if removed" SINGLE-run lookup (display plumbing)
    process_weight_robustness_for_display,  # Task 5 (#419): weight-robustness display helper
    snapshot_sub_functions_by_id,  # Issue #436: sub-function lookup for zero-reason labels
)
from idraa.services._view_model_helpers import (
    has_ci_band as _has_ci_band,
)
from idraa.services._view_model_helpers import (
    strip_samples as _strip_samples,
)
from idraa.services.reporting_currency import ReportingCurrency

__all__ = [
    "_build_capacity_cap_note",
    "_build_control_effectiveness_rows",
    "_build_headline_ale",
    "_build_risk_comparison",
    "_build_tail_risk",
    "_field_mean_and_retention",
    "_has_ci_band",
    "_lognormal_retention",
    "_safe_display_max",
    "_strip_samples",
    "build_display_results",
]

# USD identity: no conversion, no provenance — used as default so callers
# that do not yet thread rc are correct-by-default.
_USD_IDENTITY = ReportingCurrency("USD", Decimal("1"), is_pinned=True, provenance=None)


def _convert_risk_dict(risk: dict[str, Any], rc: ReportingCurrency) -> dict[str, Any]:
    """Return a copy of a persisted risk dict with all money fields converted.

    Money keys: annualized_loss_expectancy, mean, median, std_deviation,
    var_90/95/99/999, expected_shortfall.{es_95/99/999},
    expected_shortfall_se.{es_95/99/999} (Task 10 — the ES Monte Carlo
    standard error is in the SAME money units as expected_shortfall, so it
    converts identically; ``None`` values — legacy-absent per-level entries
    or the "insufficient tail samples" sentinel — pass through unconverted).
    Non-money keys (e.g. loss_event_frequency, n_simulations) pass through.
    """
    if rc.code == "USD":
        return risk  # identity — no copy needed
    out = dict(risk)
    for key in (
        "annualized_loss_expectancy",
        "mean",
        "median",
        "std_deviation",
        "var_90",
        "var_95",
        "var_99",
        "var_999",
    ):
        if key in out and out[key] is not None:
            out[key] = rc.convert(out[key])
    es = out.get("expected_shortfall")
    if isinstance(es, dict):
        new_es: dict[str, Any] = {}
        for ekey, ev in es.items():
            new_es[ekey] = rc.convert(ev) if ev is not None else ev
        out["expected_shortfall"] = new_es
    # Task 10 (Spec-B1): same wholesale-conversion treatment for the ES SE
    # sibling dict — additive key, ABSENT entirely on legacy rows (out.get
    # returns None and the isinstance guard skips it, so nothing crashes or
    # double-converts on those rows).
    es_se = out.get("expected_shortfall_se")
    if isinstance(es_se, dict):
        new_es_se: dict[str, Any] = {}
        for ekey, ev in es_se.items():
            new_es_se[ekey] = rc.convert(ev) if ev is not None else ev
        out["expected_shortfall_se"] = new_es_se
    return out


def _convert_curve_losses(
    points: list[dict[str, Any]], rc: ReportingCurrency
) -> list[dict[str, Any]]:
    """Convert the ``loss`` field in each LEC/EPC point; leave other fields."""
    if rc.code == "USD":
        return points
    return [{**p, "loss": rc.convert(p["loss"])} if "loss" in p else p for p in points]


def _currency_meta(rc: ReportingCurrency) -> dict[str, str]:
    """Build the ``currency`` sub-dict for the view-model top level."""
    from idraa.currency import APP_LOCALE

    symbol = get_currency_symbol(rc.code, locale=APP_LOCALE)
    return {"code": rc.code, "symbol": symbol}


def build_display_results(run: Any, rc: ReportingCurrency = _USD_IDENTITY) -> dict[str, Any] | None:
    """Convert run.simulation_results JSON to a template view-model dict.

    Returns None when run.simulation_results is None (PENDING / RUNNING /
    FAILED-before-persist runs). The caller (route layer) treats None as
    "show status panel only; hide results panel".

    ``rc`` defaults to USD identity so callers that do not yet thread a
    ReportingCurrency remain correct.  All money values are converted once
    here; templates only format (convert-once invariant).
    """
    if run.simulation_results is None:
        return None

    sr = run.simulation_results
    base_raw = _strip_samples(sr.get("base_risk", {}))
    residual_raw = _strip_samples(sr.get("residual_risk", {}))
    ci_raw = sr.get("confidence_intervals", {})
    adjustments = sr.get("control_adjustments", [])
    snapshot = run.controls_snapshot or []

    # Convert all money fields at the boundary — templates format only.
    base = _convert_risk_dict(base_raw, rc)
    residual = _convert_risk_dict(residual_raw, rc)

    # CI bounds are money (dollar loss bounds).
    if rc.code != "USD" and ci_raw:
        ci: dict[str, Any] = dict(ci_raw)
        ci["lower_bound"] = rc.convert(ci_raw.get("lower_bound", 0.0))
        ci["upper_bound"] = rc.convert(ci_raw.get("upper_bound", 0.0))
    else:
        ci = ci_raw

    lec = _convert_curve_losses(sr.get("loss_exceedance_curve", []), rc)
    epc = _convert_curve_losses(sr.get("exceedance_probability_curve", []), rc)

    # Task 7 (#436): single-run availability flag for "No detection partner" suppression.
    # SINGLE runs have exactly one scenario; AGGREGATE mixed-effect is deferred → False.
    _snap_scenarios = (getattr(run, "scenario_inputs_snapshot", None) or {}).get("scenarios") or []
    _snap0 = _snap_scenarios[0] if _snap_scenarios else {}  # adapter-iter: ok — non-empty guard
    _availability_effect = len(_snap_scenarios) == 1 and _snap0.get("effect") == "availability"

    # Mean+typical side-by-side (2026-07-04): the "if removed" primary figure
    # switches basis with the run's weight_robustness blob (new runs are
    # basis=="mean"; legacy blobs have no "basis" key -> "typical" default).
    # Build BOTH lookups from the flat control_adjustments passthrough and pick
    # primary/secondary by basis so legacy runs render exactly as before (typical
    # primary, no secondary sub-line).
    _wr_raw = getattr(run, "weight_robustness", None)
    _basis = (_wr_raw or {}).get("basis", "typical")
    _ir_typical = if_removed_by_control_single(adjustments, key="if_removed_value")
    if _basis == "mean":
        _ir_primary = if_removed_by_control_single(adjustments, key="if_removed_value_mean")
        _ir_secondary: dict[str, float | None] | None = _ir_typical
    else:
        _ir_primary = _ir_typical
        _ir_secondary = None

    return {
        "headline_ale": _build_headline_ale(residual, ci),
        "risk_comparison": _build_risk_comparison(base, residual),
        "control_effectiveness_rows": _build_control_effectiveness_rows(adjustments, snapshot),
        "base_risk": base,
        "residual_risk": residual,
        "confidence_intervals": ci,
        "loss_exceedance_curve": lec,
        "exceedance_probability_curve": epc,
        # #266 D1: tail-risk summary (p90/p99.9 VaR + Expected Shortfall) for
        # the residual side, surfaced top-level for the detail page. Uses .get()
        # throughout so OLD persisted runs (which lack these keys) render zeros
        # instead of raising. See _build_tail_risk for the p99.9 reliability
        # caveat at the 10k iteration default.
        # T2 (#351): _build_tail_risk moved to _view_model_helpers; re-exported above.
        # residual is already converted above — tail values are already in rc.
        "tail_risk": _build_tail_risk(residual),
        # Task 1 (#353): 10-row distribution-stats + tail-risk ladder (base vs residual).
        # Consumes converted base + residual so dist_stats rows are in rc currency.
        "dist_stats": build_dist_stats_rows(base, residual),
        "dist_stats_note": DIST_STATS_DEFINITIONAL_NOTE,
        # P3 currency metadata — templates use these for formatting labels.
        "currency": _currency_meta(rc),
        "currency_provenance": rc.provenance,
        # Task 5 (#419): weight-robustness display data (converted to reporting currency).
        # None on legacy runs without weight_robustness column or before Task 4 landed.
        # Issue #436: pass sub-function lookup so per-control $0 cells get a reason label.
        # Task 7 (#436): compute availability flag from single-run snapshot so "No detection
        # partner" is suppressed for recovery controls in availability scenarios (self-detect,
        # FAIR-CAM §3.3.2 p.19). AGGREGATE calls keep the default False (mixed-effect deferred).
        "weight_robustness": process_weight_robustness_for_display(
            getattr(run, "weight_robustness", None),
            rc.convert,
            rc.code,
            sub_functions_by_id=snapshot_sub_functions_by_id(snapshot),
            availability_effect=_availability_effect,
            # Leave-one-out "if removed" (display plumbing): SINGLE reads the flat
            # control_adjustments passthrough (run_executor.py's _inject_loo).
            # Mean+typical side-by-side: primary basis-selected above; secondary
            # (typical) paired ONLY when the primary is mean-basis.
            if_removed_by_control=_ir_primary,
            if_removed_by_control_typical=_ir_secondary,
        ),
        # PR2 Task 8 (D16): per-scenario capacity-cap disclosure. None on
        # legacy runs (no snapshot), and None when nothing in the scenario's
        # PL/SL is actually capped -- see _build_capacity_cap_note.
        "capacity_cap_note": _build_capacity_cap_note(run, rc),
    }


def _build_headline_ale(residual: dict[str, Any], ci: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": residual.get("annualized_loss_expectancy", 0.0),
        "lo": ci.get("lower_bound", 0.0),
        "hi": ci.get("upper_bound", 0.0),
        "has_ci_band": _has_ci_band(ci),
    }


def _build_risk_comparison(base: dict[str, Any], residual: dict[str, Any]) -> dict[str, Any]:
    b = base.get("annualized_loss_expectancy", 0.0)
    r = residual.get("annualized_loss_expectancy", 0.0)
    reduction = b - r
    reduction_pct: float | None = reduction / b * 100 if b > 0 else None
    return {
        "base": b,
        "residual": r,
        "reduction": reduction,
        "reduction_pct": reduction_pct,
    }


def _build_control_effectiveness_rows(
    adjustments: list[dict[str, Any]],
    snapshot: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join control_adjustments → controls_snapshot for friendly names.

    v1, v2, and v3 snapshot shapes carry control_id + name at top level
    (verified in schemas/run_snapshot.py); the join works uniformly.

    Sort is stable: effectiveness DESC, name ASC for ties (deterministic
    test output across page reloads).

    A control_id present in adjustments but absent from snapshot (data
    drift) renders with name "(unknown)" — visible defect signal, not a
    silent drop.
    """
    name_by_id = {c["control_id"]: c["name"] for c in snapshot}
    rows = [
        {
            "control_id": adj["control_id"],
            "name": name_by_id.get(adj["control_id"], "(unknown)"),
            "effectiveness": adj.get("effectiveness", 0.0),
        }
        for adj in adjustments
    ]
    rows.sort(key=lambda r: (-r["effectiveness"], r["name"]))
    return rows


# ---------------------------------------------------------------------------
# PR2 Task 8 (D16): per-scenario capacity-cap disclosure.
#
# Reads the AS-EXECUTED loss dicts from run.scenario_inputs_snapshot (T2/#351)
# -- never the live scenario row, which can drift after the run completes.
# Computed ANALYTICALLY (never a second Monte Carlo simulation) and scoped to
# SINGLE-run detail: build_display_results (this module) is only ever called
# for RunType.SINGLE (see routes/runs.py::_build_display_context); AGGREGATE
# runs use build_aggregate_display_results, a separate view-model that does
# not read the per-scenario snapshot and is explicitly out of scope here (a
# different view-model would need its own wiring -- see the PR body).
# ---------------------------------------------------------------------------


def _dist_kind(d: dict[str, Any]) -> str:
    """Case-insensitive distribution kind; an absent key defaults to PERT.

    Mirrors run_executor._dict_to_fair_distribution's own kind resolution
    (``str(payload.get("distribution", "pert")).lower()``). The prod backup
    stores ``'PERT'`` UPPERCASE on 31 of 40 loss dicts (B-CAP-BASIS) -- a
    naive ``== "pert"`` comparison silently drops every one of those fields
    from the weighted sums below (reweighting the scenario composition) and
    ``KeyError``s where the key is entirely absent. In-repo precedent for
    this exact defect class: services/reports.py's UPPERCASE/lowercase
    bucket-key bug (issue #90 Task 6.5).
    """
    return str(d.get("distribution", "pert")).lower()


def _lognormal_retention(meanlog: float, sigma: float, cap: float) -> float:
    """Truncated/untruncated mean ratio for ONE lognormal shape.

    ``R_f = truncated_lognormal_mean(meanlog, sigma, cap) /
    lognormal_mean(meanlog, sigma)`` -- identically ``Phi(b - sigma) /
    Phi(b)``, ``b = (ln(cap) - meanlog) / sigma``, since both call sites
    share the same ``exp(meanlog + sigma**2/2)`` factor that cancels
    algebraically. PR2 Task 8b: delegates to the shared fair_cam kernel
    (``fair_cam.quantile_pooling.truncated_lognormal_mean`` /
    ``lognormal_mean``) rather than re-deriving the ``scipy.special.ndtr``
    arithmetic inline a second time -- ``idraa.app.lognormal_display_rows``
    imports the SAME kernel for its own web-display rendering, so the
    truncated-mean math now lives in exactly one place. Matches the sampler
    (fair_cam.risk_engine._truncation) and the store-time validator
    (services.fair_cam_validation._validate_capacity_floor).

    D19 guarantees ``cap`` is comfortably above this field's p95 for any row
    written through the validated producers (Task 3b), so ``b`` is
    comfortably positive and the kernel's internal ``Phi(b)`` is close to
    1.0 in practice. This function still guards the degenerate case -- a
    pre-D19 legacy row or a raw-SQL-written snapshot could carry a cap at
    or below the field's own shape, driving ``b`` deeply negative until
    ``Phi(b)`` underflows to EXACTLY 0.0 (not a small positive float, per
    the same footgun documented in run_executor._validated_capacity_bound)
    -- the kernel raises ``ValueError`` in that case rather than returning
    a division artifact. A disclosure surface must never 500 a run-detail
    page or divide by zero: a non-positive sigma, a non-finite/non-positive
    cap, or the kernel's underflow ``ValueError`` are all caught here and
    treated as the cap being NON-BINDING for this field (R_f = 1.0) rather
    than raised or propagated.
    """
    if sigma <= 0.0 or not math.isfinite(cap) or cap <= 0.0:
        return 1.0
    # Function-local import (see module docstring): keeps fair_cam out of
    # this module's own module-level import graph, so services/reports.py's
    # module-level import of _build_control_effectiveness_rows (below)
    # cannot transitively land fair_cam in services/pdf_report.py's
    # test-enforced fair_cam-free sys.modules check.
    from fair_cam.quantile_pooling import lognormal_mean, truncated_lognormal_mean

    try:
        truncated_mean = truncated_lognormal_mean(meanlog, sigma, cap)
    except ValueError:
        # The kernel raises when Phi(b) underflows to exactly 0.0 (cap far
        # below this field's own core) -- the same degenerate case the
        # pre-refactor inline arithmetic caught via `denom <= 0.0`.
        return 1.0
    denom = lognormal_mean(meanlog, sigma)
    if not math.isfinite(denom) or denom <= 0.0:
        return 1.0
    ratio = truncated_mean / denom
    if not math.isfinite(ratio):
        return 1.0
    # Both Phi's saturate towards 1.0 for large b; clamp so floating-point
    # noise can never push the ratio a hair above 1.0 (which would make a
    # downstream `1 - R` go negative).
    return min(1.0, ratio)


def _field_mean_and_retention(d: dict[str, Any] | None) -> tuple[float, float]:
    """Return ``(E_f, R_f)`` for one PL/SL loss field dict.

    ``E_f`` is the field's UNTRUNCATED parent mean; ``R_f`` is the truncated/
    untruncated mean ratio (1.0 when the field carries no ``max``, or is not
    a lognormal-family kind). PERT fields never carry a ``max`` (R_f = 1.0
    always) but still contribute their ``E_f`` -- dropping a PERT field here
    would silently reweight any mixed PERT-PL/lognormal-SL scenario, which is
    the norm in the catastrophic library (both kinds must appear in BOTH the
    numerator and denominator of the caller's composition).

    Mixture fields (``lognormal_mixture``) get their own kernel -- no
    in-repo precedent exists for this (scripts/capacity_bound_figures.py's
    ``_dist_mean`` handles only lognormal + PERT and SystemExits on anything
    else): ``R_f = sum_i(w_i * m_i * R_i) / sum_i(w_i * m_i)``, with
    ``m_i = exp(mu_i + sigma_i**2/2)`` and ``R_i`` from ``_lognormal_retention``
    using the SHARED top-level cap but each component's OWN (mu_i, sigma_i)
    -- a component-independent single-field formula does not apply here.

    Malformed field shapes (missing/non-numeric keys, an unknown
    distribution kind) contribute ``(0.0, 1.0)`` -- excluded from both sums
    rather than guessed at or allowed to raise on a disclosure-only surface.
    """
    if not d:
        return 0.0, 1.0
    kind = _dist_kind(d)
    if kind == "pert":
        try:
            low, mode, high = float(d["low"]), float(d["mode"]), float(d["high"])
        except (KeyError, TypeError, ValueError):
            return 0.0, 1.0
        return (low + 4.0 * mode + high) / 6.0, 1.0
    if kind == "lognormal":
        try:
            meanlog, sigma = float(d["mean"]), float(d["sigma"])
        except (KeyError, TypeError, ValueError):
            return 0.0, 1.0
        e_f = math.exp(meanlog + sigma**2 / 2.0)
        cap_raw = d.get("max")
        if cap_raw is None:
            return e_f, 1.0
        try:
            cap = float(cap_raw)
        except (TypeError, ValueError):
            return e_f, 1.0
        return e_f, _lognormal_retention(meanlog, sigma, cap)
    if kind == "lognormal_mixture":
        components = d.get("components")
        if not isinstance(components, list) or not components:
            return 0.0, 1.0
        mix_cap_raw = d.get("max")
        mix_cap: float | None
        if mix_cap_raw is None:
            mix_cap = None
        else:
            try:
                mix_cap = float(mix_cap_raw)
            except (TypeError, ValueError):
                mix_cap = None
        weighted_mean = 0.0
        weighted_retained = 0.0
        for component in components:
            if not isinstance(component, dict):
                continue
            try:
                w_i = float(component["weight"])
                mu_i = float(component["mean"])
                sigma_i = float(component["sigma"])
            except (KeyError, TypeError, ValueError):
                continue
            m_i = math.exp(mu_i + sigma_i**2 / 2.0)
            r_i = 1.0 if mix_cap is None else _lognormal_retention(mu_i, sigma_i, mix_cap)
            weighted_mean += w_i * m_i
            weighted_retained += w_i * m_i * r_i
        if weighted_mean <= 0.0:
            return 0.0, 1.0
        return weighted_mean, weighted_retained / weighted_mean
    # Unknown/unsupported kind: exclude from both sums rather than guess.
    return 0.0, 1.0


def _safe_display_max(raw: Any, rc: ReportingCurrency) -> float | None:
    """Convert a stored ``max`` to its display value, never raising.

    Milestone gate finding (jj): ``_field_mean_and_retention`` above already
    fails soft to R_f=1.0 on a malformed/non-numeric ``max`` (its own
    ``try: cap = float(cap_raw) except (TypeError, ValueError)``) -- this is
    the SEPARATE final DISPLAY conversion of the disclosed cap value itself.
    A tampered snapshot (unreachable via validated writes -- D19's floor
    rejects a non-numeric ``max`` at write time) could otherwise reach
    ``float()`` here and raise TypeError/ValueError, 500-ing run-detail.
    Degrades to ``None`` (omit the $ display) instead, mirroring the
    retention-math fail-soft above.
    """
    if raw is None:
        return None
    try:
        return rc.convert(float(raw))
    except (TypeError, ValueError):
        return None


def _build_capacity_cap_note(run: Any, rc: ReportingCurrency) -> dict[str, Any] | None:
    """Per-scenario capacity-cap disclosure for the SINGLE run-detail page.

    Returns None (no note, no 500) when:
      - ``run.scenario_inputs_snapshot`` is missing/NULL (legacy pre-T2/#351
        runs, or a run type that never carries one),
      - the snapshot carries no scenarios,
      - neither PL nor SL carries a ``max`` (nothing capped on this
        scenario -- nothing to disclose), or
      - the fields' combined untruncated mean is non-positive (degenerate
        input; avoids a division by zero).

    Basis (Decision 1 / B-CAP-DISC): ``scenario_inputs_snapshot`` holds
    INHERENT inputs only, so this is an INHERENT-basis figure. The
    residual-basis figure is NOT the same number -- each field's R_f is
    invariant under a scale-both rule, but the scenario-level composition is
    an E_f-weighted average and the residual weights are ``k_f * E_f`` with
    INDEPENDENT PL/SL control multipliers, so the ratio moves (measured
    spread: up to ~3.67x over an admissible (k_PL, k_SL) bracket, itself
    bracket-conditional and growing unboundedly as the multiplier ratio
    diverges). Re-deriving the residual-basis figure needs the node
    multipliers plus the currency subtractor's non-linear post-transform --
    out of scope here. The caller/template MUST label this figure
    "inherent-basis" explicitly; it must never render as if it were the
    as-reported (residual) figure.

    ``R_scen = sum_f(E_f * R_f) / sum_f(E_f)`` over the scenario's
    primary_loss and secondary_loss fields -- BOTH kinds (lognormal /
    lognormal_mixture / PERT) appear in BOTH sums; LEF cancels and does not
    appear. The disclosed cap effect is ``1 - R_scen``. Never quotes
    B-CAP-PORT's portfolio figure (a different, mixed basis).
    """
    snapshot = getattr(run, "scenario_inputs_snapshot", None)
    if not isinstance(snapshot, dict):
        return None
    scenarios = snapshot.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return None
    # SINGLE runs snapshot exactly one scenario (this builder is never
    # called for AGGREGATE -- see the module-level note above).
    scen = scenarios[0]  # adapter-iter: ok — SINGLE-run snapshot has exactly one scenario
    if not isinstance(scen, dict):
        return None
    pl = scen.get("primary_loss")
    sl = scen.get("secondary_loss")
    fields = [f for f in (pl, sl) if isinstance(f, dict)]
    if not fields:
        return None
    if not any(f.get("max") is not None for f in fields):
        return None  # nothing capped on this scenario -- nothing to disclose

    total_mean = 0.0
    total_retained = 0.0
    for f in fields:
        e_f, r_f = _field_mean_and_retention(f)
        total_mean += e_f
        total_retained += e_f * r_f
    if total_mean <= 0.0:
        return None

    r_scen = total_retained / total_mean
    cap_effect_frac = max(0.0, min(1.0, 1.0 - r_scen))
    pl_max = pl.get("max") if isinstance(pl, dict) else None
    sl_max = sl.get("max") if isinstance(sl, dict) else None
    return {
        "cap_effect_frac": cap_effect_frac,
        "pl_max": _safe_display_max(pl_max, rc),
        "sl_max": _safe_display_max(sl_max, rc),
    }
