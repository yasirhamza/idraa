"""View-model builder for the AGGREGATE run-detail page.

Mirrors services/run_view_model.py on the AGGREGATE side.
The executor's _build_aggregate_results_payload writes the persisted dict;
this module converts that dict -> template-context dict.

No DB, no HTTP, no fair_cam imports — pure dict/list manipulation.

Attribution matrix semantics (Task 4 / Shapley reader):
  - Each cell's PRIMARY figure (`"value"`) reads `shapley_value_mean` from the
    persisted control_adjustments dict when present (2026-07-04 mean+typical
    side-by-side — mean-basis, scale-coherent with the MC mean headline),
    falling back to the legacy `shapley_value` (typical-basis: PERT mode /
    lognormal median) only when the mean key is absent — pre-mean-basis runs,
    or the rare cell whose mean pass individually dropped as non-finite. Each
    cell ALSO carries a paired SECONDARY figure (`"value_typical"`), always the
    legacy `shapley_value` key regardless of which basis backs `"value"` — used
    by the CSV export's paired column (Task 6), not rendered in the on-screen
    matrix. The two figures are independent reads: one CAN be None while the
    other is populated (the mean and typical Shapley passes are independent
    computations sharing only the composition cache) — render each side's
    absence as None ('—'), never a fabricated 0.0.
  - Absent primary key (no `shapley_value_mean` NOR `shapley_value`) → cell is
    None → renders as '—' and is excluded from column totals. This is
    intentionally distinct from a genuine 0.0 (null-player) cell which IS
    included in totals.
  - Run-level legacy guard: if control_adjustments exist but NONE carry a
    `shapley_value` key, the matrix returns {"controls": [], "rows": [],
    "unavailable": True} so the template can render an appropriate banner
    rather than a misleading all-$0 table.
  - Column totals equal the sum of non-None cells → Shapley efficiency holds
    at the displayed-data level (cells sum to column total) on the PRIMARY
    chain; `"total_reduction_typical"` is the paired informational total.
  - `matrix["basis"]`: `"mean"` when ANY cell on the run carries
    `shapley_value_mean` (every run executed after the mean-basis chain
    landed), else `"typical"` (legacy runs, and the empty/unavailable states).
    Drives caption/label switches on consuming surfaces.

Currency: callers pass ``rc`` (a ReportingCurrency from
``services/reporting_currency.py``).  The default is ``_USD_IDENTITY``
(no conversion) so un-threaded callers stay correct.  All money values are
converted once here at the boundary; templates and formatters only format.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from idraa.services._view_model_helpers import (
    DIST_STATS_DEFINITIONAL_NOTE,  # Task 1 (#353): defined in _view_model_helpers to avoid circular import with reports.py
    build_dist_stats_rows,  # Task 1 (#353): shared row builder
    has_ci_band,
    if_removed_by_control_aggregate,  # leave-one-out "if removed" AGGREGATE lookup (display plumbing)
    process_weight_robustness_for_display,  # Task 5 (#419): weight-robustness display helper
    snapshot_sub_functions_by_id,  # Issue #436: sub-function lookup for zero-reason labels
    strip_samples,
)
from idraa.services.reporting_currency import ReportingCurrency
from idraa.services.run_view_model import (
    _USD_IDENTITY,
    _convert_curve_losses,
    _convert_risk_dict,
    _currency_meta,
)


def build_aggregate_display_results(
    run: Any, rc: ReportingCurrency = _USD_IDENTITY
) -> dict[str, Any] | None:
    """Convert run.simulation_results (AGGREGATE shape) -> template view-model.

    Returns None when run.simulation_results is None (PENDING / RUNNING /
    FAILED-before-persist runs).

    ``rc`` defaults to USD identity so callers that do not yet thread a
    ReportingCurrency remain correct.  All money values are converted once
    here at the boundary (convert-once invariant).
    """
    if run.simulation_results is None:
        return None

    sr = run.simulation_results
    agg_with_raw = strip_samples(sr.get("aggregate_with_controls", {}))
    agg_without_raw = strip_samples(sr.get("aggregate_without_controls", {}))
    ci_raw = sr.get("confidence_intervals", {})
    control_value = sr.get("control_value", {})

    # Convert money fields at the view-model boundary.
    agg_with = _convert_risk_dict(agg_with_raw, rc)
    agg_without = _convert_risk_dict(agg_without_raw, rc)

    # CI bounds are dollar loss bounds.
    if rc.code != "USD" and ci_raw:
        ci: dict[str, Any] = dict(ci_raw)
        ci["lower_bound"] = rc.convert(ci_raw.get("lower_bound", 0.0))
        ci["upper_bound"] = rc.convert(ci_raw.get("upper_bound", 0.0))
    else:
        ci = ci_raw

    # Convert control_value dollars (percent is a ratio — NOT money).
    if rc.code != "USD" and control_value:
        control_value = dict(control_value)
        control_value["dollars"] = rc.convert(control_value.get("dollars", 0.0))

    # LEC loss values (from the per-side raw curves) are money.
    with_lec_raw = sr.get("aggregate_with_controls", {}).get("loss_exceedance_curve", [])
    without_lec_raw = sr.get("aggregate_without_controls", {}).get("loss_exceedance_curve", [])
    with_epc_raw = sr.get("dual_epc", {}).get("with_controls", [])
    without_epc_raw = sr.get("dual_epc", {}).get("without_controls", [])

    # Leave-one-out "if removed" (display plumbing): AGGREGATE sums each
    # control's if_removed_value across scenarios that carry the key
    # (run_executor.py's per-scenario _inject_loo; linearity of expectation
    # makes the sum exact). _ir_partial marks controls whose sum covers only
    # part of their scenarios (LOO-Meth-3 "(partial)" marker).
    #
    # Mean+typical side-by-side (2026-07-04): build BOTH the typical-basis
    # (historical key) and mean-basis (new key) sums, then pick primary/
    # secondary by the run's weight_robustness basis so legacy runs (no
    # "basis" key -> "typical" default) render exactly as before — typical
    # primary, no secondary sub-line, no "(partial)" double-marking.
    _per_scenario = sr.get("per_scenario", [])
    _wr_raw = getattr(run, "weight_robustness", None)
    _basis = (_wr_raw or {}).get("basis", "typical")
    _ir_lookup_typical, _ir_partial_typical = if_removed_by_control_aggregate(
        _per_scenario, key="if_removed_value"
    )
    if _basis == "mean":
        _ir_lookup, _ir_partial = if_removed_by_control_aggregate(
            _per_scenario, key="if_removed_value_mean"
        )
        _ir_lookup_secondary: dict[str, float | None] | None = _ir_lookup_typical
    else:
        _ir_lookup, _ir_partial = _ir_lookup_typical, _ir_partial_typical
        _ir_lookup_secondary = None
    return {
        "headline_ale": _build_headline_ale(agg_with, ci),
        "control_value_headline": _build_control_value_headline(control_value),
        "dual_lec": _build_dual_lec(
            with_lec=_convert_curve_losses(with_lec_raw, rc),
            without_lec=_convert_curve_losses(without_lec_raw, rc),
        ),
        "dual_epc": _build_dual_epc(
            with_epc=_convert_curve_losses(with_epc_raw, rc),
            without_epc=_convert_curve_losses(without_epc_raw, rc),
        ),
        "per_scenario_ale_rows": _build_per_scenario_ale_rows(sr.get("per_scenario", []), rc),
        "per_scenario_control_matrix": _build_per_scenario_control_matrix(
            sr.get("per_scenario", []), rc
        ),
        "aggregate_with_controls": agg_with,
        "aggregate_without_controls": agg_without,
        "confidence_intervals": ci,
        "n_scenarios": sr.get("n_scenarios", 0),
        "n_simulations": sr.get("n_simulations", 0),
        # Task 1 (#353): 10-row distribution-stats + tail-risk ladder.
        # AGGREGATE semantics: aggregate_without_controls is the base side,
        # aggregate_with_controls is the residual side — mirrors the PDF
        # _draw_dist_stats_page column ordering (Without controls / With controls).
        # Both are already converted above.
        "dist_stats": build_dist_stats_rows(agg_without, agg_with),
        "dist_stats_note": DIST_STATS_DEFINITIONAL_NOTE,
        # P3 currency metadata — templates use these for formatting labels.
        "currency": _currency_meta(rc),
        "currency_provenance": rc.provenance,
        # Task 5 (#419): weight-robustness display data (converted to reporting currency).
        # None on legacy runs without weight_robustness column or before Task 4 landed.
        # Issue #436: pass sub-function lookup so per-control $0 cells get a reason label.
        "weight_robustness": process_weight_robustness_for_display(
            getattr(run, "weight_robustness", None),
            rc.convert,
            rc.code,
            sub_functions_by_id=snapshot_sub_functions_by_id(
                getattr(run, "controls_snapshot", None) or []
            ),
            if_removed_by_control=_ir_lookup,
            if_removed_partial_ids=_ir_partial,
            if_removed_by_control_typical=_ir_lookup_secondary,
        ),
    }


def _build_headline_ale(agg_with: dict[str, Any], ci: dict[str, Any]) -> dict[str, Any]:
    """Reuses PR nu's headline_ale_with_ci_band macro shape."""
    return {
        "value": agg_with.get("annualized_loss_expectancy", 0.0),
        "lo": ci.get("lower_bound", 0.0),
        "hi": ci.get("upper_bound", 0.0),
        "has_ci_band": has_ci_band(ci),
    }


def _build_control_value_headline(control_value: dict[str, Any]) -> dict[str, Any]:
    """For the new control_value_headline macro."""
    return {
        "dollars": control_value.get("dollars", 0.0),
        "percent": control_value.get("percent", 0.0),
    }


def _build_dual_lec(
    with_lec: list[dict[str, Any]], without_lec: list[dict[str, Any]]
) -> dict[str, Any]:
    """Pre-bundle for the dual_lec_curve macro.

    The executor (services/run_executor.py::_build_aggregate_lec_pair)
    already evaluates both curves on a shared union log-grid (100 points,
    pyfair-style: probability = (samples ≥ loss).mean() at each grid
    point). The view-model just clamps zeros to $1 (the chart's log-x
    axis can't render log10(0)) and passes the dense data through.
    """

    def _clamp(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"loss": max(p.get("loss", 0.0), 1.0), "probability": p.get("probability", 0.0)}
            for p in points
        ]

    return {
        "with_controls": _clamp(with_lec),
        "without_controls": _clamp(without_lec),
    }


def _build_dual_epc(
    with_epc: list[dict[str, Any]], without_epc: list[dict[str, Any]]
) -> dict[str, Any]:
    """Pre-bundle for the dual_epc_curve macro.

    Mirrors `_build_dual_lec` but inverts axes: the macro reads percentile
    on x and loss on y. Clamps loss to >= $1 so the chart's log y-axis
    can render every point (the LEC clamps log-x for the same reason).
    """

    def _clamp(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"percentile": p.get("percentile", 0.0), "loss": max(p.get("loss", 0.0), 1.0)}
            for p in points
        ]

    return {
        "with_controls": _clamp(with_epc),
        "without_controls": _clamp(without_epc),
    }


def _build_per_scenario_ale_rows(
    per_scenario: list[dict[str, Any]],
    rc: ReportingCurrency = _USD_IDENTITY,
) -> list[dict[str, Any]]:
    """One row per constituent scenario, sorted desc by base_ale.

    FAIR-pure: surfaces raw $ ALE per scenario (NOT "% contribution to aggregate")
    per the PR psi + PR phi methodology cleanup. Bar lengths in the chart convey
    relative impact; no overclaiming framing.

    ``rc`` converts money fields (base_ale, residual_ale) to the reporting
    currency. The sort key uses the raw ALE values for consistent ordering
    regardless of the conversion rate.
    """
    rows = []
    for ps in per_scenario:
        base_ale_raw = ps.get("base_risk", {}).get("annualized_loss_expectancy", 0.0)
        residual_ale_raw = ps.get("residual_risk", {}).get("annualized_loss_expectancy", 0.0)
        rows.append(
            {
                "scenario_id": ps.get("scenario_id", ""),
                "scenario_name": ps.get("scenario_name", "(unknown)"),
                "base_ale": rc.convert(base_ale_raw),
                "residual_ale": rc.convert(residual_ale_raw),
                # Keep raw for sort key to maintain stable ordering
                "_sort_base_ale": base_ale_raw,
            }
        )
    rows.sort(key=lambda r: (-r["_sort_base_ale"], r["scenario_name"]))
    # Strip the internal sort key before returning
    for r in rows:
        r.pop("_sort_base_ale", None)
    return rows


_ABSENT = object()  # distinguishes "no shapley_value key" from a real 0.0 (B-Arch-I1)


def _cell_value(adj: dict[str, Any], key: str = "shapley_value") -> float | None:
    """Persisted Shapley dollar for one (scenario, control) cell, read from ``key``.
    Returns None when the key is ABSENT (legacy run, or a scenario that
    skipped/dropped attribution) so it renders '—' and is excluded from totals —
    NOT conflated with a genuine 0.0 null-player. Cells now sum to the scenario /
    column total (Shapley efficiency).

    Explicit null degrades to absent: a persisted ``<key>: null`` (currently
    impossible from the writer, but a latent JSON foot-gun from future schema
    migrations or hand-edits) is treated identically to a missing key — it returns
    None and is excluded from totals, not coerced to a fake $0 null-player.
    """
    raw = adj.get(key, _ABSENT)
    return None if raw is _ABSENT or raw is None else float(raw)


def _primary_cell_value(adj: dict[str, Any]) -> float | None:
    """Primary (headline-comparable) matrix cell (2026-07-04 mean+typical
    side-by-side): MEAN-basis ``shapley_value_mean`` when present, else the
    legacy TYPICAL-basis ``shapley_value`` (pre-mean-basis runs, or the rare
    scenario whose mean pass individually dropped this cell as non-finite
    while the typical pass succeeded — see run_executor.py's
    ``*_dropped_mean_only`` audit trail)."""
    v = _cell_value(adj, "shapley_value_mean")
    return v if v is not None else _cell_value(adj, "shapley_value")


def _secondary_cell_value(adj: dict[str, Any]) -> float | None:
    """Paired typical-basis matrix cell (secondary figure): always the legacy
    ``shapley_value`` key, regardless of whether the primary used the mean or
    the typical fallback. Used only for the CSV export's paired column
    (Task 6, side-by-side); the on-screen matrix stays primary-only."""
    return _cell_value(adj, "shapley_value")


def displayed_control_order(
    totals: Mapping[str, float | None],
    names: Mapping[str, str],
) -> list[str]:
    """Canonical displayed column order, EXCLUDING absent-only controls.

    Shared pure helper (Arch-I8 / Arch-I-CanonSort1): the displayed attribution
    matrix's column sort AND the weight-robustness ensemble's canonical reference
    ranking MUST use the SAME ordering so ``_canon_order == displayed column order``
    by construction (contract-tested in Task 7). Sort key matches
    ``_build_per_scenario_control_matrix`` exactly:
    ``(total is None, -(total or 0.0), name)``.

    2026-07-04 mean+typical side-by-side: the ensemble's canonical reference
    (``run_executor.py``'s ``_canon_vals_agg``) is now built from the MEAN-basis
    Shapley pass, so ``totals`` here MUST be summed from ``_primary_cell_value``
    (mean-basis, falling back to legacy typical-basis only when the mean key is
    absent) — NOT the raw legacy ``_cell_value``/``shapley_value`` reading — or
    the two orderings would silently diverge on any run with a mean/typical
    rank disagreement. ``_build_per_scenario_control_matrix`` passes
    primary-basis totals for exactly this reason.

    Absent-only controls (``total is None`` — every cell was
    skipped/over_cap/over_budget/error so they have no valued cell to rank) form a
    trailing None-class that is EXCLUDED from the returned order: the ensemble
    cannot rank a control with no canonical value, and the displayed matrix renders
    them after the valued columns. A genuine null-player worth exactly ``0.0`` has a
    non-None total and IS included.

    Args:
        totals: control_id -> summed reduction-$ (float) or None (absent-only).
        names: control_id -> display name (sort tie-break).

    Returns:
        Ordered list of control_ids with a non-None total (absent-only excluded).
    """
    valued = [cid for cid, total in totals.items() if total is not None]
    valued.sort(key=lambda cid: (-(totals[cid] or 0.0), names.get(cid, "")))
    return valued


def _build_per_scenario_control_matrix(
    per_scenario: list[dict[str, Any]],
    rc: ReportingCurrency = _USD_IDENTITY,
    *,
    prefer_basis: str = "mean",
) -> dict[str, Any]:
    """Build the scenario x control attribution matrix using persisted Shapley values.

    ``prefer_basis`` (2026-07-04 mean+typical side-by-side, default ``"mean"`` =
    today's behavior): selects which basis populates the PRIMARY on-screen
    fields (``cell["value"]`` / ``control["total_reduction"]``, and — since
    column order and the legacy/unavailable state machine are keyed off the
    same primary read — the sort order and state-machine branch too).
    ``"mean"`` (default): primary = MEAN-basis (falling back to legacy typical
    only when the mean key is absent — ``_primary_cell_value``), secondary =
    always legacy typical (``_secondary_cell_value``). ``"typical"``: the two
    swap — primary = always legacy typical, secondary = MEAN-basis. The ONLY
    known caller of ``"typical"`` is ``services/reports.py``'s PDF builder
    (issue #467 — the PDF's own "typical-case estimates" caption is not yet
    basis-aware, so its input matrix is pinned to typical basis to keep that
    caption true). Web view-model call sites (this module's own
    ``build_aggregate_display_results``) do not pass this — they get the
    default mean-preferred matrix.

    Reads `per_scenario[i].control_adjustments[j].shapley_value` (representative-value
    point estimate — PERT mode / lognormal median — persisted by the executor's Shapley pass).

    Cell semantics:
    - Present `shapley_value` key with a non-None value → float cell (0.0 is a
      genuine null-player, included in totals and rendered as $0, NOT absent).
    - Absent `shapley_value` key OR explicit ``shapley_value: null`` → None cell →
      renders '—' and excluded from column totals (Shapley efficiency holds on the
      non-None subset).

    Column-membership semantics (absent-only controls):
    - A control is registered as a column as soon as any adjustment references it,
      even if every one of its cells is None (all scenarios were
      skipped/over_cap/over_budget/error).
    - Such a column gets ``total_reduction: None`` — NOT 0.0 — so the template
      can render '—' at the column footer too. This distinguishes "no data" from a
      genuine null-player worth exactly $0.
    - Column sort: valued columns first (desc by total_reduction), then absent-only
      columns (total_reduction is None) at the end, tie-break by control_name asc
      within each group. Key: ``(total_reduction is None, -(total_reduction or 0.0),
      control_name)``.

    Templates must render ``total_reduction is None`` as '—' (Task 5 owns templates).

    State machine:
    - No control_adjustments anywhere → empty state {"controls": [], "rows": []}.
    - Adjustments exist but NONE carry `shapley_value` → legacy/unavailable state
      {"controls": [], "rows": [], "unavailable": True}.
    - At least one `shapley_value` present → normal matrix (mixed None/float OK).

    Returns:
        {
            "controls": list[{control_id, control_name, total_reduction}],
                # total_reduction: float (sum of non-None cells) OR None (absent-only).
                # column order: valued columns desc by total_reduction, then absent-only
                # columns, tie-break by control_name asc within each group.
            "rows": list[{scenario_id, scenario_name, cells: list[{control_id, value}]}],
                # row order: desc by base_ale, tie-break by scenario_name asc.
                # `cells` positional parallel to `controls`; each entry is a
                # self-describing {control_id, value: float | None} dict.
        }
        OR {"controls": [], "rows": []} for the empty state,
        OR {"controls": [], "rows": [], "unavailable": True} for legacy runs.
    """
    # First pass: discover controls and classify the run.
    # totals[cid] accumulates the sum of non-None RAW USD cells so the sort
    # ordering is consistent regardless of conversion rate; None sentinel means absent-only.
    totals_raw: dict[str, float | None] = {}
    # Paired typical-basis totals (2026-07-04 side-by-side, CSV export only —
    # the on-screen matrix stays primary-only). Mirrors totals_raw's
    # absent-only-until-first-value accumulation.
    totals_typical_raw: dict[str, float | None] = {}
    names: dict[str, str] = {}
    has_adjustments = False
    has_shapley = False
    has_mean = False

    # prefer_basis swap (2026-07-04, PDF #467 pin): "typical" flips which
    # function feeds the PRIMARY ("value"/"total_reduction") vs SECONDARY
    # ("value_typical"/"total_reduction_typical") fields below. Default "mean"
    # is today's assignment (primary=_primary_cell_value, secondary=
    # _secondary_cell_value) — unchanged for every existing (web) caller.
    if prefer_basis == "typical":
        _primary_fn, _secondary_fn = _secondary_cell_value, _primary_cell_value
    else:
        _primary_fn, _secondary_fn = _primary_cell_value, _secondary_cell_value

    for ps in per_scenario:
        for adj in ps.get("control_adjustments", []) or []:
            cid = adj.get("control_id")
            if cid is None:
                continue
            has_adjustments = True
            if "shapley_value_mean" in adj and adj.get("shapley_value_mean") is not None:
                has_mean = True
            val = _primary_fn(adj)
            if val is not None:
                has_shapley = True
                # Accumulate into the running total (initialise from None if first hit).
                prev = totals_raw.get(cid)
                totals_raw[cid] = (0.0 if prev is None else prev) + val
            elif cid not in totals_raw:
                # First encounter is absent — mark as absent-only for now.
                totals_raw[cid] = None
            # (If cid already has a float total, a subsequent absent cell doesn't reset it.)
            val_typ = _secondary_fn(adj)
            if val_typ is not None:
                prev_typ = totals_typical_raw.get(cid)
                totals_typical_raw[cid] = (0.0 if prev_typ is None else prev_typ) + val_typ
            elif cid not in totals_typical_raw:
                totals_typical_raw[cid] = None
            names[cid] = adj.get("control_name", "(unnamed)")

    # State machine branches.
    if not has_adjustments:
        return {"controls": [], "rows": [], "basis": "typical"}
    if not has_shapley:
        return {"controls": [], "rows": [], "unavailable": True, "basis": "typical"}

    # "basis": describes which basis backs the PRIMARY ("value"/"total_reduction")
    # fields above. prefer_basis="typical" PINS this to "typical" unconditionally
    # (the #467 PDF pin — the primary fields are deliberately always the legacy
    # typical read regardless of whether this run has mean data). Otherwise:
    # whether ANY cell on this run carries a mean-basis figure. New runs (post
    # 2026-07-04) always do; legacy runs never do — mixed within one run is not
    # reachable (the mean pass runs uniformly over every scenario/control).
    basis = "typical" if prefer_basis == "typical" else ("mean" if has_mean else "typical")

    # Convert totals: None (absent-only) stays None; float values are converted.
    # Conversion is a linear scalar so cell_sum * rate == sum(cell * rate) —
    # Shapley efficiency is preserved after conversion (no-double-convert invariant).
    totals_converted: dict[str, float | None] = {
        cid: (rc.convert(v) if v is not None else None) for cid, v in totals_raw.items()
    }
    totals_typical_converted: dict[str, float | None] = {
        cid: (rc.convert(v) if v is not None else None) for cid, v in totals_typical_raw.items()
    }

    # Sort: valued columns first (desc by RAW total), then absent-only columns;
    # tie-break by control_name asc within each group. The valued-column order is
    # produced by the shared displayed_control_order helper (Arch-I8) so it matches
    # the weight-robustness ensemble's canonical reference order by construction.
    # 2026-07-04 mean+typical side-by-side: totals_raw is summed from
    # _primary_cell_value (MEAN-basis, falling back to typical only when the
    # mean key is absent) — the SAME basis the ensemble's canonical reference
    # now uses (run_executor.py's _canon_vals_agg is built from the mean-basis
    # Shapley pass), so the two orderings stay in lockstep on mean-basis runs.
    # The absent-only (total is None) columns the helper excludes are appended
    # here (name-sorted) because the displayed matrix DOES render them as '—' columns.
    valued_order = displayed_control_order(totals_raw, names)
    absent_order = sorted(
        (cid for cid, total in totals_raw.items() if total is None),
        key=lambda cid: names.get(cid, ""),
    )
    col_order = [*valued_order, *absent_order]
    controls: list[dict[str, Any]] = [
        {
            "control_id": cid,
            "control_name": names[cid],
            "total_reduction": totals_converted[cid],
            # Paired typical-basis column total (CSV export only, Task 6).
            "total_reduction_typical": totals_typical_converted.get(cid),
        }
        for cid in col_order
    ]
    col_index = {cid: i for i, cid in enumerate(col_order)}

    sorted_ps = sorted(
        per_scenario,
        key=lambda ps: (
            -ps.get("base_risk", {}).get("annualized_loss_expectancy", 0.0),
            ps.get("scenario_name", "(unknown)"),
        ),
    )
    rows = []
    for ps in sorted_ps:
        cells: list[dict[str, Any]] = [
            {"control_id": cid, "value": None, "value_typical": None} for cid in col_order
        ]
        for adj in ps.get("control_adjustments", []) or []:
            cid = adj.get("control_id")
            if cid is None or cid not in col_index:
                continue
            raw_val = _primary_fn(adj)
            raw_val_typ = _secondary_fn(adj)
            cells[col_index[cid]] = {
                "control_id": cid,
                # Convert cell value; None (absent) passes through unchanged.
                "value": rc.convert(raw_val) if raw_val is not None else None,
                # Paired typical-basis cell (CSV export only, Task 6).
                "value_typical": rc.convert(raw_val_typ) if raw_val_typ is not None else None,
                # #100 drill-down: per-FAIR-factor multipliers for THIS
                # (scenario, control) pair, straight from the persisted
                # adjustment (executor serialiser keys). Dimensionless
                # multipliers — no currency conversion. None when absent so
                # the template renders an em-dash.
                "factors": {
                    "tef": adj.get("tef_multiplier"),
                    "vuln": adj.get("vulnerability_multiplier"),
                    "pl": adj.get("primary_loss_multiplier"),
                    "sl": adj.get("secondary_loss_multiplier"),
                },
            }
        rows.append(
            {
                "scenario_id": ps.get("scenario_id", ""),
                "scenario_name": ps.get("scenario_name", "(unknown)"),
                "cells": cells,
            }
        )

    return {"controls": controls, "rows": rows, "basis": basis}
