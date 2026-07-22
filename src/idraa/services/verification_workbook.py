"""Verification workbook (Phase 2a): an independent in-Excel Monte Carlo.

Builds a downloadable .xlsx that re-runs the FAIR MC in native Excel formulas and
shows it agreeing with fair_cam within sampling error. Pure reader: takes a
COMPLETED run's frozen snapshots + summary, re-derives composed control
multipliers via fair_cam.compose_groups on the frozen controls_snapshot (gated by
a unit-table drift check), and streams workbook bytes. The ONLY src/ module
permitted to import an Excel WRITER library (see
tests/arch/test_excel_writer_confined.py).

This module is the SOLE xlsxwriter call site. The verification workbook uses ONE
self-contained ``LET`` dynamic-array formula per scenario
(``verification_workbook_let.scenario_let_formula``) written via xlsxwriter's
``write_dynamic_array_formula``. openpyxl was DROPPED from the write path
(Task 7): it cannot emit the dynamic-array ``cm`` metadata a spilling LET requires,
and the prior explicit-per-row openpyxl generator (~1M formula cells) was removed
in favour of the LET. openpyxl remains a DEV-only dependency (the injection tests
still read produced workbooks via ``openpyxl.load_workbook`` + ``cell.data_type``
to assert no formula promotion); the writer-confinement arch test still forbids any
NEW src openpyxl runtime import alongside xlsxwriter.

The public entry point is ``build_verification_workbook`` — an aggregate-aware
dispatcher routing single vs aggregate runs to ``build_single_run_let_workbook`` /
``build_aggregate_let_workbook`` (both xlsxwriter LET paths).

Design: docs/plans/2026-06-15-verification-workbook-spill-redesign.md (GATE PASSED
2026-06-15); original: docs/plans/2026-06-14-verification-workbook-design.md.
"""

from __future__ import annotations

import math
import re
from typing import Any

from fair_cam.models.control import Control as FairCamControl
from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction
from pydantic import TypeAdapter, ValidationError

from idraa.schemas.run_snapshot import ControlSnapshot, ControlSnapshotV3
from idraa.services._view_model_helpers import (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER,
    ES_CI_Z_95,  # Task 10 (Spec-B1): 95% two-sided normal z-score, single source of truth
    stability_badge,
)
from idraa.services.workbook_theme import WorkbookColors as _Colors

_CONTROL_SNAPSHOT_ADAPTER: TypeAdapter[ControlSnapshot] = TypeAdapter(ControlSnapshot)

# Spreadsheet formula/CSV-injection triggers: a leading char in this set, or a
# leading control char, makes some spreadsheet apps promote the string to a
# formula. Prefix with a single quote to force a text cell.
#
# The leading "{" guards xlsxwriter specifically: its write() auto-promotes both
# "=danger()" AND "{=danger()}" (legacy array-formula braces) to a live <f> cell.
# openpyxl's write-only path never did, so the writer swap to xlsxwriter widens the
# injection surface — neutralizing a leading "{" closes it defense-in-depth even if
# a call site ever forgets write_string (Sec-B2).
_FORMULA_LEADERS = ("=", "+", "-", "@", "{")
_CONTROL_LEADERS = ("\t", "\r", "\n")  # U+0009, U+000D, U+000A


def _neutralize(value: str) -> str:
    """Return *value* safe to write to a cell: prefix ``'`` if it could be read
    as a formula. Idempotent for already-safe strings. Sec-I1/Sec-N2."""
    if value and value[0] in (_FORMULA_LEADERS + _CONTROL_LEADERS):
        return "'" + value
    return value


class LegacySnapshotError(Exception):
    """Raised when a control snapshot is not V3-recompose-ready (V1/V2/malformed).
    The caller degrades to the fail-loud 'composition not reconstructible' cell."""


def snapshot_to_fair_cam_controls(
    controls_snapshot: list[dict[str, Any]],
) -> list[FairCamControl]:
    """Reconstruct MINIMAL fair_cam Controls (control_id + assignments only) from
    frozen V3 control snapshots. compose_groups reads nothing else (domain/cost/
    timing deliberately omitted — they default on FairCamControl and are unread by
    composition; verified group_composition.py:94-96). Raises LegacySnapshotError
    for any non-V3 or malformed snapshot (Pydantic-validated)."""
    controls: list[FairCamControl] = []
    for raw in controls_snapshot:
        try:
            model = _CONTROL_SNAPSHOT_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            raise LegacySnapshotError(f"control snapshot failed validation: {exc}") from exc
        if not isinstance(model, ControlSnapshotV3):
            raise LegacySnapshotError(
                f"control snapshot is not V3: version={getattr(model, 'snapshot_version', '?')}"
            )
        if not model.assignments:
            raise LegacySnapshotError("V3 control snapshot has no assignments")
        fc_assignments: list[FairCamControlFunctionAssignment] = []
        for a in model.assignments:  # iterate ALL — adapter iteration contract
            try:
                fc_assignments.append(
                    FairCamControlFunctionAssignment(
                        # v3 enum -> fair_cam enum via parallel .value (matches
                        # _v3_to_fair_cam_control's FairCamSubFunction(a.sub_function.value))
                        sub_function=FairCamSubFunction(a.sub_function.value),
                        capability_value=a.capability_value,  # None ok
                        coverage=a.coverage,
                        reliability=a.reliability,
                        degradation_rate=0.0,  # unread by compose_groups
                    )
                )
            except (ValueError, TypeError) as exc:
                # e.g. coverage/reliability out of [0,1] -> FairCam __post_init__
                # ValueError; funnel to fail-loud rather than 500.
                raise LegacySnapshotError(f"malformed V3 assignment: {exc}") from exc
        # FairCamControl: control_id + assignments only; name/domain/cost_model/
        # control_type all DEFAULT (verified control.py) and are unread by
        # compose_groups. __post_init__ raises on empty assignments — guarded above.
        controls.append(
            FairCamControl(control_id=str(model.control_id), assignments=fc_assignments)
        )
    return controls


def unit_table_has_drifted(controls_snapshot: list[dict[str, Any]]) -> bool:
    """True if any assignment's frozen ``unit_type`` differs from the LIVE
    fair_cam ``SUB_FUNCTION_UNITS`` (the table ``compose_groups`` reads).

    Comparing against ``idraa.models.enums.SUB_FUNCTION_UNITS`` would be a
    vacuous no-op (Arch-I1-r2): the engine keys its unit interpretation off
    fair_cam's copy, so the gate must too. Both operands are normalized through
    fair_cam ``UnitType.value`` so a name-vs-value or v3-vs-fair_cam
    representation mismatch can't make the gate silently always-pass /
    always-drift. Unknown frozen value / unknown sub_function / missing fields
    => treat as drift (fail loud)."""
    # Import the MODULE (not the symbol) so a test monkeypatch on the attribute
    # is visible at call time — this is the table compose_groups reads.
    import fair_cam.models.sub_function as fc_sf

    for snap in controls_snapshot:
        for a in snap.get("assignments", []):
            frozen = a.get("unit_type")
            sub = a.get("sub_function")
            if frozen is None or sub is None:
                return True  # cannot verify -> treat as drift, fail loud
            try:
                # normalize frozen (str slug OR enum) to fair_cam UnitType.value
                frozen_val = fc_sf.UnitType(getattr(frozen, "value", frozen)).value
                sf = fc_sf.FairCamSubFunction(getattr(sub, "value", sub))
                live_val = fc_sf.SUB_FUNCTION_UNITS[sf].value
            except (ValueError, KeyError):
                return True  # unknown slug/sub_function since execution -> drift
            if frozen_val != live_val:
                return True
    return False


def composed_node_multipliers(
    controls_snapshot: list[dict[str, Any]],
    *,
    availability_self_detection: bool = False,
) -> dict[str, float]:
    """Re-derive the composed node multipliers + currency subtractor the engine
    applied, by calling fair_cam's own compose_groups on the frozen snapshot.

    Returns the engine's canonical node keys VERBATIM (threat_event_frequency,
    vulnerability, primary_loss, secondary_loss) + currency_subtractor_total — the
    exact keys/values the residual path consumes (native_control_aware.py:124-131).
    Caller MUST have checked unit_table_has_drifted() first; reconstruction raises
    LegacySnapshotError for non-V3/malformed snapshots (Task 4).

    ``availability_self_detection`` — when True the LEC_RESPONSE group's magnitude
    multiplier is forwarded unconditionally (§3.3.2 p.19), matching the engine's
    treatment of availability scenarios so the workbook residual does not silently
    disagree with the run it audits. Default False = detection-gated."""
    from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
    from fair_cam.risk_engine.group_composition import compose_groups

    controls = snapshot_to_fair_cam_controls(controls_snapshot)
    comp = compose_groups(controls)
    mults = _group_comp_to_node_multipliers(
        comp, availability_self_detection=availability_self_detection
    )  # keys == _NODE_KEYS (engine canon)
    return {**mults, "currency_subtractor_total": comp.currency_subtractor_total}


# --- Shared residual-reconstructibility + App-stat helpers --------------------
# Consumed by both the single-run and aggregate LET-spill paths. The residual
# block is faithfully re-derivable ONLY when the controls_snapshot is V3
# recompose-ready (no LegacySnapshotError) AND the fair_cam unit table has NOT
# drifted since execution AND every residual magnitude node is param-scalable.
# Otherwise the workbook degrades to the fail-loud "composition not
# reconstructible -- app value shown" cell rather than emitting a wrong residual.

_FAIL_LOUD_TEXT = (
    "composition not reconstructible -- app value shown "
    "(control snapshot is legacy/non-V3 or the fair_cam unit table has drifted "
    "since this run executed; the in-Excel residual MC cannot be re-derived)"
)


def _residual_reconstructible(
    controls_snapshot: list[dict[str, Any]],
    *,
    availability_self_detection: bool = False,
) -> tuple[bool, dict[str, float] | None]:
    """Return (reconstructible, composed_mults). True only when the snapshot
    recomposes via compose_groups (no LegacySnapshotError / ValueError /
    RuntimeError -- Final-Sec-1, #439) AND the unit table has not drifted. On
    True, composed_mults carries the engine canonical node keys +
    currency_subtractor_total. No controls -> trivially reconstructible identity
    (base==residual; mults all 1.0, subtractor 0.0).

    ``availability_self_detection`` — forwarded into ``composed_node_multipliers``
    so the workbook residual matches the engine's treatment of the scenario's effect."""
    if not controls_snapshot:
        return True, {
            "threat_event_frequency": 1.0,
            "vulnerability": 1.0,
            "primary_loss": 1.0,
            "secondary_loss": 1.0,
            "currency_subtractor_total": 0.0,
        }
    if unit_table_has_drifted(controls_snapshot):
        return False, None
    try:
        mults = composed_node_multipliers(
            controls_snapshot, availability_self_detection=availability_self_detection
        )
    except (LegacySnapshotError, ValueError, RuntimeError):
        # Final-Sec-1 (#439/#451 final-gate): Slice 2's compose_groups added
        # fail-loud E-bound guards (raises ValueError on an out-of-range
        # e_vmc/e_dsc/E_meta/kappa) and a currency-in-meta RuntimeError. A
        # legacy stored snapshot can carry a capability_value the DTO
        # deliberately doesn't bound (e.g. 85.0 for a PROBABILITY assignment)
        # and trip one of those guards on recompose. Degrade to the existing
        # "not reconstructible" path rather than 500ing the workbook download
        # — this is a display-fallback boundary, not a data-integrity guard.
        return False, None
    return True, mults


def _norm_dist(dist: dict[str, Any] | None) -> dict[str, Any] | None:
    """Lowercase the distribution slug (matches _dict_to_fair_distribution's
    ``str(kind).lower()`` so casing in the frozen snapshot can't break dispatch)."""
    if not dist:
        return None
    out = dict(dist)
    if "distribution" in out:
        out["distribution"] = str(out["distribution"]).lower()
    return out


def _residual_sample_formulas(
    scenario: dict[str, Any], mults: dict[str, float] | None
) -> tuple[bool, dict[str, Any]]:
    """Residual-scalability gate for the scenario. Returns ``(feasible, {})``.

    ``feasible`` is False when a residual node cannot be PARAMETER-scaled — e.g. a
    BETA distribution sits in a tef/pl/sl slot (``scaled_params`` rejects it), a
    non-finite/negative node multiplier, or a missing/malformed param — in which
    case the caller degrades to the fail-loud "composition not reconstructible"
    cell. This is the SAME param-scalability gate the aggregate LET path uses to
    decide whether a scenario can carry an in-Excel residual LET; it shares
    ``scaled_params`` with the LET emitter (single-sourced, so the feasibility
    decision and the actual LET emit can never disagree on what is scalable).

    The second tuple element is an empty dict — the LET path builds its residual
    sampling expressions itself via ``scenario_let_formula``; this predicate only
    pre-checks scalability (callers consume ``feasible`` only)."""
    if mults is None:
        return False, {}
    from idraa.services.verification_workbook_let import scaled_params

    tef = _norm_dist(scenario.get("threat_event_frequency"))
    pl = _norm_dist(scenario.get("primary_loss"))
    sl = _norm_dist(scenario.get("secondary_loss"))
    try:
        # Probe param-level scalability of every present tef/pl/sl node (vuln is
        # sample-level and never param-scaled, so it is not probed here). A BETA in
        # a magnitude slot or a bad multiplier raises -> not reconstructible.
        if tef is not None:
            scaled_params(tef, mults["threat_event_frequency"])
        if pl is not None:
            scaled_params(pl, mults["primary_loss"])
        if sl is not None:
            scaled_params(sl, mults["secondary_loss"])
        # The sample-level mults must also be present (vuln clip + currency
        # subtractor) so the LET can emit a faithful residual.
        _ = mults["vulnerability"]
        _ = mults["currency_subtractor_total"]
    except (ValueError, KeyError, TypeError):
        return False, {}
    return True, {}


def _ale(sim_results: dict[str, Any], side: str) -> float:
    """App ALE for ``side`` in {"base_risk","residual_risk"} (run_executor.py:735)."""
    return float(sim_results.get(side, {}).get("annualized_loss_expectancy", 0.0) or 0.0)


def _var(sim_results: dict[str, Any], side: str, key: str) -> float:
    """App VaR (var_95/var_99/var_999) — direct keys on the risk dict
    (run_executor.py:739-740, 751-755)."""
    return float(sim_results.get(side, {}).get(key, 0.0) or 0.0)


def _es(sim_results: dict[str, Any], side: str, key: str) -> float:
    """App ES (es_95/es_99/es_999) — nested under ``expected_shortfall``
    (run_executor.py:677-679, merged via _fair_risk_to_dict)."""
    return float(sim_results.get(side, {}).get("expected_shortfall", {}).get(key, 0.0) or 0.0)


def _es_ci_annotation(sim_results: dict[str, Any], side: str, key: str) -> str:
    """Adjacent-cell label for an App ES value's 95% Monte Carlo interval.

    Task 10 (Spec-B1): the ES Monte Carlo standard error
    (``expected_shortfall_se``, Task 9) sibling of ``expected_shortfall``.
    Three cases, matching ``_es_ci_fields`` in _view_model_helpers.py:

    - ``expected_shortfall_se`` ABSENT from the side dict entirely (legacy
      run) -> "" (no annotation; bare ES App value, matches every other
      surface's "legacy row" rendering).
    - dict present but this level's value is ``None`` (< 2 tail samples at
      this N, SE undefined) -> the insufficient-tail-samples label.
    - dict present with a float value -> "+/-$X (95% MC interval)".

    All figures in this workbook are USD (no reporting-currency threading —
    see the "All figures in USD" note), so this formats a plain USD string
    rather than reusing the reporting-currency money filter.
    """
    se_dict = sim_results.get(side, {}).get("expected_shortfall_se")
    if se_dict is None:
        return ""
    se_val = se_dict.get(key)
    if se_val is None:
        return "95% MC interval: insufficient tail samples at this N"
    ci_half = ES_CI_Z_95 * se_val
    return f"95% MC interval ±${ci_half:,.0f}"


def _documentation_lines(
    *,
    run: Any = None,
    reconstructible: bool = True,
    max_n: int | None = None,
    aggregate_total_max: int | None = None,
    mc_iterations_max: int | None = None,
) -> list[str]:
    """Build the Documentation sheet prose lines (PURE — no writer dependency).

    Describes the LET-spill model (Task 6): each scenario is ONE self-contained
    ``LET`` dynamic-array formula that generates its own uniform draws internally
    (``RANDARRAY(N,1)`` per FAIR input) and runs base + residual over the *shared*
    draws (common random numbers). The file holds a handful of formulas; Excel
    materializes the per-iteration grid on recalc — there are no explicit per-row
    cells. Lines are returned RAW (not yet neutralized); the doc-sheet writer
    (``_write_let_documentation_sheet``) routes them through ``_neutralize``
    defense-in-depth.

    Cap arg names follow the LET model (Arch-I3 — "rows" is misleading once there
    are no explicit rows): ``max_n`` (per-run N ceiling,
    ``verification_workbook_max_n``) and ``aggregate_total_max`` (the aggregate
    ΣN ceiling, ``verification_workbook_aggregate_total_max``).
    """
    seed = getattr(run, "random_seed", None) if run is not None else None
    seed_txt = str(seed) if seed is not None else "(none — legacy run)"
    n_txt = str(max_n) if max_n is not None else "the configured cap"
    agg_txt = str(aggregate_total_max) if aggregate_total_max is not None else "the configured cap"
    mc_max_txt = f"{mc_iterations_max:,}" if mc_iterations_max is not None else "100,000"
    comp_result = "FAITHFUL" if reconstructible else "DEGRADED"
    comp_detail = (
        "the residual block was re-derived from the frozen control snapshot and "
        "matches what the engine applied"
        if reconstructible
        else "the residual block could NOT be re-derived (legacy/non-V3 snapshot "
        "or the fair_cam unit table drifted since execution); the App residual "
        "value is shown instead of an in-Excel residual MC"
    )

    lines: list[str] = [
        "Documentation",
        "",
        "WHAT THIS WORKBOOK IS",
        "An INDEPENDENT in-Excel re-run of the FAIR Monte Carlo for this analysis. "
        "Each scenario is ONE self-contained LET dynamic-array formula that "
        "generates its own uniform random draws internally with native Excel "
        "functions (RANDARRAY) and runs the whole base + residual loss chain over "
        "them. Excel's OWN random number generator drives those draws — a separate, "
        "independent RNG from the fair_cam engine that produced the App figures. If "
        "the two agree within sampling error, that is independent corroboration "
        "that the engine's sampling, per-iteration loss chain, and aggregation are "
        "correct.",
        "",
        "ONE FORMULA PER SCENARIO (no explicit rows). This file holds only a "
        "handful of formulas — one LET per scenario. There are NO per-iteration row "
        "cells written to disk. When Excel recalculates, the LET draws N rows of "
        "RANDARRAY internally and materializes the per-iteration grid in memory, "
        "then spills the summary stats (base ALE, residual ALE, control value, "
        "VaR95/99/999, ES95/99/999) as a small 9-row array next to its anchor cell. "
        "That is why the workbook builds instantly and stays tiny regardless of N.",
        "",
        "Why independence matters: the App (fair_cam) and this workbook share NO "
        "code and NO random draws. Agreement therefore cannot be a shared-bug "
        "artifact. The base-risk column is the fully-independent validation — it "
        "depends on no control composition at all.",
        "",
        "MICROSOFT 365 EXCEL (DESKTOP OR MOBILE). The LET spill relies on "
        "dynamic-array functions (LET, RANDARRAY, CHOOSE, SUMPRODUCT, "
        "PERCENTILE.INC, BETA.INV, NORM.INV) that need a modern Excel with "
        "dynamic-array support: Microsoft 365 (Windows, Mac, or mobile) or "
        "Excel 2021+. Excel Mobile and the iPad app open and spill this workbook "
        "correctly. Only pre-dynamic-array perpetual Excel (2019 and earlier) "
        "cannot compute the spill.",
        "",
        "SCOPE BOUNDARY (read this)",
        "This workbook independently validates THREE things: (1) the SAMPLING from "
        "each distribution, (2) the per-iteration LOSS CHAIN "
        "(LEF = TEF' x Vuln', LM = PL' + SL', loss = LEF x LM), and (3) the "
        "AGGREGATION (ALE = mean, VaR = percentile, ES = tail mean).",
        "It does NOT validate the FAIR-CAM composition math itself. The composed "
        "control multipliers are taken as an INPUT from the engine "
        "(re-derived via compose_groups) and fed identically to both sides, so "
        "composition is NOT independently validated here — the same compose_groups "
        "output feeds both the App and this workbook. The base-risk column (no "
        "composition) IS the fully-independent re-run.",
        "",
        "COMPOSITION AS INPUT",
        f"Composition result for this run: {comp_result} — {comp_detail}.",
        "For the residual block, the node multipliers "
        "(threat_event_frequency, vulnerability, primary_loss, secondary_loss) and "
        "the currency subtractor are re-derived from the frozen control snapshot by "
        "calling fair_cam's own compose_groups — never read from a persisted field. "
        "This re-derivation is EXACT only when BOTH (a) the unit-table drift gate "
        "passes (the snapshot's unit_type values still match fair_cam's live "
        "SUB_FUNCTION_UNITS) AND (b) the calibration vectors are unchanged — "
        "specifically the tau operational-effectiveness table AND the "
        "GROUP_NODE_MAPPING group->node weights. If the unit table has drifted, or "
        "the snapshot predates the recompose-ready V3 shape (a legacy V1/V2 run), "
        "the residual block degrades and shows the App residual value instead.",
        "",
        "COMMON RANDOM NUMBERS (CRN) — base and residual share the SAME draws",
        "Inside each LET, base and residual reuse the SAME RANDARRAY uniform "
        "columns (u_tef, u_vuln, u_pl, u_sl). The residual is NOT an independent "
        "re-draw — it is the same underlying variate, scaled. The App does the same "
        "thing, so the in-Excel control value (base ALE - residual ALE) is computed "
        "with the same CRN variance reduction the engine uses; there is no "
        "control-value-noise caveat here (an earlier explicit-row design drew base "
        "and residual independently and needed a wider control-value band — that "
        "caveat no longer applies).",
        "",
        "WHY CRN HOLDS UNDER SCALING. The node multipliers only SCALE or SHIFT a "
        "distribution; they never swap its FAMILY. A PERT's Vose moment-matched "
        "Beta alpha/beta are SCALE-INVARIANT (multiplying low/mode/high by m leaves "
        "alpha/beta unchanged — only the [low,high] support rescales), and a "
        "lognormal scale is a pure log-mean SHIFT (mean += ln(m), sigma unchanged). "
        "So inverting the SAME uniform draw u through the base and the residual "
        "distribution yields perfectly rank-correlated samples — true CRN. This "
        "holds because scaling preserves the inverse-CDF's monotone mapping of u; "
        "it would NOT survive an arbitrary DISTRIBUTION SWAP (e.g. PERT->lognormal "
        "between base and residual), which the model never does.",
        "",
        "FORMULAS (array form, inside the LET)",
        "Sampling (inverse-CDF over a uniform RANDARRAY column): PERT uses the "
        "engine's Vose moment-matched Beta form (symmetric inputs -> Beta(4,4), NOT "
        "textbook Beta(3,3)): low + BETA.INV(u, alpha, beta) * (high - low). "
        "Lognormal samples in log-space: EXP(NORM.INV(u, mean, sigma)). Uniform: "
        "low + u * (high - low). Triangular: inverse-CDF split on u. Vulnerability "
        "(Beta): BETA.INV(u, alpha, beta).",
        "Residual sampling reuses the SAME u columns (CRN): TEF/PL/SL multipliers "
        "scale the DISTRIBUTION PARAMETERS before the inverse-CDF (param-level); "
        "the vulnerability multiplier scales the drawn SAMPLE (sample-level), "
        "matching the engine asymmetry.",
        "Array-safe clips (this is the spill rewrite — read this). Because the loss "
        "chain runs over an in-memory ARRAY, the usual MIN(1, MAX(0, x)) would "
        "AGGREGATE the array to a single scalar and collapse the Monte Carlo. "
        "Instead the clips are written as element-wise BOOLEAN arithmetic: the "
        "non-negative floor max(0, x) is written (x>0)*x, and the [0,1] clip "
        "clip(x, 0, 1) is written ((x>0)*(x<1)*x+(x>=1)). These evaluate "
        "element-by-element and are exactly equivalent to numpy's maximum / clip. "
        "Vulnerability is [0,1]-clipped after its sample-level multiplier; the "
        "currency subtractor double-floors the secondary-loss sample "
        "((sl>0)*sl, minus subtractor, floored again). Then "
        "LEF = (TEF'>0)*TEF' x Vuln', LM = (PL'>0)*PL' + SL', loss = LEF x LM.",
        "Aggregation: ALE = AVERAGE(loss array). "
        "VaR(q) = PERCENTILE.INC(loss array, q). The tail mean (ES) is computed "
        "with SUMPRODUCT (a conditional-average function would need a real "
        "worksheet range and rejects an in-memory LET array): "
        "ES(q) = IFERROR(SUMPRODUCT((loss>=VaR_q)*loss) / "
        "SUMPRODUCT(--(loss>=VaR_q)), MAX(loss)). The empty-tail fallback is MAX "
        "(matching the App's persisted tail-metric implementation, which uses "
        "sample_max on an empty tail — NOT fair_cam's other ES path whose "
        "empty-tail fallback equals VaR).",
        "",
        "CAVEATS",
        "1. RANDARRAY VOLATILITY. RANDARRAY (like RAND) is a volatile function: the "
        "spill RE-ROLLS on every recalculation (any edit, or pressing F9). Each "
        "recalc draws a fresh Monte Carlo, so the Excel figures will shift slightly "
        "each time — this is expected sampling noise, not a discrepancy. To FREEZE "
        "a particular run, copy the spilled cells and Paste Special -> Values "
        "(paste-as-values) before comparing.",
        "",
        f"2. SAMPLING TOLERANCE & var_999. Agreement with the App is within "
        f"sampling error at this N, not bit-exact (the two RNGs are independent — "
        f"see below). Tail metrics are noisier than the mean: var_999 (and es_999) "
        f"are the 99.9th-percentile tail and are stable ONLY when sampled near the "
        f"server-side MC cap ({mc_max_txt} = 100,000) iterations. At smaller N the "
        f"var_999 / es_999 stats can diverge materially between this workbook and "
        f"the App purely from tail sampling variance — widen your tolerance for "
        f"those stats.",
        "",
        f"3. SEED / INDEPENDENT RNG. The App run used random seed {seed_txt}. "
        f"Re-running the analysis in the app with this seed reproduces the App "
        f"figures deterministically. This workbook cannot consume that seed — "
        f"Excel's RANDARRAY is independent by design (that independence is the whole "
        f"point), so the in-Excel figures are NOT bit-exact against the App; they "
        f"corroborate within sampling error.",
        "",
        f"4. RESPONSIVENESS CAP (N cap). The in-Excel N is "
        f"N = min(mc_iterations, {n_txt}) (verification_workbook_max_n is the per-run "
        f"N cap), independently of the run's mc_iterations — the workbook is a "
        f"fidelity instrument; N draws demonstrate convergence while keeping recalc "
        f"responsive. For an AGGREGATE run, each reconstructible scenario gets its "
        f"own LET and the per-scenario N is scaled down so the total across "
        f"scenarios, SUM(N), stays at or below {agg_txt} "
        f"(verification_workbook_aggregate_total_max). The aggregate roll-up sums "
        f"the per-scenario residual ALEs in Excel (ALE IS additive across "
        f"scenarios). The tail metrics (VaR/ES) are NOT additive -- you cannot sum "
        f"per-scenario VaR/ES -- so the Excel column for the aggregate VaR/ES rows "
        f"is 'n/a (not re-derived in this workbook)'. The correct aggregate tail "
        f"risk DOES exist and the App computes it: it is read off the aggregate "
        f"loss distribution (the per-iteration SUM of the scenarios' loss samples, "
        f"scenarios drawn independently), and the App's aggregate VaR/ES are shown "
        f"in the App column of those rows (a legacy run whose stored payload "
        f"predates aggregate tail metrics shows a 're-run to populate' note "
        f"instead). Non-reconstructible scenarios are listed summary-only (App "
        f"values) and excluded from the in-Excel residual roll-up. "
        f"SCENARIO-INDEPENDENCE CAVEAT: these aggregate VaR/ES assume the scenarios "
        f"are statistically independent (the aggregate loss is the per-iteration SUM "
        f"of independently-drawn scenario losses). If scenarios share a common cause "
        f"(shared infrastructure, asset, or control, or a single triggering event) "
        f"they are positively correlated and the TRUE aggregate tail is FATTER than "
        f"shown -- treat the aggregate VaR/ES as a LOWER BOUND. The aggregate ALE "
        f"(mean) is additive regardless of correlation; only the tail depends on "
        f"independence. For tightly-coupled scenarios, model them as a single scenario.",
        "",
        "5. UNIT-TABLE DRIFT & LEGACY FALLBACKS. If the fair_cam unit table has "
        "drifted since this run executed, or the snapshot is a legacy/non-V3 shape, "
        "the residual cannot be faithfully re-derived; those scenarios fall back to "
        "the App residual value (labeled in the sheet) rather than emitting a wrong "
        "in-Excel residual. The base column is always the fully-independent re-run.",
    ]
    return lines


# --- Task 2: single-run LET-spill assembly (gate path) ------------------------
# A leaner single-run assembly that replaces the N explicit formula rows with ONE
# self-contained LET dynamic-array formula (verification_workbook_let.scenario_let_
# formula). The LET generates its own RANDARRAY(N,1) uniform columns inside Excel
# and returns a 9-row stat array (base ALE, residual ALE, control value, VaR95/99/
# 999, ES95/99/999) that SPILLS DOWN from the anchor cell. A side-by-side block
# reads those spilled cells next to the App (fair_cam) values, with a delta + a
# within-tolerance flag per row. The old explicit-row path (build_verification_
# workbook) is kept alongside until Task 7 removes it.
#
# Fail-loud is CARRIED unchanged: a legacy/malformed snapshot (LegacySnapshotError),
# unit-table drift, a BETA in a tef/pl/sl slot, or any unsupported distribution
# kind -> the residual side renders the labeled "composition not reconstructible --
# app value shown" cell; the LET (which still validly emits the base side) is NOT
# emitted in that case (the LET is base+residual coupled). Instead the base App
# figures are shown App-only so the workbook is never a wrong number.

# Per-run N ceiling + aggregate ΣN cap are config fields (Task 5):
# settings.verification_workbook_max_n / verification_workbook_aggregate_total_max.
# _LET_MIN_N is the hard floor the aggregate ΣN scale-down never drops a scenario's
# N below — a many-scenario aggregate still draws a statistically meaningful sample
# per scenario rather than collapsing toward 1.
_LET_MIN_N = 100


def _agg_scaled_n(per_run_n: int, n_scenarios: int, aggregate_total_max: int) -> tuple[int, bool]:
    """Aggregate ΣN scale-down. Each of ``n_scenarios`` reconstructible scenarios
    draws the SAME N; with that uniform N, ``Σ N = n_scenarios * N``. If that
    exceeds ``aggregate_total_max``, lower N to ``aggregate_total_max //
    n_scenarios`` (integer floor), never below ``_LET_MIN_N``.

    Returns ``(n, scaled)`` where ``scaled`` is True iff the aggregate cap actually
    LOWERED N below ``per_run_n`` (so the caller emits the cap note only when it
    binds). With <= 1 scenario, or when the ΣN already fits, N == per_run_n and
    ``scaled`` is False.
    """
    if n_scenarios <= 1:
        # A single (or empty) scenario is governed by the per-run max_n, not the
        # aggregate ΣN cap (which exists to bound MANY-scenario recompute cost);
        # never scale a lone scenario below max_n. Matches this fn's docstring.
        return per_run_n, False
    if per_run_n * n_scenarios <= aggregate_total_max:
        return per_run_n, False
    scaled_n = max(aggregate_total_max // n_scenarios, _LET_MIN_N)
    # Floor-clamped back to per_run_n is impossible here (we only enter when the
    # product exceeds the cap), but guard against scaled_n >= per_run_n on a tiny
    # cap + the _LET_MIN_N floor so `scaled` honestly reflects a real reduction.
    if scaled_n >= per_run_n:
        return per_run_n, False
    return scaled_n, True


# The 9 stats the LET's CHOOSE({1;2;...;9}, ...) array returns, IN ORDER (must match
# scenario_let_formula). (label, app_side, app_kind, app_key) — app_kind in
# {"ale","var","es","control"}.
_LET_STAT_SPEC: tuple[tuple[str, str, str, str], ...] = (
    ("Base ALE", "base_risk", "ale", ""),
    ("Residual ALE", "residual_risk", "ale", ""),
    ("Control value (Base - Residual ALE)", "", "control", ""),
    ("Residual VaR 95", "residual_risk", "var", "var_95"),
    ("Residual VaR 99", "residual_risk", "var", "var_99"),
    ("Residual VaR 99.9", "residual_risk", "var", "var_999"),
    ("Residual ES 95", "residual_risk", "es", "es_95"),
    ("Residual ES 99", "residual_risk", "es", "es_99"),
    ("Residual ES 99.9", "residual_risk", "es", "es_999"),
)

# VWB-1 (2026-07-03 correctness audit): the 99.9-tail metrics (VaR 99.9 / ES 99.9)
# are only stable near the server-side MC cap (100,000 iterations); at the workbook's
# capped N they diverge from the App purely from tail sampling variance and would
# FALSE-FLAG a correct workbook. The workbook's own caveat #2 already says so. So the
# MC verdict COUNT EXCLUDES these two rows: their ok? cell reads the tail note below
# instead of "CHECK" when out of band, so a plain COUNTIF(...,"CHECK") over the full
# stat span can never count them (it only sees the literal "CHECK" the core rows emit).
# Keyed off the app_key suffix so a future _LET_STAT_SPEC edit stays in sync.
_LET_TAIL_APP_KEYS = frozenset({"var_999", "es_999"})
_LET_TAIL_OK_NOTE = "noisy tail -- see tolerance note"

# Aggregate tail-row spec (Excel col is n/a — not re-derived in-sheet; App col reads
# the aggregate run's aggregate_with_controls dict). ALE is additive across scenarios
# and IS rolled up in Excel; the tail metrics (VaR/ES) are NOT additive, so the
# workbook does not re-derive them in-sheet, but the App DOES compute them from the
# aggregate loss distribution (the per-iteration SUM of the scenarios' loss samples,
# scenarios drawn independently). Those App aggregate VaR/ES are shown in the App col.
# (label, app_kind, app_key) — app_kind in {"var","es"}.
_AGG_TAIL_ROW_SPEC: tuple[tuple[str, str, str], ...] = (
    ("VaR 95", "var", "var_95"),
    ("VaR 99", "var", "var_99"),
    ("VaR 99.9", "var", "var_999"),
    ("ES 95", "es", "es_95"),
    ("ES 99", "es", "es_99"),
    ("ES 99.9", "es", "es_999"),
)

# Excel-column text for the aggregate tail rows: the workbook rolls up scenario ALEs
# only and does NOT independently re-derive the aggregate tail in-sheet (tails can't
# be summed from per-scenario tails).
_AGG_TAIL_EXCEL_NA = "n/a (not re-derived in this workbook)"

# App-column text for a LEGACY aggregate run whose persisted aggregate_with_controls
# predates aggregate tail metrics (has_tail_metrics False / values 0.0) — suppress,
# do not fabricate a 0 (mirrors the suppress-not-fabricate convention).
_AGG_TAIL_APP_LEGACY_NA = "n/a (run predates aggregate tail metrics -- re-run to populate)"


_LET_GATE_NOTE = (
    "Open in Microsoft 365 Excel (desktop or mobile). The LET formula in the highlighted cell "
    "below GENERATES the Monte Carlo inside Excel (RANDARRAY) and SPILLS its 9-row "
    "stat array DOWN from that cell. Compare the 'Excel' column to the 'App' column "
    "in the side-by-side block -- they should match within sampling tolerance "
    "(tails VaR/ES 99.9 are noisier; widen tolerance there). Press F9 to re-roll "
    "the Monte Carlo; the Excel column will shift slightly each time (RANDARRAY is "
    "volatile) -- that is expected sampling noise, not a discrepancy."
)

# --- Task 4: worked-example block (single-run sheet) --------------------------
# An explicit, per-row SCALAR block that shows the sampling -> loss chain for the
# FIRST scenario one trial at a time, so the math is inspectable WITHOUT relying on
# the spilled LET (a spill's formula lives ONLY in its anchor cell — the materialized
# cells below are opaque values, not formulas). Each row is one independent trial:
# a per-row RAND() cell per FAIR node feeds the SAME _invcdf inverse-CDF the LET uses
# (single-sourced — NTH-3), then SCALAR clips (MAX(0,..)/MIN(1,MAX(0,..)) — correct
# here because these reference per-cell SCALARS, e.g. A12, NOT the LET's array vars;
# the boolean (x>0)*x clips are ONLY for the LET array context), then the base loss.
#
# Write discipline (Sec-I4): every label/header via write_string; the RAND/sample/
# clip/loss cells via write_formula; no numeric constants are written in this block.
# Scope: BASE chain only (sampling -> base loss); the residual/control composition
# is already inspectable in the side-by-side block. Single-run sheet only.
_WORKED_EXAMPLE_TRIALS = 12  # ~15 rows incl. title + column header + blank

# Worked-example column layout (0-based). RAND draws | inverse-CDF samples |
# scalar clips | base loss. The label sits in its own column to the right so the
# computed columns stay a clean A..M block.
_WE_COL_U_TEF = 0  # A  RAND() for TEF
_WE_COL_U_VULN = 1  # B  RAND() for Vuln
_WE_COL_U_PL = 2  # C  RAND() for PL
_WE_COL_U_SL = 3  # D  RAND() for SL
_WE_COL_TEF = 4  # E  TEF sample  = _invcdf(tef, A<r>)
_WE_COL_VULN = 5  # F  Vuln sample = _invcdf(vuln, B<r>)
_WE_COL_PL = 6  # G  PL sample   = _invcdf(pl, C<r>)
_WE_COL_SL = 7  # H  SL sample   = _invcdf(sl, D<r>)
_WE_COL_TEF_CLIP = 8  # I  =MAX(0, E<r>)            (TEF floor)
_WE_COL_VULN_CLIP = 9  # J  =MIN(1, MAX(0, F<r>))    (Vuln [0,1] clip)
_WE_COL_PL_CLIP = 10  # K  =MAX(0, G<r>)            (PL floor)
_WE_COL_SL_CLIP = 11  # L  =MAX(0, H<r>)            (SL floor)
_WE_COL_LOSS = 12  # M  =I<r>*J<r>*(K<r>+L<r>)   (base loss = LEF*LM)

# Short column headers (C4 aesthetic audit): the verbose clip headers
# ("vuln clip MIN(1,MAX(0,..))") collided with the narrow columns, so the exact
# clip/loss formulas now live in the block's intro note (see _write_worked_example)
# and the headers are terse. The "tef sample"/"vuln sample"/... + "...RAND()" +
# "base loss" tokens are load-bearing (keyword-pinned by the worked-example tests).
_WE_HEADERS: tuple[str, ...] = (
    "u_tef RAND()",
    "u_vuln RAND()",
    "u_pl RAND()",
    "u_sl RAND()",
    "tef sample",
    "vuln sample",
    "pl sample",
    "sl sample",
    "tef (clipped)",
    "vuln (clipped)",
    "pl (clipped)",
    "sl (clipped)",
    "base loss",
)


def _write_worked_example(
    rows: _XlsxRows,
    ws: Any,
    *,
    scenario: dict[str, Any],
    styles: _Styles,
    n_trials: int = _WORKED_EXAMPLE_TRIALS,
) -> bool:
    """Append the worked-example block to the single-run sheet (BASE chain).

    Returns True if the block was emitted, False if the scenario's distributions
    are not all native-Excel sampleable (then a single labeled note row is written
    instead — never a wrong formula). The sampling cell formula for each node is
    produced by calling ``_invcdf(node_dist, "<that row's RAND cell ref>")`` — the
    SAME helper the LET uses — so the worked-example and the LET can never drift.

    SCALAR clips (Sec-I4): the per-row references are scalar cells (e.g. ``E12``),
    so ``MAX(0, E12)`` / ``MIN(1, MAX(0, F12))`` ARE the correct element-wise clips
    here; the LET's boolean ``(x>0)*x`` form is ONLY for its array context.
    """
    from xlsxwriter.utility import xl_rowcol_to_cell

    from idraa.services.verification_workbook_let import _invcdf

    tef_dist = _norm_dist(scenario.get("threat_event_frequency")) or {}
    vuln_dist = _norm_dist(scenario.get("vulnerability")) or {}
    pl_dist = _norm_dist(scenario.get("primary_loss")) or {}
    sl_dist = _norm_dist(scenario.get("secondary_loss")) or {}

    rows.blank()
    # Intro + the exact clip/loss formulas folded in (C4): the column headers are
    # terse, so the formula text lives here in one wrapped, merged cell (kept as the
    # SINGLE row directly above the header row — the worked-example tests locate the
    # header at title_row+1 and the first trial at title_row+2).
    rows.prose(
        "Worked example (first scenario, BASE chain, one trial per row): explicit "
        "scalar formulas so the sampling -> loss math is inspectable on any device "
        "WITHOUT the spilled LET. Each row is one independent trial; RAND() is "
        "volatile (re-rolls on F9). The same inverse-CDF the LET uses is single-"
        "sourced here, so the two cannot drift. Column formulas: u_* = RAND(); "
        "tef/vuln/pl/sl sample = inverse-CDF(u); clips = tef MAX(0,tef), "
        "vuln MIN(1,MAX(0,vuln)), pl MAX(0,pl), sl MAX(0,sl); "
        "base loss = tef(clipped)*vuln(clipped)*(pl(clipped)+sl(clipped)).",
        styles.note_wrap,
        span=len(_WE_HEADERS),
    )
    rows.row(list(_WE_HEADERS))

    first_trial_row = rows.next_row_1based  # 1-based row of the FIRST trial

    for i in range(n_trials):
        r0 = (first_trial_row - 1) + i  # 0-based row index for xlsxwriter

        u_tef_ref = xl_rowcol_to_cell(r0, _WE_COL_U_TEF)  # e.g. "A12"
        u_vuln_ref = xl_rowcol_to_cell(r0, _WE_COL_U_VULN)
        u_pl_ref = xl_rowcol_to_cell(r0, _WE_COL_U_PL)
        u_sl_ref = xl_rowcol_to_cell(r0, _WE_COL_U_SL)

        # Single-source the per-row sampling expression from _invcdf, passing the
        # row's SCALAR RAND cell ref as the "uniform var" (interpolated verbatim).
        # _invcdf raises on an unsupported distribution kind -> emit a labeled note
        # row instead of a wrong formula (carries the module's fail-loud contract).
        try:
            tef_sample = "=" + _invcdf(tef_dist, u_tef_ref)
            vuln_sample = "=" + _invcdf(vuln_dist, u_vuln_ref)
            pl_sample = "=" + _invcdf(pl_dist, u_pl_ref)
            sl_sample = "=" + _invcdf(sl_dist, u_sl_ref)
        except (ValueError, KeyError, TypeError):
            # Roll back the two label/header rows we wrote: leave a single note row.
            # (We already advanced past them; just append the note below.)
            rows.row(
                [
                    "Worked example unavailable: a scenario distribution is not "
                    "natively sampleable in Excel (see the LET/side-by-side block)."
                ]
            )
            return False

        # Sample cell refs for the clip column (E/F/G/H of THIS row).
        tef_cell = xl_rowcol_to_cell(r0, _WE_COL_TEF)
        vuln_cell = xl_rowcol_to_cell(r0, _WE_COL_VULN)
        pl_cell = xl_rowcol_to_cell(r0, _WE_COL_PL)
        sl_cell = xl_rowcol_to_cell(r0, _WE_COL_SL)
        # Clip cell refs for the loss column (I/J/K/L of THIS row).
        tef_clip_cell = xl_rowcol_to_cell(r0, _WE_COL_TEF_CLIP)
        vuln_clip_cell = xl_rowcol_to_cell(r0, _WE_COL_VULN_CLIP)
        pl_clip_cell = xl_rowcol_to_cell(r0, _WE_COL_PL_CLIP)
        sl_clip_cell = xl_rowcol_to_cell(r0, _WE_COL_SL_CLIP)

        # RAND() draws (per-row, scalar, volatile). RAND is an ORIGINAL Excel
        # function (no _xlfn. prefix); use_future_functions prefixes BETA.INV /
        # NORM.INV inside the _invcdf samples, which is correct + LET-consistent.
        ws.write_formula(r0, _WE_COL_U_TEF, "=RAND()")
        ws.write_formula(r0, _WE_COL_U_VULN, "=RAND()")
        ws.write_formula(r0, _WE_COL_U_PL, "=RAND()")
        ws.write_formula(r0, _WE_COL_U_SL, "=RAND()")
        # Inverse-CDF samples (single-sourced from _invcdf).
        ws.write_formula(r0, _WE_COL_TEF, tef_sample)
        ws.write_formula(r0, _WE_COL_VULN, vuln_sample)
        ws.write_formula(r0, _WE_COL_PL, pl_sample)
        ws.write_formula(r0, _WE_COL_SL, sl_sample)
        # SCALAR clips (per-cell refs; MAX/MIN are correct here — NOT boolean form).
        ws.write_formula(r0, _WE_COL_TEF_CLIP, f"=MAX(0, {tef_cell})")
        ws.write_formula(r0, _WE_COL_VULN_CLIP, f"=MIN(1, MAX(0, {vuln_cell}))")
        ws.write_formula(r0, _WE_COL_PL_CLIP, f"=MAX(0, {pl_cell})")
        ws.write_formula(r0, _WE_COL_SL_CLIP, f"=MAX(0, {sl_cell})")
        # Base loss = LEF * LM = MAX(0,tef)*clip(vuln) * (MAX(0,pl)+MAX(0,sl)).
        ws.write_formula(
            r0,
            _WE_COL_LOSS,
            f"={tef_clip_cell}*{vuln_clip_cell}*({pl_clip_cell}+{sl_clip_cell})",
        )
        rows.blank()  # advance the cursor past the row we wrote directly

    return True


def _app_stat(sim_results: dict[str, Any], side: str, kind: str, key: str) -> Any:
    """App (fair_cam) value for one stat-spec row. ``control`` is base ALE minus
    residual ALE (the App's control-value figure)."""
    if kind == "control":
        return _ale(sim_results, "base_risk") - _ale(sim_results, "residual_risk")
    if kind == "ale":
        return _ale(sim_results, side)
    if kind == "var":
        return _var(sim_results, side, key)
    if kind == "es":
        return _es(sim_results, side, key)
    return 0.0


class _Styles:
    """Workbook-scoped xlsxwriter formats, built ONCE and reused (per-cell
    add_format bloats the file). Colors come from the design-system tokens
    (``workbook_theme.WorkbookColors``) so the workbook matches the web + PDF:
    brand section headers/title, success/warning verdict hues, ink2 muted
    notes. Money stays exact ($#,##0); Calibri (Excel default) is kept."""

    def __init__(self, wb: Any) -> None:
        self.title = wb.add_format(
            {"bold": True, "font_size": 14, "bg_color": _Colors.brand, "font_color": _Colors.white}
        )
        self.section_header = wb.add_format(
            {
                "bold": True,
                "bg_color": _Colors.brand,
                "font_color": _Colors.white,
                "bottom": 1,
                "border_color": _Colors.border_subtle,
            }
        )
        self.money = wb.add_format({"num_format": "$#,##0"})
        self.multiplier = wb.add_format({"num_format": "0.0000"})
        # ELAPSED_TIME capability cells: day-counts, not 0-1 scores — a display
        # suffix so 70.0 on the Capability column can't read as a corrupt score.
        self.days = wb.add_format({"num_format": '0.0000 "d"'})
        self.pct = wb.add_format({"num_format": "0.0%"})
        self.flag_ok = wb.add_format(
            {
                "bg_color": _Colors.success_fill,
                "font_color": _Colors.status_success,
                "align": "center",
            }
        )
        self.flag_check = wb.add_format(
            {
                "bg_color": _Colors.warning_fill,
                "font_color": _Colors.status_warning,
                "align": "center",
                "bold": True,
            }
        )
        self.note_muted = wb.add_format({"italic": True, "font_color": _Colors.ink2})
        # Wrapped variants for MERGED prose paragraphs (aesthetic audit C2): a long
        # note lives in a cell merged across the table width with text_wrap, so it
        # never dictates column-A geometry nor overflows the value columns.
        self.note_wrap = wb.add_format(
            {"italic": True, "font_color": _Colors.ink2, "text_wrap": True, "valign": "top"}
        )
        self.body_wrap = wb.add_format({"text_wrap": True, "valign": "top"})
        self.verdict_ok = wb.add_format(
            {
                "bold": True,
                "bg_color": _Colors.success_fill,
                "font_color": _Colors.status_success,
                "font_size": 12,
                "align": "center",
            }
        )
        self.verdict_check = wb.add_format(
            {
                "bold": True,
                "bg_color": _Colors.warning_fill,
                "font_color": _Colors.status_warning,
                "font_size": 12,
                "align": "center",
            }
        )
        self.header = wb.add_format(
            {
                "bold": True,
                "bottom": 1,
                "font_color": _Colors.ink1,
                "border_color": _Colors.border_subtle,
            }
        )
        self.doc_title = wb.add_format(
            {
                "bold": True,
                "font_size": 14,
                "bg_color": _Colors.brand,
                "font_color": _Colors.white,
                "text_wrap": True,
                "valign": "top",
            }
        )
        self.doc_body = wb.add_format({"text_wrap": True, "valign": "top"})
        self.doc_heading = wb.add_format(
            {"bold": True, "text_wrap": True, "valign": "top", "font_color": _Colors.ink1}
        )


class _XlsxRows:
    """Thin row-cursor over an xlsxwriter worksheet.

    xlsxwriter is index-addressed (``write_string(row, col, ...)``) and has no
    openpyxl-style ``append``; this wrapper restores a deterministic top-to-bottom
    cursor so the LET anchor / spill-range / side-by-side row math stays exact.

    Security (Sec-B1/Sec-B2): every cell is written with the TYPE-SPECIFIC writer
    — ``write_string`` for text (so a ``=``/``{=…}``/URL string can NEVER promote
    to a live formula or hyperlink) and ``write_number`` for numerics. The ONLY
    formula written is the trusted, fully-internally-built LET, via
    ``write_dynamic_array_formula`` on a SINGLE anchor cell. Strings still pass
    through ``_neutralize`` for defense-in-depth even though ``write_string``
    already forces a text cell.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._row = 0  # 0-based next free row

    @property
    def next_row_1based(self) -> int:
        """The 1-based index of the NEXT row a write_* call will land on."""
        return self._row + 1

    def _write_num(self, row0: int, col0: int, value: float, fmt: Any = None) -> None:
        if fmt is not None:
            self._ws.write_number(row0, col0, value, fmt)
        else:
            self._ws.write_number(row0, col0, value)

    def _cell(self, row0: int, col0: int, value: Any, fmt: Any = None) -> None:
        if isinstance(value, tuple) and len(value) == 2:
            value, fmt = value
        if value is None or value == "":
            return  # leave the cell genuinely empty (do not write a blank string)
        if isinstance(value, bool):
            self._write_num(row0, col0, int(value), fmt)
        elif isinstance(value, (int, float)):
            self._write_num(row0, col0, float(value), fmt)
        else:
            # EVERY string forced to a text cell: write_string never promotes a
            # leading =/+/-/@/{ to a formula nor a URL to a hyperlink. _neutralize
            # is belt-and-suspenders so the stored bytes are also lead-quoted.
            s = _neutralize(str(value))
            if fmt is not None:
                self._ws.write_string(row0, col0, s, fmt)
            else:
                self._ws.write_string(row0, col0, s)

    def row(self, cells: list[Any]) -> None:
        """Write one row of mixed-type cells (left to right) and advance.

        Each element may be a plain value OR a ``(value, fmt)`` tuple where ``fmt``
        is an xlsxwriter format object (or ``None`` for no format).
        """
        for col0, value in enumerate(cells):
            self._cell(self._row, col0, value)
        self._row += 1

    def blank(self) -> None:
        """Advance past one (empty) spacer row without writing anything."""
        self._row += 1

    def prose(self, text: str, fmt: Any, *, span: int = 6, height: float | None = None) -> None:
        """Write a wrapped prose paragraph MERGED across ``span`` columns at the
        current row, then advance (aesthetic audit C2).

        Long notes are the root cause of the "dead gulf": a 400-char nowrap string
        sitting in column A forces the column (and every downstream renderer /
        Excel auto-fit) enormously wide, pushing the value columns far to the right.
        Merging the note across the table width + ``text_wrap`` keeps column A sized
        to its longest LABEL, so values sit adjacent. ``height`` defaults to an
        estimate from the text length (so the wrapped lines are visible without
        manual row-height fiddling). Text is neutralized defense-in-depth even
        though ``merge_range`` writes a text cell under ``strings_to_formulas=False``.
        """
        s = _neutralize(str(text))
        if span > 1:
            self._ws.merge_range(self._row, 0, self._row, span - 1, s, fmt)
        else:
            self._ws.write_string(self._row, 0, s, fmt)
        if height is None:
            # ~ (span-weighted) chars per wrapped line; conservative floor so a
            # short note still gets one line and a long one gets enough height.
            height = max(15.0, 15.0 * math.ceil(len(s) / 110))
        self._ws.set_row(self._row, height)
        self._row += 1

    def formula_at(self, row0: int, col0: int, formula: str, fmt: Any = None) -> None:
        """Write a TRUSTED internal formula (cell refs only, no user data) at an
        explicit coordinate, with optional format. Does NOT move the cursor."""
        if fmt is not None:
            self._ws.write_formula(row0, col0, formula, fmt)
        else:
            self._ws.write_formula(row0, col0, formula)

    def dynamic_array(self, col0: int, formula: str) -> None:
        """Write the trusted LET as a dynamic-array formula on a SINGLE anchor cell
        (NEVER a multi-cell range — a range pre-fills static zeros that conflict
        with the spill metadata and Excel drops the formula). Advances ONE row; the
        caller MUST reserve the (n_stats-1) cells below for the downward spill."""
        from xlsxwriter.utility import xl_rowcol_to_cell

        anchor = xl_rowcol_to_cell(self._row, col0)
        self._ws.write_dynamic_array_formula(anchor, formula)
        self._row += 1


def build_single_run_let_sheet(
    ws: Any,
    *,
    run: Any,
    org: Any,
    scenario: dict[str, Any],
    sim_results: dict[str, Any],
    reconstructible: bool,
    mults: dict[str, float] | None,
    n: int,
    capped: bool,
    mc_iterations: int,
    max_n: int,
    styles: _Styles,
) -> None:
    """Lay out the single-run LET-spill MC sheet on an xlsxwriter worksheet.

    VERDICT-FIRST layout (1-based rows). The verdict region is a FIXED 13 rows so
    the offset below it is clean (INPUTS lands at row 15):

      1   "VERIFICATION — <scenario name>"  (title)
      2   roll-up verdict banner (merged A2:F2; green when 0 CHECKs, else amber)
      3   (blank)
      4   header: Metric | Excel (LET) | App (fair_cam) | Δ | Δ% | ok?  (freeze below)
      5..13  the 9 stat rows (Excel spill ref vs App, with Δ / Δ% / OK-CHECK flag)
      14  (blank)
      15  "INPUTS"  (section header) + provenance + cap note + dist params +
          composed multipliers + app summary + USD note + tolerance/gate notes
      N   "MECHANISM"  (section header) + LET label/anchor + 9-row spill (col B) +
          worked example.

    IMPLEMENTATION ORDER (KEY FACT — xlsxwriter is index-addressed): the
    below-verdict content (INPUTS + MECHANISM + LET anchor + worked example) is
    written FIRST, seeding the cursor at 0-based row 14 so INPUTS lands on row 15.
    The LET anchor's exact spill row is captured during that pass. THEN the verdict
    region (rows 1-13) is written by explicit coordinate, referencing the spill
    cells. This lets the verdict table sit ABOVE the LET it reads.

    When NOT reconstructible (legacy/drift/BETA-in-slot/unsupported), the LET is NOT
    emitted (it is base+residual coupled); instead the fail-loud cell is written and
    the App base figures are shown App-only with a static amber banner.

    MATH-LOCK: the App ``_app_stat`` numeric VALUES and the LET formula string are
    byte-identical to the pre-styling layout — only coordinates + cell formats
    change. The Δ% column and the banner are FORMULA cells (no stored numbers).
    """
    from idraa.services.verification_workbook_let import scenario_let_formula

    n_stats = len(_LET_STAT_SPEC)

    # Verdict-region geometry — single source of truth (all offsets derive from
    # these, so changing _LET_STAT_SPEC's length can never silently desync the
    # layout). Behavior is byte-identical at n_stats == 9.
    title_row1 = 1  # 1-based
    banner_row1 = 2
    header_row1 = 4
    stat_first_row1 = 5  # first stat row (1-based), after title/banner/blank/header
    stat_last_row1 = stat_first_row1 + n_stats - 1  # 13 when n_stats == 9
    inputs_seed_row0 = stat_last_row1 + 1  # one blank row then INPUTS — 0-based 14

    # --- Decide LET feasibility up front (drives both regions) ----------------
    if reconstructible and mults is not None:
        try:
            let_formula: str | None = scenario_let_formula(scenario, mults, n)
        except (ValueError, TypeError, KeyError, AttributeError):
            # Unsupported distribution / non-scalable node / non-numeric param /
            # unforeseen non-dict node -> fail-loud (never a wrong formula).
            # AttributeError added after the 2026-07-09 prod 500: a None node
            # escaped the tuple and 500'd the whole download instead of
            # degrading this scenario (root-fixed for null SL in
            # scenario_let_formula; this catch backstops the next shape).
            let_formula = None
    else:
        let_formula = None

    # =========================================================================
    # PASS 1 — below-verdict content. Seed the cursor at 0-based row 14 so the
    # INPUTS section header lands on 1-based row 15 (verdict region is rows 1-13).
    # =========================================================================
    rows = _XlsxRows(ws)
    rows._row = inputs_seed_row0  # next write -> INPUTS on 1-based row (stat_last + 2)

    # --- INPUTS section -------------------------------------------------------
    rows.row([("INPUTS", styles.section_header)])
    rows.row(["Run:", run.name or "(unnamed)"])
    rows.row(["Organization:", org.name])
    rows.row(["Scenario:", str(scenario.get("scenario_name") or "(unnamed)")])
    rows.row([f"MC iterations (run): {int(mc_iterations)}"])
    rows.row([f"In-Excel sample count (N): {n}"])
    rows.row([f"Per-run N cap (verification_workbook_max_n): {max_n}"])
    rows.row([f"Random seed: {run.random_seed if run.random_seed is not None else '(none)'}"])
    # Cap note (Task 5): emitted ONLY when the per-run N cap actually binds
    # (mc_iterations > max_n, so N < mc_iterations). When N == mc_iterations no
    # misleading note is written.
    if capped:
        rows.prose(
            f"NOTE: Excel re-run uses {n} of {mc_iterations} trials for "
            f"responsiveness -- still statistically representative. "
            f"mc_iterations ({mc_iterations}) exceeds the per-run N cap "
            f"({max_n}); the in-Excel LET is run at N={n}. Convergence is "
            "demonstrated; exact App-figure agreement is within sampling "
            "error at this N.",
            styles.note_wrap,
        )
    rows.blank()

    # Distribution parameters, one labeled row per node. Values are flattened into a
    # display-only "key=value" text cell (written as a string, never promoted).
    rows.row(["Distribution parameters (frozen scenario inputs):"])
    for node_label, node_key in (
        ("Threat event frequency", "threat_event_frequency"),
        ("Vulnerability", "vulnerability"),
        ("Primary loss", "primary_loss"),
        ("Secondary loss", "secondary_loss"),
    ):
        dist = _norm_dist(scenario.get(node_key)) or {}
        kind = str(dist.get("distribution", "pert")) if dist else "(missing)"
        params = ", ".join(
            f"{k}={v}"
            for k, v in dist.items()
            if k != "distribution" and isinstance(v, (int, float)) and not isinstance(v, bool)
        )
        rows.row([node_label, kind, params])

    rows.blank()
    rows.prose(
        "Composed control multipliers (re-derived via compose_groups):",
        styles.body_wrap,
    )
    if reconstructible and mults is not None:
        for mk, mlabel in (
            ("threat_event_frequency", "TEF mult"),
            ("vulnerability", "Vuln mult"),
            ("primary_loss", "PL mult"),
            ("secondary_loss", "SL mult"),
            ("currency_subtractor_total", "Currency subtractor"),
        ):
            rows.row([mlabel, (float(mults[mk]), styles.multiplier)])
    else:
        rows.row([(_FAIL_LOUD_TEXT, styles.note_muted)])

    rows.blank()
    rows.row(["App (fair_cam) stored summary:"])
    rows.row(["Base ALE", (_ale(sim_results, "base_risk"), styles.money)])
    rows.row(["Residual ALE", (_ale(sim_results, "residual_risk"), styles.money)])

    # Control economics (App only — not sampled; consistent with the explicit path).
    cost = sim_results.get("cost_summary", {}) or {}
    rows.blank()
    rows.row(["Control economics (App only -- not sampled)"])
    # Value adjacent to its label (col B), no dead middle column (C1).
    rows.row(
        ["Total annual cost", (float(cost.get("total_annual_cost", 0.0) or 0.0), styles.money)]
    )
    rows.row(
        [
            "Total risk reduction",
            (float(cost.get("total_risk_reduction", 0.0) or 0.0), styles.money),
        ]
    )
    rows.row(["Net benefit", (float(cost.get("net_benefit", 0.0) or 0.0), styles.money)])
    roi = cost.get("aggregate_roi")
    rows.row(["Aggregate ROI", (float(roi) if roi is not None else "n/a")])
    # Issue #413: the control-value dollars above rest on implementation-calibrated
    # composition weights (fair_cam weights_provenance), not FAIR-Standard-grounded.
    rows.prose(CONTROL_WEIGHT_PROVENANCE_DISCLAIMER, styles.note_wrap)

    rows.blank()
    # Currency note + relocated tolerance-explanation note + gate note (muted).
    rows.row([("All figures in USD (engine model-base currency).", styles.note_muted)])
    # Tolerance prose (VWB-1): describe EXACTLY what the verdict now counts — the
    # core rows only, with the two 99.9 tails shown but excluded from the count.
    n_tail_rows = sum(1 for _s in _LET_STAT_SPEC if _s[3] in _LET_TAIL_APP_KEYS)
    n_core = n_stats - n_tail_rows
    rows.prose(
        f"Tolerance: the verdict banner counts ONLY the {n_core} core metrics — it "
        "flags each OK when |Excel - App| <= 5% of |App| (abs-floor 1 to avoid "
        "div-by-zero). The two 99.9-tail rows (VaR 99.9 and ES 99.9) are EXCLUDED "
        "from the verdict: the 99.9th percentile is stable only when sampled near the "
        "server-side MC cap (100,000 iterations), so at this workbook's capped N it "
        "diverges from the App purely from tail sampling variance. Out of band those "
        f'two rows read "{_LET_TAIL_OK_NOTE}" instead of CHECK, so they never trip '
        "the banner — compare them by eye and widen tolerance there. The Excel column "
        "reads the LET spill cells in the MECHANISM section below.",
        styles.note_wrap,
    )
    rows.prose(_LET_GATE_NOTE, styles.note_wrap)

    # --- MECHANISM section: the LET anchor + its downward spill + worked example
    rows.blank()
    rows.row([("MECHANISM", styles.section_header)])
    rows.prose(
        "LET (metric names in column A; spills its 9-row stat array down column B):",
        styles.body_wrap,
    )
    let_anchor_row = rows.next_row_1based  # the LET anchor lands on the NEXT row

    if let_formula is not None:
        # Labels in A, the LET anchor in B (col 1, 0-based). The 9-element array
        # SPILLS DOWN B, occupying B(anchor)..B(anchor+8). Those B cells MUST stay
        # empty for the spill — so write the metric NAME on every spill row in
        # column A (not just one pointer label on the anchor), then the LET anchor
        # in B. This puts a label beside each spilled value instead of leaving 8
        # empty rows next to anonymous numbers (eye strain).
        for _i, _spec in enumerate(_LET_STAT_SPEC):
            ws.write_string(let_anchor_row - 1 + _i, 0, _neutralize(_spec[0]))
        rows.dynamic_array(1, let_formula)  # anchor at B(anchor); advances 1 row
        let_ok = True
        # Reserve the spill range: skip n_stats-1 rows so the next content starts
        # BELOW B(anchor+8) and cannot collide with the spill (#SPILL! guard). These
        # cells are NEVER written — Excel materializes the spill into them.
        for _ in range(n_stats - 1):
            rows.blank()
    else:
        rows.row([(_FAIL_LOUD_TEXT, styles.note_muted)])
        let_ok = False

    # --- Worked example (Task 4): explicit per-row scalar sampling -> base loss
    # for the FIRST scenario, so the math is inspectable without the spilled LET.
    # Placed BELOW the LET spill range so its column-A..M formulas never overlap
    # the LET spill (column B above). Always emitted (base chain depends on no
    # control composition); falls back to a single note row only if a base
    # distribution is not natively sampleable.
    _write_worked_example(rows, ws, scenario=scenario, styles=styles)

    # =========================================================================
    # PASS 2 — verdict region (1-based rows 1-13), written by explicit coordinate
    # and referencing the LET spill cells captured above.
    # =========================================================================
    # PASS 2 writes by ABSOLUTE coordinate (the ``rows`` cursor is unused here;
    # ``formula_at`` is just the trusted-formula proxy that bypasses the cursor).
    scenario_name = str(scenario.get("scenario_name") or "(unnamed)")
    ws.write_string(title_row1 - 1, 0, _neutralize(f"VERIFICATION — {scenario_name}"), styles.title)

    # Header at 1-based header_row1; freeze panes below it so it stays on screen.
    header_labels = ["Metric", "Excel (LET)", "App (fair_cam)", "Δ", "Δ%", "ok?"]
    for c0, h in enumerate(header_labels):
        ws.write_string(header_row1 - 1, c0, h, styles.header)
    ws.freeze_panes(header_row1, 0)  # freeze below header -> 0-based header_row1

    # Stat rows at 1-based stat_first_row1..stat_last_row1.
    for i, (label, side, kind, key) in enumerate(_LET_STAT_SPEC):
        app_val = _app_stat(sim_results, side, kind, key)
        r1 = stat_first_row1 + i  # 1-based row of this stat
        r0 = r1 - 1  # 0-based
        # Task 10 (Spec-B1): ES rows carry the 95% MC interval as a suffix on
        # the label cell (the fixed 6-col verdict layout — Metric/Excel(LET)/
        # App(fair_cam)/Δ/Δ%/ok? — has no spare column; the label is the one
        # "adjacent" spot that does not disturb any formula/conditional-format
        # column reference). "" for VaR/ALE/control rows (kind != "es").
        label_text = label
        if kind == "es":
            note = _es_ci_annotation(sim_results, side, key)
            if note:
                label_text = f"{label} ({note})"
        if let_ok:
            ws.write_string(r0, 0, _neutralize(label_text))
            rows.formula_at(r0, 1, f"=B{let_anchor_row + i}", styles.money)
            ws.write_number(r0, 2, float(app_val), styles.money)
            rows.formula_at(r0, 3, f"=B{r1}-C{r1}", styles.money)
            rows.formula_at(r0, 4, f"=(B{r1}-C{r1})/MAX(ABS(C{r1}),1)", styles.pct)
            # VWB-1: the two 99.9-tail rows read the tail note (NOT "CHECK") when out
            # of band, so the banner's COUNTIF(...,"CHECK") over the full span never
            # counts them. Core rows keep the plain OK/CHECK gate.
            out_of_band = _LET_TAIL_OK_NOTE if key in _LET_TAIL_APP_KEYS else "CHECK"
            rows.formula_at(
                r0,
                5,
                f'=IF(ABS(B{r1}-C{r1})<=0.05*MAX(ABS(C{r1}),1),"OK","{out_of_band}")',
            )
        else:
            ws.write_string(r0, 0, _neutralize(label_text))
            ws.write_string(r0, 1, "n/a", styles.note_muted)
            ws.write_number(r0, 2, float(app_val), styles.money)
            ws.write_string(r0, 3, "n/a", styles.note_muted)
            ws.write_string(r0, 4, "n/a", styles.note_muted)
            ws.write_string(r0, 5, "n/a", styles.note_muted)

    # Conditional format on the flag column range (col 5, the stat-row span).
    if let_ok:
        ws.conditional_format(
            stat_first_row1 - 1,
            5,
            stat_last_row1 - 1,
            5,
            {"type": "text", "criteria": "containing", "value": "OK", "format": styles.flag_ok},
        )
        ws.conditional_format(
            stat_first_row1 - 1,
            5,
            stat_last_row1 - 1,
            5,
            {
                "type": "text",
                "criteria": "containing",
                "value": "CHECK",
                "format": styles.flag_check,
            },
        )

    # Banner at 1-based banner_row1, merged across A..F. COUNTIF spans the stat rows.
    banner_row0 = banner_row1 - 1
    first, last = stat_first_row1, stat_last_row1  # 1-based flag-column row span
    if let_ok:
        # VWB-1: the COUNTIF spans the full stat range, but only the core rows can
        # emit the literal "CHECK" (the 99.9 tails emit the tail note), so the count
        # is exactly the number of CORE metrics out of tolerance. The banner copy
        # states that explicitly. n_core / n_tail_rows were computed above.
        banner = (
            f'=IF(COUNTIF(F{first}:F{last},"CHECK")=0,'
            f'"ALL {n_core} CORE METRICS WITHIN ±5% TOLERANCE (99.9 tails shown, '
            f'excluded from verdict — see tolerance note)",'
            f'COUNTIF(F{first}:F{last},"CHECK")&" CORE METRIC(S) OUT OF TOLERANCE — '
            f'see ok? column")'
        )
        ws.merge_range(banner_row0, 0, banner_row0, 5, "", styles.verdict_check)
        ws.write_formula(banner_row0, 0, banner, styles.verdict_check)
        # Turn the banner green when there are zero CHECKs (conditional overrides fill).
        ws.conditional_format(
            banner_row0,
            0,
            banner_row0,
            0,
            {
                "type": "formula",
                "criteria": f'=COUNTIF(F{first}:F{last},"CHECK")=0',
                "format": styles.verdict_ok,
            },
        )
    else:
        # Fail-loud: a static amber banner (no live flags -> no COUNTIF/conditional).
        ws.merge_range(
            banner_row0,
            0,
            banner_row0,
            5,
            _neutralize("RESIDUAL NOT RE-DERIVED — see note below"),
            styles.verdict_check,
        )

    # Column widths (C1): column A sized for the longest LABEL (prose now lives in
    # merged wrapped cells, so it no longer inflates A); the value columns B..F set
    # individually so each carries an explicit width (a single ranged set_column can
    # be read back as width-less on the tail columns). The worked-example columns
    # G..M get a compact width so their terse headers/values fit.
    ws.set_column(0, 0, 50)
    for _c in range(1, 6):
        ws.set_column(_c, _c, 16)
    ws.set_column(6, 12, 12)


def _write_let_documentation_sheet(
    ws: Any,
    *,
    run: Any,
    reconstructible: bool,
    max_n: int,
    aggregate_total_max: int | None = None,
    mc_iterations_max: int | None = None,
    styles: _Styles,
) -> None:
    """Write the Documentation sheet for the LET workbook via xlsxwriter.

    Emits the LET-model prose (``_documentation_lines``, the pure shared
    line-builder); every line is neutralized AND written via ``write_string`` so
    nothing can promote. ``max_n`` / ``aggregate_total_max`` are the LET-model caps
    (``verification_workbook_max_n`` / ``verification_workbook_aggregate_total_max``);
    ``mc_iterations_max`` anchors the var_999-stability caveat (100,000).

    The Documentation tab gains legibility formatting: the first non-empty line is
    bold 14pt with text wrap (``styles.doc_title``); section headings (ALL-CAPS
    prefix or ``N. HEADING`` numbered pattern) are bold with text wrap
    (``styles.doc_heading``); body lines get text wrap and valign top
    (``styles.doc_body``). Column A is widened to 80 characters for readable prose.
    """
    doc_lines = list(
        _documentation_lines(
            run=run,
            reconstructible=reconstructible,
            max_n=max_n,
            aggregate_total_max=aggregate_total_max,
            mc_iterations_max=mc_iterations_max,
        )
    )

    ws.set_column(0, 0, 80)

    first_nonempty_written = False
    for row0, line in enumerate(doc_lines):
        neutral = _neutralize(line)
        if not neutral:
            continue
        if not first_nonempty_written:
            # First non-empty line is the document title.
            ws.write_string(row0, 0, neutral, styles.doc_title)
            first_nonempty_written = True
        elif re.match(r"^\d+\.\s", line) or re.match(r"^[A-Z][A-Z ]+[A-Z]", line):
            # Section heading: numbered (e.g. "1. RANDARRAY VOLATILITY.") or
            # ALL-CAPS-prefixed (e.g. "WHAT THIS WORKBOOK IS",
            # "ONE FORMULA PER SCENARIO (no explicit rows).").
            ws.write_string(row0, 0, neutral, styles.doc_heading)
        else:
            ws.write_string(row0, 0, neutral, styles.doc_body)


_SCOPE_NOTE_SINGLE = (
    "Scope: the dollar values in the 'Estimated value range' sections are for "
    "this run's single scenario only — the same control can earn additional "
    "value in other scenarios (an aggregate run's workbook shows the "
    "portfolio-wide figure)."
)
_SCOPE_NOTE_AGGREGATE = (
    "Scope: the dollar values in the 'Estimated value range' sections are "
    "summed across every scenario in this run that the control participates in."
)


def _write_controls_sheet(
    ws: Any,
    *,
    controls_snapshot: list[dict[str, Any]],
    weight_robustness: dict[str, Any] | None = None,
    styles: _Styles,
    combined_effect_hint: str = "the MC sheet INPUTS section",
    scope_note: str = _SCOPE_NOTE_SINGLE,
    help_base_url: str = "",
) -> None:
    """Write the Control Audit sheet (Task 6 / issue #419).

    Emits three blocks per active control:

    1. **Composition LET formulas** — per PROBABILITY-unit assignment, a
       ``LET(_xlpm.opeff, cap*cov*rel, 1-_xlpm.opeff*w)`` formula per target
       FAIR node at the canonical weight (auditable, references input cells).

    2. **Deterministic band-endpoint sensitivity** — Low / Base / High LET
       formulas with the +/-2sigma logit-space weight endpoints embedded as constants
       (from ``band_endpoint_mappings()``). Columns labelled "Low", "Base",
       "High". ELAPSED_TIME and CURRENCY assignments are skipped with a note.

    3. **Emitted stochastic range** — server-computed p5/p50/p95 from
       ``run.weight_robustness.per_control[cid]`` written as VALUES (not
       formulas — the ensemble draws are server-side only). A note row
       distinguishes this block from the deterministic sensitivity above.
       When ``state='insufficient_budget'`` the block is labeled
       "robustness not assessed".

    Security (mirrors the MC sheet): every label goes through ``_neutralize``
    and ``write_string``; formula cells reference only internal cell
    coordinates + numeric constants (no user data). User-supplied control
    name / sub-function slug pass through ``rows.row()`` → ``_neutralize``
    → ``write_string`` (never promoted to a formula).
    """
    from fair_cam.models.composition_topology import (
        GROUP_NODE_MAPPING,
        GROUP_TYPE,
        BooleanGroup,
        GroupType,
        sub_function_to_group,
    )
    from fair_cam.models.sub_function import (
        SUB_FUNCTION_UNITS,
        FairCamSubFunction,
        UnitType,
    )

    from idraa.services.weight_robustness import band_endpoint_mappings
    from idraa.utils.text import humanize_slug

    def _emits_standalone_formula(group: BooleanGroup) -> bool:
        """True iff a single assignment to ``group`` is applied by the engine
        with single-assignment fidelity, so a standalone ``1-E*w`` formula is
        faithful.

        Only OR groups qualify: ``or_compose`` applies a single member directly.
        AND groups compose multiplicatively (``and_compose``) and
        ``group_composition`` pads ABSENT members to 0.0 — a single/partial
        assignment collapses the product to 0 → the engine applies the identity
        ``1 - 0*w = 1.0`` (no benefit), so an emitted ``1-E*w < 1`` would
        overstate the effect.  LEC_RESPONSE (the only weak-AND group) is excluded
        explicitly: ``weak_and_compose`` is a mean of PRESENT members (it does NOT
        pad absent members to 0), but the engine substitutes its node effect with
        the Detection-gated LEC_DETECTION_RESPONSE_PAIR effectiveness, so a
        standalone formula would still be unfaithful.  Deriving the predicate from
        ``GROUP_TYPE`` (plus the explicit LEC_RESPONSE exclusion) covers any future
        AND group automatically.
        """
        return GROUP_TYPE[group] == GroupType.OR and group != BooleanGroup.LEC_RESPONSE

    rows = _XlsxRows(ws)
    # Column A sized for the longest LABEL (humanized sub-function slugs run ~45
    # chars); prose lives in merged wrapped cells so it no longer inflates A (C1/C2).
    ws.set_column(0, 0, 48)
    for _c in range(1, 5):
        ws.set_column(_c, _c, 18)

    rows.row([("Control Audit", styles.title)])
    rows.prose(
        "How much each control is worth, and how that figure moves with the model's "
        "assumptions. The columns walk the calculation: each control's effect on a risk "
        "factor at the model's standard weights, then at the low / typical / high ends of "
        "the plausible weight range. The 'Estimated value range' section below shows the "
        "server-computed dollar ranges from the weight-uncertainty simulations (these are "
        "not recomputed in Excel — there are no raw simulation draws here). The per-formula "
        "figures assume each control acts alone; the engine's actual result combines all "
        f"controls together (see {combined_effect_hint}).",
        styles.body_wrap,
        span=5,
    )
    # Scope disclosure (workbook-labels PR): single-run values are scenario-scoped
    # while aggregate values sum across the run's scenarios — say so, since the
    # same control legitimately shows different dollars in the two workbooks.
    rows.prose(scope_note, styles.note_wrap, span=5)
    # Plain-text URL (NOT write_url): the workbook keeps a zero-hyperlink
    # invariant (strings_to_urls=False, write_string everywhere) so no user
    # string can ever become a clickable URL. The help link is shown as
    # copyable text to preserve that guarantee.
    rows.row(
        [
            (
                f"Learn how to read this: {help_base_url}/help/control-value-robustness",
                styles.note_muted,
            )
        ]
    )
    # #439 Slice-2 κ disclosure (Sec2-I1 / Arch2-N1): make the standalone-vs-coupled
    # seam visible instead of silent. The per-assignment formulas (block 2) and the
    # deterministic low/typical/high sensitivity (block 3) show each control acting
    # ALONE (κ-free, same semantics as the κ=0 catalog seams); only the stored
    # "Estimated value range" (block 4) carries the meta→reliability coupling.
    rows.prose(
        "Per-assignment formulas and deterministic sensitivity show each control "
        "standalone (no meta reliability uplift); the stored ranges include the "
        "meta coupling (kappa).",
        styles.note_wrap,
        span=5,
    )
    # Deduped range explainer (C3): the per-control "Estimated dollar-value range ...
    # computed from the weight-uncertainty simulations" paragraph used to repeat in
    # EVERY control block. It is rendered ONCE here; each block keeps only its
    # "Estimated value range (from N ...)" header + the p5/p50/p95 rows.
    range_explainer = (
        "About the 'Estimated value range' sections below: each is the estimated "
        "dollar-value range for that control, computed server-side from the "
        "weight-uncertainty simulations. These come from the server and are NOT "
        "recomputed in Excel (there are no raw simulation draws here), so they differ "
        "from the low/typical/high deterministic sensitivity, which IS recomputed from "
        "the formulas."
    )
    # Basis disclosure (workbook-labels PR): only mean-basis blobs render a
    # 'Typical-case point' row, so only they get the two-bases sentence — a
    # legacy (typical-basis) run must keep its labels verbatim (see the
    # test_workbook_legacy_basis_keeps_original_labels_verbatim guard).
    # insufficient_budget blobs are stamped basis=="mean" too (run_executor
    # stamps unconditionally) but render only the skip note and NO typical row,
    # so they must not get a sentence describing a row that never appears
    # (bundled-review NTH-1).
    _wr = weight_robustness or {}
    if _wr.get("basis") == "mean" and _wr.get("state") != "insufficient_budget":
        range_explainer += (
            " The 'Typical-case point' row uses a DIFFERENT basis by design: it "
            "prices the typical chain — mode/median point estimates, the "
            "most-likely-year outcome — while the p5/p50/p95 rows price the average "
            "(mean) chain; on tail-heavy portfolios the typical point sits far "
            "below the range."
        )
    rows.prose(range_explainer, styles.note_wrap, span=5)
    rows.blank()

    if not controls_snapshot:
        rows.row([("No controls active on this run.", styles.note_muted)])
        return

    # ── Band-endpoint weights (computed once for all controls) ────────────────
    # Use the pinned sigma from weight_robustness.band so the endpoints reproduce
    # under Settings drift (Sec-I2 reproducibility from the spec).
    pinned_sigma: float | None = None
    if weight_robustness:
        band_meta = weight_robustness.get("band") or {}
        ls = band_meta.get("logit_sigma")
        if isinstance(ls, (int, float)) and ls >= 0.0:
            pinned_sigma = float(ls)

    try:
        endpoints = band_endpoint_mappings(sigma=pinned_sigma)
    except (ValueError, TypeError, KeyError):
        endpoints = None

    wr_state: str = (weight_robustness or {}).get("state") or "not_available"
    draws_used: int = int((weight_robustness or {}).get("draws_used") or 0)
    per_control_wr: dict[str, Any] = (weight_robustness or {}).get("per_control") or {}
    # Mean+typical side-by-side (2026-07-04): "basis" defaults to "typical" for
    # legacy blobs persisted before the mean-basis chain landed (no "basis" key)
    # — see run_executor.py's _build_weight_robustness. Drives the p5/p50/p95
    # row labels below + whether a paired typical-case point row is shown.
    _wr_basis: str = str((weight_robustness or {}).get("basis") or "typical")
    _canonical_value_typical: dict[str, Any] = (weight_robustness or {}).get(
        "canonical_value_typical"
    ) or {}
    # The un-rankable signal is the PAIR set (Spec-I1), NOT a control's own
    # stability_class — mirror the web/PDF surfaces so a "too close to call"
    # control isn't shown as "stable" here (per-control stability_badge collapses
    # unstable→stable by design, leaving the pair set as the sole marker source).
    _indis_ids: set[str] = set()
    for _pair in (weight_robustness or {}).get("indistinguishable_pairs") or []:
        if isinstance(_pair, (list, tuple)):
            _indis_ids.update(str(c) for c in _pair)

    for snap in controls_snapshot:
        ctrl_id = str(snap.get("control_id") or "")
        ctrl_name = str(snap.get("name") or ctrl_id)
        assignments: list[dict[str, Any]] = snap.get("assignments") or []

        rows.row([(f"Control: {ctrl_name} ({ctrl_id})", styles.section_header)])

        # ── Block 1 — Input cells ─────────────────────────────────────────────
        rows.row(
            [
                ("Sub-function", styles.header),
                ("Unit", styles.header),
                ("Capability", styles.header),
                ("Coverage", styles.header),
                ("Reliability", styles.header),
            ]
        )

        # Track per-assignment cell addresses so the composition and band-endpoint
        # LET formulas can reference them by coordinate (never by user-data content).
        asn_cells: list[dict[str, Any]] = []
        for asn in assignments:
            sf_str = str(asn.get("sub_function") or "")
            unit_str = str(asn.get("unit_type") or "probability")
            cap_val = asn.get("capability_value")
            cov_val = float(asn.get("coverage") or 1.0)
            rel_val = float(asn.get("reliability") or 1.0)
            asn_row1 = rows.next_row_1based  # 1-based row of this write
            # C5: show the humanized sub-function name next to the raw slug (raw slug
            # kept first so the value stays auditable). humanize_slug is the SAME
            # canonical humanizer the web/PDF surfaces use (idraa.utils.text).
            human = humanize_slug(sf_str)
            sf_label = f"{sf_str} — {human}" if human and human != sf_str else sf_str
            rows.row(
                [
                    sf_label,
                    unit_str,
                    # Capability may be null (ELAPSED_TIME back-fill path).
                    # ELAPSED_TIME capabilities are day-counts (converted via
                    # exp(-t/τ)), not 0-1 scores — day-suffixed display format.
                    # Numeric value unchanged; blocks 2/3 skip elapsed-time
                    # before touching cap_cell today, and a number format is
                    # display-only either way (bundled-review NTH-2).
                    (
                        float(cap_val) if cap_val is not None else "null",
                        styles.days
                        if unit_str == UnitType.ELAPSED_TIME.value
                        else styles.multiplier,
                    ),
                    (cov_val, styles.multiplier),
                    (rel_val, styles.multiplier),
                ]
            )
            # Column layout (0-based cols → 1-based letters):
            # col 0 = A (sf_str), col 1 = B (unit_str),
            # col 2 = C (cap),    col 3 = D (cov), col 4 = E (rel)
            asn_cells.append(
                {
                    "sf": sf_str,
                    "unit": unit_str,
                    "cap_val": cap_val,
                    "row1": asn_row1,
                    "cap_cell": f"C{asn_row1}",
                    "cov_cell": f"D{asn_row1}",
                    "rel_cell": f"E{asn_row1}",
                }
            )

        rows.blank()

        # ── Block 2 — Composition LET formulas (canonical weights) ───────────
        rows.row(
            [
                (
                    "Control effect per risk factor (this control alone, at the model's "
                    "standard weights):",
                    styles.header,
                )
            ]
        )

        any_opeff_formula = False
        for ac in asn_cells:
            sf_str = ac["sf"]
            try:
                sf_enum = FairCamSubFunction(sf_str)
            except ValueError:
                rows.row([(f"  {sf_str}: unknown sub-function slug — skipped", styles.note_muted)])
                continue

            unit_enum = SUB_FUNCTION_UNITS.get(sf_enum)
            if unit_enum == UnitType.CURRENCY:
                rows.row(
                    [
                        (
                            f"  {sf_str}: CURRENCY (loss-reduction subtractor — no opeff)",
                            styles.note_muted,
                        )
                    ]
                )
                continue
            if unit_enum == UnitType.ELAPSED_TIME:
                rows.row(
                    [
                        (
                            f"  {sf_str}: ELAPSED_TIME — formula uses exp(-t/τ); not shown here",
                            styles.note_muted,
                        )
                    ]
                )
                continue

            # PROBABILITY / PERCENT_REDUCTION: opeff = cap * cov * rel
            group = sub_function_to_group(sf_enum)
            mapping = GROUP_NODE_MAPPING[group]
            if not mapping.targets:
                rows.row(
                    [
                        (
                            f"  {sf_str}: no standalone node target "
                            f"(AND-pair child — effect via {group.value})",
                            styles.note_muted,
                        )
                    ]
                )
                continue

            if not _emits_standalone_formula(group):
                # Conditional groups: a standalone 1-E*w would overstate the
                # benefit because the engine does not apply a single assignment
                # to this group directly (see _emits_standalone_formula).
                if group == BooleanGroup.LEC_RESPONSE:
                    # D8 Detection-gate: the engine substitutes the
                    # LEC_DETECTION_RESPONSE_PAIR effectiveness for the raw
                    # LEC_RESPONSE effectiveness.  Without a Detection partner
                    # the pair eff is None → identity (no magnitude benefit).
                    rows.row(
                        [
                            (
                                f"  {sf_str}: on stealth (confidentiality/integrity) "
                                f"scenarios this control's effect depends on a detection "
                                f"control also being present; availability scenarios "
                                f"self-detect, so there it applies without one. Either "
                                f"way a standalone figure would overstate it — the "
                                f"actual combined effect is in {combined_effect_hint}.",
                                styles.note_muted,
                            )
                        ]
                    )
                else:
                    # AND / weak-AND group: and_compose pads absent members to
                    # 0.0, so a single/partial assignment collapses the product
                    # to 0 → the engine applies identity (no benefit).
                    rows.row(
                        [
                            (
                                f"  {sf_str}: this control's effect needs its whole "
                                f"group of controls present, so one control on its own "
                                f"gives no standalone benefit. The actual combined "
                                f"effect is in {combined_effect_hint}.",
                                styles.note_muted,
                            )
                        ]
                    )
                continue

            cap_ref = ac["cap_cell"]
            cov_ref = ac["cov_cell"]
            rel_ref = ac["rel_cell"]
            opeff_expr = f"{cap_ref}*{cov_ref}*{rel_ref}"

            for node in mapping.targets:
                w = mapping.weights[node]
                # LET(_xlpm.opeff, cap*cov*rel, 1-_xlpm.opeff*w)
                # xlsxwriter use_future_functions=True will prefix LET → _xlfn.LET
                let_formula = f"=LET(_xlpm.opeff,{opeff_expr},1-_xlpm.opeff*{w})"
                formula_row1 = rows.next_row_1based
                rows.row([f"  {sf_str} → {node} (w={w:.4f})"])
                # Write to col C (col0=2) of the same row the label occupies
                ws.write_formula(formula_row1 - 1, 2, let_formula, styles.multiplier)
                any_opeff_formula = True

        if not any_opeff_formula:
            rows.row(
                [
                    (
                        # Workbook-labels PR: the old "CURRENCY-only or all
                        # ELAPSED_TIME" wording was wrong for pair-gated /
                        # conditional PROBABILITY assignments (they ARE
                        # opeff-bearing — they just emit no standalone formula).
                        f"(No standalone-formula assignments — every assignment "
                        f"above is pair-gated, conditional, elapsed-time, or "
                        f"currency; the combined effect is in {combined_effect_hint}.)",
                        styles.note_muted,
                    )
                ]
            )

        rows.blank()

        # ── Block 3 — Band-endpoint sensitivity (low/base/high) ──────────────
        if endpoints is not None:
            rows.row(
                [
                    (
                        "How the effect shifts across the plausible weight range "
                        "(this control alone):",
                        styles.header,
                    )
                ]
            )
            if pinned_sigma is None:
                rows.row(
                    [
                        (
                            "Note: this run carries no saved weight range, so the "
                            "low/high ends use the current default spread.",
                            styles.note_muted,
                        )
                    ]
                )
            rows.row(
                [
                    "Control effect on risk factor",
                    ("Low", styles.header),
                    ("Typical", styles.header),
                    ("High", styles.header),
                ]
            )

            any_ep_row = False
            for ac in asn_cells:
                sf_str = ac["sf"]
                try:
                    sf_enum = FairCamSubFunction(sf_str)
                except ValueError:
                    continue
                unit_enum = SUB_FUNCTION_UNITS.get(sf_enum)
                if unit_enum in (UnitType.CURRENCY, UnitType.ELAPSED_TIME):
                    continue
                group = sub_function_to_group(sf_enum)
                mapping = GROUP_NODE_MAPPING[group]
                if not mapping.targets:
                    continue
                if not _emits_standalone_formula(group):
                    # Conditional group (AND / weak-AND / Detection-gated) — no
                    # standalone band formula (mirrors the Block 2 note: actual
                    # multiplier is in the MC sheet INPUTS section).
                    continue

                cap_ref = ac["cap_cell"]
                cov_ref = ac["cov_cell"]
                rel_ref = ac["rel_cell"]
                opeff_expr = f"{cap_ref}*{cov_ref}*{rel_ref}"

                for node in mapping.targets:
                    # Fetch the perturbed weight from the endpoint mapping.
                    # Fall back to the canonical weight if the key is somehow absent.
                    w_low = endpoints["low"][group].weights.get(node, mapping.weights[node])
                    w_base = endpoints["base"][group].weights.get(node, mapping.weights[node])
                    w_high = endpoints["high"][group].weights.get(node, mapping.weights[node])

                    low_let = f"=LET(_xlpm.opeff,{opeff_expr},1-_xlpm.opeff*{w_low:.8f})"
                    base_let = f"=LET(_xlpm.opeff,{opeff_expr},1-_xlpm.opeff*{w_base:.8f})"
                    high_let = f"=LET(_xlpm.opeff,{opeff_expr},1-_xlpm.opeff*{w_high:.8f})"

                    ep_row1 = rows.next_row_1based
                    rows.row([f"  {sf_str} → {node}"])
                    ws.write_formula(ep_row1 - 1, 1, low_let, styles.multiplier)
                    ws.write_formula(ep_row1 - 1, 2, base_let, styles.multiplier)
                    ws.write_formula(ep_row1 - 1, 3, high_let, styles.multiplier)
                    any_ep_row = True

            if not any_ep_row:
                rows.row([("(No PROBABILITY assignments with node targets.)", styles.note_muted)])
        else:
            rows.row([("Band-endpoint sensitivity unavailable.", styles.note_muted)])

        rows.blank()

        # ── Block 4 — Stochastic range (server-computed weight ensemble) ──────
        k_label = f"{draws_used}" if draws_used else "the"
        rows.row(
            [
                (
                    f"Estimated value range (from {k_label} weight-uncertainty simulations)",
                    styles.section_header,
                )
            ]
        )

        wr_entry: dict[str, Any] = per_control_wr.get(ctrl_id) or {}

        if weight_robustness is None:
            rows.row(
                [
                    (
                        "Value range not available (this run was created before this "
                        "feature, or carries no weight-uncertainty data).",
                        styles.note_muted,
                    )
                ]
            )
        elif wr_state == "insufficient_budget":
            rows.row(
                [
                    (
                        "Ranking-stability check skipped — not enough simulation budget "
                        "to test it on this run.",
                        styles.note_muted,
                    )
                ]
            )
        elif not wr_entry:
            rows.row(
                [
                    (
                        f"No value-range data found for control {ctrl_id}.",
                        styles.note_muted,
                    )
                ]
            )
        else:
            # C3: the per-control boilerplate paragraph that used to repeat here is
            # now rendered ONCE at the top of the sheet; this block keeps only the
            # header (above) + the p5/p50/p95 rows.
            p5 = wr_entry.get("reduction_p5")
            p50 = wr_entry.get("reduction_p50")
            p95 = wr_entry.get("reduction_p95")
            sc = str(wr_entry.get("stability_class") or "")
            # Mean+typical side-by-side (2026-07-04): mean-basis blobs relabel the
            # three rows as average-basis (p5/p50/p95 across weight draws are now
            # MEAN-chain figures, not the historical typical/median chain) and gain
            # a fourth row for the paired typical-case point (from
            # canonical_value_typical). Legacy (typical-basis) blobs keep today's
            # three labels byte-identical — MATH-LOCK: no LET formula strings
            # touched here, prose/labels only.
            if _wr_basis == "mean":
                rows.row(
                    [
                        "Low end (p5 across weight draws, average basis):",
                        (float(p5) if isinstance(p5, (int, float)) else "n/a", styles.money),
                    ]
                )
                rows.row(
                    [
                        "Central (p50 across weight draws, average basis):",
                        (float(p50) if isinstance(p50, (int, float)) else "n/a", styles.money),
                    ]
                )
                rows.row(
                    [
                        "High end (p95 across weight draws, average basis):",
                        (float(p95) if isinstance(p95, (int, float)) else "n/a", styles.money),
                    ]
                )
                _typical_pt = _canonical_value_typical.get(ctrl_id)
                rows.row(
                    [
                        "Typical-case point ($ value):",
                        (
                            float(_typical_pt) if isinstance(_typical_pt, (int, float)) else "n/a",
                            styles.money,
                        ),
                    ]
                )
            else:
                rows.row(
                    [
                        "Low end (p5, $ value):",
                        (float(p5) if isinstance(p5, (int, float)) else "n/a", styles.money),
                    ]
                )
                rows.row(
                    [
                        "Typical case (p50, $ value):",
                        (float(p50) if isinstance(p50, (int, float)) else "n/a", styles.money),
                    ]
                )
                rows.row(
                    [
                        "High end (p95, $ value):",
                        (float(p95) if isinstance(p95, (int, float)) else "n/a", styles.money),
                    ]
                )
            _ranking = (
                "too close to call"
                if ctrl_id in _indis_ids
                else stability_badge({"stability_class": sc})
            )
            rows.row(["Ranking:", _ranking])

        rows.blank()
        rows.blank()


def build_single_run_let_workbook(run: Any, org: Any, *, base_url: str = "") -> bytes:
    """Build the single-run verification workbook via the LET-spill path (.xlsx bytes).

    Parallel to ``build_verification_workbook`` but uses ONE self-contained LET
    dynamic-array formula per scenario instead of N explicit formula rows, written
    with **xlsxwriter** (openpyxl cannot emit the dynamic-array ``cm`` metadata a
    spilling LET requires). Carries the same fail-loud gating (LegacySnapshotError /
    unit-table drift / non-param-scalable residual node / unsupported distribution
    -> labeled fail-loud cell, base App figures shown). N is capped at
    ``settings.verification_workbook_max_n`` so the workbook recalcs responsively at
    the capped N.

    Security hardening (Sec-B1/Sec-B2): the workbook is built with
    ``strings_to_formulas=False`` + ``strings_to_urls=False`` (so xlsxwriter never
    auto-promotes a ``=``/``{=…}`` string to a live formula nor a URL string to a
    hyperlink), ``use_future_functions=True`` (so the BARE function names in the LET
    are auto-prefixed with ``_xlfn.``). Every label/text is written via
    ``write_string``; the ONLY formula is the trusted internal LET (+ the internal
    side-by-side cell-ref formulas).
    """
    import gc
    import io as _io

    import xlsxwriter

    from idraa.config import get_settings

    settings = get_settings()
    max_n = settings.verification_workbook_max_n

    mc_iterations = int(run.mc_iterations or 0)
    n = min(mc_iterations, max_n) if mc_iterations > 0 else max_n
    capped = mc_iterations > max_n

    sim_results: dict[str, Any] = run.simulation_results or {}
    controls_snapshot: list[dict[str, Any]] = run.controls_snapshot or []

    sis = run.scenario_inputs_snapshot or {}
    scenarios = sis.get("scenarios") or []
    scenario = scenarios[0] if scenarios else {}

    reconstructible, mults = _residual_reconstructible(
        controls_snapshot,
        availability_self_detection=(scenario.get("effect") == "availability"),
    )

    buf = _io.BytesIO()
    wb = xlsxwriter.Workbook(
        buf,
        {
            "use_future_functions": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "in_memory": True,
        },
    )
    mc_ws = wb.add_worksheet("MC")
    doc_ws = wb.add_worksheet("Documentation")
    ctrl_ws = wb.add_worksheet("Controls")
    styles = _Styles(wb)

    build_single_run_let_sheet(
        mc_ws,
        run=run,
        org=org,
        scenario=scenario,
        sim_results=sim_results,
        reconstructible=reconstructible,
        mults=mults,
        n=n,
        capped=capped,
        mc_iterations=mc_iterations,
        max_n=max_n,
        styles=styles,
    )
    _write_let_documentation_sheet(
        doc_ws,
        run=run,
        reconstructible=reconstructible,
        max_n=max_n,
        aggregate_total_max=settings.verification_workbook_aggregate_total_max,
        mc_iterations_max=settings.mc_iterations_max,
        styles=styles,
    )
    _write_controls_sheet(
        ctrl_ws,
        controls_snapshot=controls_snapshot,
        weight_robustness=getattr(run, "weight_robustness", None),
        styles=styles,
        scope_note=_SCOPE_NOTE_SINGLE,
        help_base_url=base_url,
    )

    wb.close()  # serializes the workbook into buf
    out = buf.getvalue()
    del wb, mc_ws, doc_ws, ctrl_ws, buf, sim_results, controls_snapshot, mults
    gc.collect()
    return out


# --- Task 3: AGGREGATE LET-spill assembly (per-scenario LET + residual roll-up) -
# An aggregate run re-runs the FAIR MC for each reconstructible scenario as ONE
# self-contained LET dynamic-array formula at its OWN single-cell anchor (each LET
# generates its OWN independent RANDARRAY draws — mirrors the engine's per-scenario
# SeedSequence.spawn: native_control_aware.py:107,146-148, where each scenario gets
# an independent random stream and draws are NOT shared across scenarios). The
# roll-up validates ALE ADDITIVITY only:
#
#   residual-ALE roll-up = SUM of the per-scenario residual-ALE spill cells
#                          (the LET's 2nd stat, _LET_STAT_SPEC index 1)
#   base-ALE roll-up     = SUM of the per-scenario base-ALE spill cells
#                          (the LET's 1st stat, _LET_STAT_SPEC index 0)
#   control value        = base roll-up - residual roll-up
#
# The aggregate VaR/ES rows show the Excel column as "n/a (not re-derived in this
# workbook)" — tails are NOT additive (you cannot sum per-scenario VaR/ES), so the
# in-Excel roll-up does not re-derive them. The App column shows the App's REAL
# aggregate VaR/ES (read off the run's aggregate_with_controls dict, which the App
# computes from the aggregate loss distribution = per-iteration SUM of the scenarios'
# loss samples, scenarios drawn independently). Only ALE is rolled up in Excel.
#
# K-OF-M SUBSET (carried from the legacy aggregate, _build_aggregate_workbook):
# in-Excel MC is built for at most K = settings.verification_workbook_max_scenarios
# RECONSTRUCTIBLE scenarios within the M-scenario run; the Excel residual roll-up is
# compared against the App figure for EXACTLY those K scenarios (the sum of their App
# residual ALEs), NOT the full-M aggregate.
#
# HONEST DEGRADED (T11): a NON-reconstructible scenario (legacy/malformed snapshot,
# unit-table drift, BETA-in-magnitude / non-param-scalable residual) has NO faithful
# residual LET. It is EXCLUDED from the residual roll-up entirely (its base magnitude
# is NEVER summed in — base >= residual, so summing it would silently INFLATE the
# residual figure and fabricate a delta) and listed SUMMARY-ONLY with the labeled
# fail-loud cell + its App base ALE. Mirrors the single-run fail-loud path.


def _agg_let_collect_scenarios(
    run: Any,
    *,
    k: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, int]:
    """Partition an aggregate run's scenarios into the in-Excel (reconstructible,
    within the K cap) set and the summary-only set, reusing the SAME subset/gating
    semantics as the legacy ``_build_aggregate_workbook``.

    Returns ``(in_excel, summary_only, m, n_excluded_kcap, n_excluded_nonrecon)``.
    Each ``in_excel`` entry is ``{name, scenario, mults, app_res_ale, app_base_ale}``
    (``scenario`` is the engine-canonical-keyed input dict for scenario_let_formula).
    Each ``summary_only`` entry is ``{name, app_res_ale, app_base_ale, reason}``.
    """
    sim_results: dict[str, Any] = run.simulation_results or {}
    per_scenario: list[dict[str, Any]] = sim_results.get("per_scenario") or []
    controls_snapshot: list[dict[str, Any]] = run.controls_snapshot or []

    by_id: dict[str, dict[str, Any]] = {str(ps.get("scenario_id")): ps for ps in per_scenario}
    order = _agg_scenario_order(run, per_scenario)
    m = len(order)

    sis = run.scenario_inputs_snapshot or {}
    inputs_by_id: dict[str, dict[str, Any]] = {
        str(s.get("scenario_id")): s for s in (sis.get("scenarios") or [])
    }

    in_excel: list[dict[str, Any]] = []
    summary_only: list[dict[str, Any]] = []
    n_excluded_kcap = 0
    n_excluded_nonrecon = 0
    for pos, sid in enumerate(order):
        ps = by_id.get(sid, {})
        name = str(ps.get("scenario_name") or inputs_by_id.get(sid, {}).get("scenario_name") or sid)
        app_res_ale = _agg_app_residual_ale(ps)
        app_base_ale = _agg_app_base_ale(ps)
        if pos >= k:
            n_excluded_kcap += 1
            summary_only.append(
                {
                    "name": name,
                    "app_res_ale": app_res_ale,
                    "app_base_ale": app_base_ale,
                    "reason": "beyond the K scenario cap (in-Excel MC not built)",
                }
            )
            continue

        scen_inputs = inputs_by_id.get(sid, {})
        scen_controls = _per_scenario_controls_snapshot(run, controls_snapshot, sid)
        # Mirror the single-run path (~:1798): thread each scenario's own effect so the
        # workbook residual matches the engine's availability_self_detection treatment.
        # .get("effect") == "availability" is None-safe (returns False for missing key).
        reconstructible, mults = _residual_reconstructible(
            scen_controls,
            availability_self_detection=(scen_inputs.get("effect") == "availability"),
        )
        # _residual_sample_formulas is the SAME param-scalability gate the single-run
        # and legacy aggregate paths use: it returns feasible=False for a BETA in a
        # tef/pl/sl slot (scaled_params rejects it) or any non-scalable residual node.
        feasible = False
        if reconstructible:
            feasible, _ = _residual_sample_formulas(scen_inputs, mults)
        if not (reconstructible and feasible and mults is not None):
            n_excluded_nonrecon += 1
            summary_only.append(
                {
                    "name": name,
                    "app_res_ale": app_res_ale,
                    "app_base_ale": app_base_ale,
                    "reason": (
                        "residual not reconstructible (legacy/drift/non-scalable) "
                        "-- App base ALE shown"
                    ),
                }
            )
            continue

        in_excel.append(
            {
                "name": name,
                "scenario": scen_inputs,
                "mults": mults,
                "app_res_ale": app_res_ale,
                "app_base_ale": app_base_ale,
            }
        )

    return in_excel, summary_only, m, n_excluded_kcap, n_excluded_nonrecon


def build_aggregate_let_sheet(
    ws: Any,
    *,
    run: Any,
    org: Any,
    in_excel: list[dict[str, Any]],
    summary_only: list[dict[str, Any]],
    k: int,
    m: int,
    n: int,
    capped: bool,
    mc_iterations: int,
    max_n: int,
    aggregate_total_max: int,
    agg_scaled: bool,
    n_excluded_kcap: int,
    n_excluded_nonrecon: int,
    styles: _Styles,
) -> None:
    """Lay out the aggregate LET-spill MC sheet on an xlsxwriter worksheet.

    One LET per reconstructible in-Excel scenario, each at its OWN single-cell
    anchor in column B (each LET generates its own independent RANDARRAY draws),
    stacked vertically with its 9-row stat array spilling DOWN. Then a roll-up
    block: residual-ALE roll-up = SUM of the per-scenario residual-ALE spill cells
    (B<anchor+1>), base-ALE roll-up = SUM of the base-ALE cells (B<anchor>), control
    value = base - residual. For the aggregate VaR/ES rows the Excel column is the
    text "n/a (not re-derived in this workbook)" (tails are NOT additive, so the
    in-Excel roll-up does not re-derive them) while the App column shows the App's
    REAL aggregate VaR/ES read off ``run.simulation_results["aggregate_with_controls"]``
    (legacy payloads predating aggregate tail metrics show a 're-run to populate'
    note). The K-subset App ALE figure (sum of those scenarios' App residual ALEs)
    sits beside the Excel residual roll-up. The excluded (K-cap or non-reconstructible)
    scenarios are listed summary-only.

    K-subset symmetry: a scenario can pass ``_agg_let_collect_scenarios``' gate (so
    it arrives in ``in_excel``) yet ``scenario_let_formula`` still RAISE at build
    time. Such an emit-fail scenario is routed to ``summary_only`` (this function may
    APPEND to the passed-in list) with the fail-loud-labeled reason, and is excluded
    from BOTH the Excel residual SUM and the App comparison sum + every "X of M"
    count. Excel-roll-up membership == App-sum membership == label count, ALWAYS:
    all three derive from the EMITTED set (scenarios whose cells were appended).

    Mirrors ``build_single_run_let_sheet`` security/writer mechanics: every label
    via ``write_string`` (through ``_neutralize``), the ONLY formulas the trusted
    internal LETs (single anchors via ``write_dynamic_array_formula``) + the roll-up
    SUM / delta cell-ref formulas (cell refs + constants only, no user data).
    """
    from idraa.services._view_model_helpers import has_tail_metrics
    from idraa.services.verification_workbook_let import scenario_let_formula

    n_stats = len(_LET_STAT_SPEC)
    # Spill offsets into a scenario's 9-row stat array (must match _LET_STAT_SPEC).
    base_ale_offset = 0  # _LET_STAT_SPEC[0] == "Base ALE"
    res_ale_offset = 1  # _LET_STAT_SPEC[1] == "Residual ALE"

    # --- Verdict-region geometry — single source of truth ----------------------
    # The verdict region is: title (1) + banner (2) + blank (3) + header (4) +
    # n_additive ADDITIVE-ALE comparison rows (each carries an OK/CHECK flag) +
    # n_tail aggregate tail rows (no flags) + one trailing blank gap. Every offset
    # below derives from these counts + n_additive_first; NO independently-hardcoded
    # row literal (a future spec-length change can never silently desync the layout).
    # The ADDITIVE rows ARE the flag rows the banner's COUNTIF spans; the tail rows
    # have NO flags so they are excluded from the COUNTIF range.
    n_additive = 3  # Residual ALE roll-up, Base ALE roll-up, Control value
    n_tail = len(_AGG_TAIL_ROW_SPEC)  # aggregate VaR/ES rows (no flags)
    title_row1 = 1
    banner_row1 = 2
    header_row1 = 4
    additive_first_row1 = header_row1 + 1  # first additive-ALE row (1-based) -> 5
    additive_last_row1 = additive_first_row1 + n_additive - 1  # last flag row
    tail_first_row1 = additive_last_row1 + 1
    tail_last_row1 = tail_first_row1 + n_tail - 1
    # One blank gap below the comparison block, then PASS 1 content begins. The
    # cursor is seeded so the provenance section header lands just below the gap.
    pass1_seed_row0 = tail_last_row1 + 1  # 0-based; provenance lands on tail_last+2

    # --- K-subset symmetry: resolve the EMITTED set BEFORE writing anything --------
    # A scenario passed _agg_let_collect_scenarios' gate (so it's in in_excel) yet
    # scenario_let_formula can still RAISE at build time (belt-and-suspenders for an
    # unforeseen non-scalable shape). When that happens we must keep Excel-roll-up
    # membership, App-sum membership, AND the "X of M" label count IDENTICAL: the
    # emit-fail scenario is routed to summary_only (listed, not silently dropped) and
    # is excluded from BOTH the Excel SUM and the App comparison sum + every count.
    # Pre-resolving the LET formula up front lets the header/label use the emitted
    # count (computed below), not the optimistic len(in_excel).
    emitted: list[dict[str, Any]] = []  # scenarios whose LET actually emits cells
    n_excluded_emit_fail = 0
    for scen in in_excel:
        try:
            scen["_let_formula"] = scenario_let_formula(scen["scenario"], scen["mults"], n)
        except (ValueError, TypeError, KeyError, AttributeError):
            # AttributeError: see the single-run catch — 2026-07-09 prod 500
            # backstop for unforeseen non-dict nodes.
            # Passed the gate but failed to emit -> symmetric exclusion: route to
            # summary_only with a fail-loud-labeled reason; NOT summed anywhere.
            n_excluded_emit_fail += 1
            summary_only.append(
                {
                    "name": scen["name"],
                    "app_res_ale": scen["app_res_ale"],
                    "app_base_ale": scen["app_base_ale"],
                    "reason": f"LET emit failed at build -- {_FAIL_LOUD_TEXT}",
                }
            )
            continue
        emitted.append(scen)

    # All counts/labels derive from the EMITTED set so Excel-roll-up membership ==
    # App-sum membership == label count, always (even on the emit-fail path).
    n_in = len(emitted)
    subset_res_ale = sum(scen["app_res_ale"] for scen in emitted)
    subset_base_ale = sum(scen["app_base_ale"] for scen in emitted)

    # =========================================================================
    # PASS 1 — below-verdict content. Seed the cursor just below the comparison
    # block so the provenance section header lands on pass1_seed_row0+1. This pass
    # captures the per-scenario spill-cell coords AND the roll-up SUM cell coords the
    # PASS-2 verdict rows reference.
    # =========================================================================
    rows = _XlsxRows(ws)
    rows._row = pass1_seed_row0

    # --- Provenance + K-of-M disclosure (relocated below the verdict) ---------
    rows.row([("PROVENANCE / K-of-M DISCLOSURE", styles.section_header)])
    rows.row(["Run:", run.name or "(unnamed)"])
    rows.row(["Organization:", org.name])
    rows.row([f"MC iterations (run): {int(mc_iterations)}"])
    rows.row([f"In-Excel sample count per scenario (N): {n}"])
    rows.row([f"Per-run N cap (verification_workbook_max_n): {max_n}"])
    rows.row(
        [f"Aggregate ΣN cap (verification_workbook_aggregate_total_max): {aggregate_total_max}"]
    )
    rows.row([f"Scenarios in run (M): {m}"])
    rows.row([f"Scenario cap (verification_workbook_max_scenarios, K): {k}"])
    rows.prose(
        f"in-Excel MC shown for {n_in} of {m} scenarios "
        "(residual-reconstructible AND within the K cap AND LET emitted); each "
        "scenario block is an INDEPENDENT LET that generates its OWN RANDARRAY "
        "draws (mirrors the engine's per-scenario spawn). The roll-up is the SUM "
        f"of ONLY those scenarios' residual-ALE spill cells. {len(summary_only)} "
        f"scenario(s) are excluded from the roll-up and listed summary-only "
        f"below: {n_excluded_kcap} beyond the K = {k} scenario cap, "
        f"{n_excluded_nonrecon} whose residual was not reconstructible "
        f"(legacy/drift/non-scalable -- App base ALE shown), and "
        f"{n_excluded_emit_fail} that passed the gate but failed to emit a LET "
        "at build (never summed into the residual roll-up). The roll-up is "
        "compared against the App figure for EXACTLY those EMITTED scenarios "
        "(sum of their App residual ALEs), NOT the full-M aggregate. This "
        "in-Excel roll-up validates ALE ADDITIVITY across scenarios. The tail "
        "metrics (VaR/ES) are NOT additive, so the Excel column for those rows is "
        "n/a (not re-derived in this workbook); the App column there shows the "
        "App's REAL aggregate VaR/ES (computed from the aggregate loss "
        "distribution -- the per-iteration sum of the scenarios' loss samples, "
        "scenarios drawn independently). NOTE: those aggregate VaR/ES ASSUME the "
        "scenarios are independent; if they share a common cause (positively "
        "correlated), the true aggregate tail is HIGHER than shown -- treat it as "
        "a lower bound. (Aggregate ALE is additive regardless of correlation.)",
        styles.note_wrap,
    )
    # Cap note (Task 5): emitted ONLY when a cap actually binds — either the per-run
    # N ceiling clamped mc_iterations, or the aggregate ΣN scale-down lowered N below
    # that ceiling. When nothing binds (N == mc_iterations and ΣN fits), no
    # misleading note is written.
    if agg_scaled:
        rows.prose(
            f"NOTE: Excel re-run uses N of {mc_iterations} trials for "
            f"responsiveness -- still statistically representative. Each "
            f"scenario's in-Excel LET is run at N={n} (scaled down from the "
            f"per-run cap {max_n} so the total across {n_in} reconstructible "
            f"scenario(s) stays within the aggregate cap of "
            f"{aggregate_total_max} trials).",
            styles.note_wrap,
        )
    elif capped:
        rows.prose(
            f"NOTE: Excel re-run uses {n} of {mc_iterations} trials for "
            f"responsiveness -- still statistically representative. "
            f"mc_iterations ({mc_iterations}) exceeds the per-run N cap "
            f"({max_n}); each scenario's in-Excel LET is run at N={n}.",
            styles.note_wrap,
        )
    # Relocated gate note (M365 instructions), muted.
    rows.prose(_LET_GATE_NOTE, styles.note_wrap)
    rows.blank()

    # --- One LET per reconstructible scenario, each at its OWN single anchor ---
    # Track each scenario's base-ALE + residual-ALE spill cell coordinate so the
    # roll-up references them by explicit coordinate (no brittle hand-counting).
    base_ale_cells: list[str] = []
    res_ale_cells: list[str] = []

    rows.row([("PER-SCENARIO MONTE CARLO", styles.section_header)])
    rows.prose(
        "Per-scenario LET blocks (metric names in column A; each spills its 9-row "
        "stat array down column B):",
        styles.body_wrap,
    )
    # Iterate the EMITTED set only: scenario_let_formula was pre-resolved above (emit
    # failures were already routed to summary_only), so every block here DOES emit and
    # its cells ARE appended -> Excel-roll-up membership == App-sum membership == n_in.
    for scen in emitted:
        let_formula: str = scen["_let_formula"]
        rows.row([(f"Scenario block: {scen['name']}", styles.section_header)])
        anchor_row = rows.next_row_1based  # the LET anchor lands on the NEXT row
        # Metric name on EVERY spill row in column A (not just one pointer label on
        # the anchor) so the 9-row stat array reads with a label beside each value
        # instead of 8 empty rows next to anonymous numbers (eye strain). Column A
        # is untouched by the spill (which materializes only into column B).
        for _i, _spec in enumerate(_LET_STAT_SPEC):
            ws.write_string(anchor_row - 1 + _i, 0, _neutralize(_spec[0]))
        rows.dynamic_array(1, let_formula)  # anchor at B<anchor_row>; advances 1 row
        base_ale_cells.append(f"B{anchor_row + base_ale_offset}")
        res_ale_cells.append(f"B{anchor_row + res_ale_offset}")
        # Reserve the spill range so the next block can't collide (#SPILL! guard).
        for _ in range(n_stats - 1):
            rows.blank()
        rows.blank()  # spacer between scenario blocks

    # --- Roll-up block: SUM of the per-scenario spill cells -------------------
    # ALE is additive across scenarios; tails are NOT. The verdict region's
    # ADDITIVE-ALE rows reference these captured roll-up cells by coordinate.
    rows.prose("Aggregate roll-up (ALE additivity; tails non-additive):", styles.body_wrap)
    res_rollup_row = rows.next_row_1based
    # Empty subset (all scenarios excluded) -> the empty sum is 0 (a valid numeric).
    res_rollup_formula = "=" + "+".join(res_ale_cells) if res_ale_cells else "=0"
    base_rollup_formula = "=" + "+".join(base_ale_cells) if base_ale_cells else "=0"
    ws.write_string(res_rollup_row - 1, 0, _neutralize("Residual ALE roll-up (Excel)"))
    ws.write_formula(res_rollup_row - 1, 1, res_rollup_formula, styles.money)
    rows.blank()
    base_rollup_row = rows.next_row_1based
    ws.write_string(base_rollup_row - 1, 0, _neutralize("Base ALE roll-up (Excel)"))
    ws.write_formula(base_rollup_row - 1, 1, base_rollup_formula, styles.money)
    rows.blank()
    control_row = rows.next_row_1based
    ws.write_string(control_row - 1, 0, _neutralize("Control value (Base - Residual roll-up)"))
    ws.write_formula(control_row - 1, 1, f"=B{base_rollup_row}-B{res_rollup_row}", styles.money)
    rows.blank()

    # --- Summary-only remainder (excluded scenarios) -------------------------
    if summary_only:
        rows.blank()
        rows.row(
            [
                (
                    "EXCLUDED SCENARIOS (summary-only — excluded from the residual roll-up)",
                    styles.section_header,
                )
            ]
        )
        rows.row(
            [
                ("Scenario", styles.header),
                ("App residual ALE", styles.header),
                ("App base ALE", styles.header),
                ("Reason excluded", styles.header),
            ]
        )
        for scen in summary_only:
            # The fail-loud label is carried in the reason cell for non-reconstructible
            # scenarios so the exclusion is unmistakable; K-capped scenarios carry the
            # cap reason. Both are written via write_string (never promoted).
            rows.row(
                [
                    scen["name"],
                    (float(scen["app_res_ale"]), styles.money),
                    (float(scen["app_base_ale"]), styles.money),
                    str(scen.get("reason", "")),
                ]
            )
        # An explicit fail-loud marker row so a non-reconstructible OR emit-fail
        # exclusion is surfaced with the canonical labeled text (mirrors the
        # single-run cell). Both kinds carry the labeled text in their reason cell;
        # this trailing marker is the unmistakable belt-and-suspenders surfacing.
        if n_excluded_nonrecon or n_excluded_emit_fail:
            rows.row([(_FAIL_LOUD_TEXT, styles.note_muted)])

    # =========================================================================
    # PASS 2 — verdict region (1-based rows 1..tail_last_row1), written by explicit
    # coordinate and referencing the roll-up SUM cells captured in PASS 1.
    # =========================================================================
    ws.write_string(
        title_row1 - 1,
        0,
        _neutralize(f"AGGREGATE VERIFICATION — {run.name or '(unnamed)'}"),
        styles.title,
    )

    # Header at header_row1; freeze panes below it so it stays on screen.
    header_labels = ["Metric", "Excel (LET roll-up)", "App (fair_cam)", "Δ", "Δ%", "ok?"]
    for c0, h in enumerate(header_labels):
        ws.write_string(header_row1 - 1, c0, h, styles.header)
    ws.freeze_panes(header_row1, 0)  # freeze below header -> 0-based header_row1

    let_ok = n_in > 0

    # --- ADDITIVE-ALE comparison rows (these carry the OK/CHECK flags) --------
    # Each: Excel col = a ref to the captured roll-up SUM cell; App col = the numeric
    # K-subset sum; Δ = =B{r}-C{r}; Δ% = =(B{r}-C{r})/MAX(ABS(C{r}),1); flag =
    # =IF(ABS(B{r}-C{r})<=0.05*MAX(ABS(C{r}),1),"OK","CHECK"). All cell-ref/constant
    # formulas (no user data) — matches the single-run verdict-region forms.
    # (label, excel_rollup_cell_ref, app_spec) where app_spec is either a float
    # (a stored App numeric) or a str App-column FORMULA (cell-ref only). The
    # control-value App is a FORMULA (=C{base}-C{res}) NOT a stored number so the
    # money multiset stays byte-identical to the pre-reorder layout (which never
    # stored a control-value App number — it was an Excel formula only). MATH-LOCK.
    res_row1 = additive_first_row1
    base_row1 = additive_first_row1 + 1
    additive_rows: tuple[tuple[str, str, float | str], ...] = (
        (f"Residual ALE roll-up ({n_in} of {m} scenarios)", f"B{res_rollup_row}", subset_res_ale),
        (f"Base ALE roll-up ({n_in} of {m} scenarios)", f"B{base_rollup_row}", subset_base_ale),
        (
            "Control value (Base - Residual roll-up)",
            f"B{control_row}",
            # App control value derived in-sheet from the two App cells above (no
            # stored number) -> preserves the App-value multiset under the reorder.
            f"=C{base_row1}-C{res_row1}",
        ),
    )
    for i, (label, excel_ref, app_spec) in enumerate(additive_rows):
        r1 = additive_first_row1 + i
        r0 = r1 - 1
        ws.write_string(r0, 0, _neutralize(label))
        ws.write_formula(r0, 1, f"={excel_ref}", styles.money)
        if isinstance(app_spec, str):
            ws.write_formula(r0, 2, app_spec, styles.money)
        else:
            ws.write_number(r0, 2, float(app_spec), styles.money)
        if let_ok:
            ws.write_formula(r0, 3, f"=B{r1}-C{r1}", styles.money)
            ws.write_formula(r0, 4, f"=(B{r1}-C{r1})/MAX(ABS(C{r1}),1)", styles.pct)
            ws.write_formula(r0, 5, f'=IF(ABS(B{r1}-C{r1})<=0.05*MAX(ABS(C{r1}),1),"OK","CHECK")')
        else:
            # Degenerate: no emitted scenarios -> the roll-up cell is the empty-sum 0
            # (a valid numeric). Show the Excel ref + App value but NO live flag.
            ws.write_string(r0, 3, "n/a", styles.note_muted)
            ws.write_string(r0, 4, "n/a", styles.note_muted)
            ws.write_string(r0, 5, "n/a", styles.note_muted)

    # --- Aggregate tail rows (VaR/ES) — Excel col "n/a", App col numeric ------
    # Tails are NOT additive across scenarios, so the Excel column is the
    # not-re-derived text; the App column shows the App's REAL aggregate VaR/ES read
    # off the run's persisted aggregate_with_controls dict (legacy payloads predating
    # aggregate tail metrics show the suppress-not-fabricate legacy n/a). The ok?
    # column is a static "n/a" (no flag) so these rows are NEVER in the banner COUNTIF.
    agg_with: dict[str, Any] = (run.simulation_results or {}).get(
        "aggregate_with_controls", {}
    ) or {}
    agg_has_tail = has_tail_metrics(agg_with)
    # _var/_es read sim_results.get(side).get(...); wrap agg_with under a synthetic
    # side so the SAME app-stat helpers (single-sourced) read the aggregate values.
    agg_wrap = {"aggregate_with_controls": agg_with}
    for i, (label, kind, key) in enumerate(_AGG_TAIL_ROW_SPEC):
        r0 = (tail_first_row1 - 1) + i
        ws.write_string(r0, 0, _neutralize(label))
        ws.write_string(r0, 1, _AGG_TAIL_EXCEL_NA, styles.note_muted)
        if not agg_has_tail:
            ws.write_string(r0, 2, _neutralize(_AGG_TAIL_APP_LEGACY_NA), styles.note_muted)
        elif kind == "var":
            ws.write_number(
                r0, 2, float(_var(agg_wrap, "aggregate_with_controls", key)), styles.money
            )
        else:  # es
            ws.write_number(
                r0, 2, float(_es(agg_wrap, "aggregate_with_controls", key)), styles.money
            )
        ws.write_string(r0, 3, "", styles.note_muted)  # Δ blank (no Excel value)
        # Task 10 (Spec-B1): ES rows get the 95% MC interval in the adjacent
        # (otherwise-unused) column E, next to the App value in column C.
        # Column A's label ("ES 95" etc.) stays byte-identical — it is looked
        # up verbatim elsewhere (test_aggregate_let_var_es_app_col_sane_ladder).
        if kind == "es" and agg_has_tail:
            note = _es_ci_annotation(agg_wrap, "aggregate_with_controls", key)
            ws.write_string(r0, 4, _neutralize(note), styles.note_muted)
        ws.write_string(r0, 5, "n/a", styles.note_muted)  # ok? n/a (no flag)

    # --- Conditional format + banner (COUNTIF spans ONLY the additive flag rows) ---
    flag_first0 = additive_first_row1 - 1
    flag_last0 = additive_last_row1 - 1
    banner_row0 = banner_row1 - 1
    if let_ok:
        ws.conditional_format(
            flag_first0,
            5,
            flag_last0,
            5,
            {"type": "text", "criteria": "containing", "value": "OK", "format": styles.flag_ok},
        )
        ws.conditional_format(
            flag_first0,
            5,
            flag_last0,
            5,
            {
                "type": "text",
                "criteria": "containing",
                "value": "CHECK",
                "format": styles.flag_check,
            },
        )
        # Banner COUNTIF spans ONLY the additive-ALE flag rows (NOT the tail rows).
        first, last = additive_first_row1, additive_last_row1
        banner = (
            f'=IF(COUNTIF(F{first}:F{last},"CHECK")=0,'
            f'"AGGREGATE ALE ROLL-UP WITHIN ±5% TOLERANCE",'
            f'COUNTIF(F{first}:F{last},"CHECK")&" ROLL-UP METRIC(S) OUT OF TOLERANCE — see ok? column")'
        )
        ws.merge_range(banner_row0, 0, banner_row0, 5, "", styles.verdict_check)
        ws.write_formula(banner_row0, 0, banner, styles.verdict_check)
        ws.conditional_format(
            banner_row0,
            0,
            banner_row0,
            0,
            {
                "type": "formula",
                "criteria": f'=COUNTIF(F{first}:F{last},"CHECK")=0',
                "format": styles.verdict_ok,
            },
        )
    else:
        # Degenerate (zero emitted scenarios): static amber banner, no live flags.
        ws.merge_range(
            banner_row0,
            0,
            banner_row0,
            5,
            _neutralize("NO RECONSTRUCTIBLE SCENARIOS — see summary"),
            styles.verdict_check,
        )

    # Column widths (C1): column A sized for the longest LABEL; value columns B..F
    # set individually so each carries an explicit width. Prose lives in merged
    # wrapped cells so it no longer inflates column A.
    ws.set_column(0, 0, 46)
    for _c in range(1, 6):
        ws.set_column(_c, _c, 18)


def build_aggregate_let_workbook(run: Any, org: Any, *, base_url: str = "") -> bytes:
    """Build the AGGREGATE verification workbook via the LET-spill path (.xlsx bytes).

    Parallel to ``build_single_run_let_workbook`` but emits ONE self-contained LET
    per reconstructible scenario (each at its own single-cell anchor, generating its
    own independent RANDARRAY draws) and rolls up the per-scenario residual/base ALE
    spill cells via SUM (validating ALE ADDITIVITY in Excel). The aggregate VaR/ES
    rows show the Excel column as "n/a (not re-derived in this workbook)" (tails are
    not additive) and the App column as the App's REAL aggregate VaR/ES (read off the
    run's aggregate_with_controls dict). Carries the legacy aggregate's K-of-M
    subset semantics + T11 honest-degraded exclusion (non-reconstructible scenarios
    excluded from the roll-up, summary-only with the fail-loud cell).

    Per-scenario N starts at ``min(run.mc_iterations, verification_workbook_max_n)``
    and is then scaled DOWN proportionally (integer floor, floor of
    ``_LET_MIN_N``) so the TOTAL ``Σ N`` across the reconstructible in-Excel
    scenarios stays <= ``verification_workbook_aggregate_total_max``. With K'
    reconstructible scenarios drawing N each, ``Σ N = K' * N``; if that exceeds the
    aggregate cap, N is reset to ``aggregate_total_max // K'``.

    Security hardening identical to the single-run LET path: ``strings_to_formulas
    =False`` + ``strings_to_urls=False`` + ``use_future_functions=True``, every label
    via ``write_string``; the only formulas are the trusted internal LETs + roll-up
    cell-ref/SUM formulas.
    """
    import gc
    import io as _io

    import xlsxwriter

    from idraa.config import get_settings

    settings = get_settings()
    k = settings.verification_workbook_max_scenarios
    max_n = settings.verification_workbook_max_n
    aggregate_total_max = settings.verification_workbook_aggregate_total_max

    mc_iterations = int(run.mc_iterations or 0)

    in_excel, summary_only, m, n_excluded_kcap, n_excluded_nonrecon = _agg_let_collect_scenarios(
        run, k=k
    )

    # Per-run N ceiling first, then the aggregate ΣN scale-down.
    per_run_n = min(mc_iterations, max_n) if mc_iterations > 0 else max_n
    per_run_capped = mc_iterations > max_n  # mc_iterations clamped by the per-run ceiling
    n, agg_scaled = _agg_scaled_n(per_run_n, len(in_excel), aggregate_total_max)
    # The "cap binds" flag covers EITHER the per-run N ceiling clamping mc_iterations
    # OR the aggregate ΣN scale-down lowering N below the per-run ceiling.
    capped = per_run_capped or agg_scaled

    buf = _io.BytesIO()
    wb = xlsxwriter.Workbook(
        buf,
        {
            "use_future_functions": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "in_memory": True,
        },
    )
    agg_ws = wb.add_worksheet("Aggregate")
    doc_ws = wb.add_worksheet("Documentation")
    ctrl_ws = wb.add_worksheet("Controls")
    styles = _Styles(wb)

    build_aggregate_let_sheet(
        agg_ws,
        run=run,
        org=org,
        in_excel=in_excel,
        summary_only=summary_only,
        k=k,
        m=m,
        n=n,
        capped=capped,
        mc_iterations=mc_iterations,
        max_n=max_n,
        aggregate_total_max=aggregate_total_max,
        agg_scaled=agg_scaled,
        n_excluded_kcap=n_excluded_kcap,
        n_excluded_nonrecon=n_excluded_nonrecon,
        styles=styles,
    )
    _write_let_documentation_sheet(
        doc_ws,
        run=run,
        reconstructible=True,
        max_n=max_n,
        aggregate_total_max=aggregate_total_max,
        mc_iterations_max=settings.mc_iterations_max,
        styles=styles,
    )
    _write_controls_sheet(
        ctrl_ws,
        controls_snapshot=run.controls_snapshot or [],
        weight_robustness=getattr(run, "weight_robustness", None),
        styles=styles,
        # VWB2-1: the aggregate workbook has no "MC" sheet — point the caveats
        # at where its composed effects actually live.
        combined_effect_hint=("the Aggregate sheet's per-scenario blocks (composed multipliers)"),
        scope_note=_SCOPE_NOTE_AGGREGATE,
        help_base_url=base_url,
    )

    wb.close()  # serializes the workbook into buf
    out = buf.getvalue()
    del wb, agg_ws, doc_ws, ctrl_ws, buf, in_excel, summary_only
    gc.collect()
    return out


def build_verification_workbook(run: Any, org: Any, *, base_url: str = "") -> bytes:
    """Public entry point: build the verification workbook (.xlsx bytes) for a
    COMPLETED run, routing single vs aggregate runs to the LET-spill path.

    Aggregate-aware dispatcher (Arch-I1): an AGGREGATE run (run_type == "aggregate"
    OR ``simulation_results["per_scenario"]`` present) routes to
    ``build_aggregate_let_workbook`` (one LET per reconstructible scenario + a
    residual-ALE roll-up); every other COMPLETED run routes to
    ``build_single_run_let_workbook`` (one LET for the single scenario). Both build
    with **xlsxwriter** via ``write_dynamic_array_formula`` — the verification
    workbook is a handful of self-contained LET dynamic-array formulas that
    generate their own RANDARRAY Monte Carlo inside Excel, NOT explicit per-row
    formula cells (the prior openpyxl explicit-row generator was removed in Task 7).

    The route (``download_verification_workbook``) calls THIS function; the
    single/aggregate split lives here so the route stays type-agnostic.

    Args:
        run: object carrying ``name``, ``run_type``, ``mc_iterations``,
            ``random_seed``, ``controls_snapshot``, ``scenario_inputs_snapshot``,
            ``simulation_results`` (the RiskAnalysisRun ORM attrs), and — for
            aggregate runs — ``aggregate_scenario_ids`` /
            ``aggregate_control_ids_per_scenario``.
        org: object carrying ``name``.

    Returns:
        The .xlsx file as bytes.
    """
    sim_results: dict[str, Any] = run.simulation_results or {}
    if _is_aggregate(run, sim_results):
        return build_aggregate_let_workbook(run, org, base_url=base_url)
    return build_single_run_let_workbook(run, org, base_url=base_url)


# --- Aggregate-run shared helpers (K-of-M ordering / App-stat extraction) ------
# Shared by the aggregate LET-spill path (_agg_let_collect_scenarios). The
# explicit-per-row openpyxl aggregate builder was removed in Task 7; the aggregate
# verification workbook is now ONE LET per reconstructible scenario (each generating
# its own independent RANDARRAY draws — mirroring the engine's per-scenario
# SeedSequence.spawn) with a residual-ALE roll-up. See build_aggregate_let_workbook.


def _is_aggregate(run: Any, sim_results: dict[str, Any]) -> bool:
    """Aggregate iff run_type is the AGGREGATE enum/value OR the persisted
    simulation_results carries a per_scenario list (the aggregate-only key)."""
    run_type = getattr(run, "run_type", None)
    rt_val = str(getattr(run_type, "value", run_type)).lower() if run_type is not None else ""
    if rt_val == "aggregate":
        return True
    return isinstance(sim_results.get("per_scenario"), list)


def _agg_scenario_order(run: Any, per_scenario: list[dict[str, Any]]) -> list[str]:
    """Authoritative scenario ordering for the aggregate. Prefer the run's frozen
    ``aggregate_scenario_ids`` (the spawn-index order the engine used,
    run_executor.py:1086); fall back to per_scenario list order."""
    ids = getattr(run, "aggregate_scenario_ids", None)
    if ids:
        return [str(s) for s in ids]
    return [str(ps.get("scenario_id")) for ps in per_scenario]


def _agg_app_residual_ale(ps: dict[str, Any]) -> float:
    """K-subset comparison reads the residual ALE at the EXACT nested path
    ``per_scenario[i]["residual_risk"]["annualized_loss_expectancy"]`` — NOT a
    flat ``["ale"]`` (no such key; per_scenario entries are full SINGLE-shape
    payloads, run_executor.py:422-430)."""
    return float(ps.get("residual_risk", {}).get("annualized_loss_expectancy", 0.0) or 0.0)


def _agg_app_base_ale(ps: dict[str, Any]) -> float:
    """K-subset base ALE at ``per_scenario[i]["base_risk"]["annualized_loss_expectancy"]``."""
    return float(ps.get("base_risk", {}).get("annualized_loss_expectancy", 0.0) or 0.0)


def _per_scenario_controls_snapshot(
    run: Any, controls_snapshot: list[dict[str, Any]], scenario_id: str
) -> list[dict[str, Any]]:
    """Subset the RUN-LEVEL controls_snapshot (the deduplicated union for an
    aggregate run) to the controls active on ``scenario_id``, using the frozen
    ``aggregate_control_ids_per_scenario`` map (str-keyed). When that map is None
    (engine full-universe fallback, native_control_aware.py:170), every scenario
    uses the full universe."""
    per_scen = getattr(run, "aggregate_control_ids_per_scenario", None)
    if not per_scen:
        return controls_snapshot
    active = {str(c) for c in per_scen.get(str(scenario_id), [])}
    return [s for s in controls_snapshot if str(s.get("control_id")) in active]
