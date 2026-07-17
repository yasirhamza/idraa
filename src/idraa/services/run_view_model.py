"""View-model builder for the run-detail page.

Pure transformation: takes a RiskAnalysisRun ORM row (with simulation_results
JSON pre-loaded) and returns a dict consumed by templates/runs/_results_panel.html.

Mirrors services/run_executor.py:_build_results_payload on the read side:
the executor converts fair-cam DTOs → persisted dict; this module converts
persisted dict → template view-model.

No DB, no HTTP, no fair_cam imports — pure dict/list manipulation.

Currency: callers pass ``rc`` (a ReportingCurrency from
``services/reporting_currency.py``).  The default is ``_USD_IDENTITY``
(no conversion) so un-threaded callers stay correct.  Templates and
formatters receive already-converted values and only format; they never
multiply by a rate again (convert-once invariant).
"""

from __future__ import annotations

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
    "_build_control_effectiveness_rows",
    "_build_headline_ale",
    "_build_risk_comparison",
    "_build_tail_risk",
    "_has_ci_band",
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
