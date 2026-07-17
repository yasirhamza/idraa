"""View-model helpers for the dashboard (omicron-1).

Pure functions: no DB, no HTTP, no fair_cam imports. Mirrors
``services/run_view_model.py`` and ``services/aggregate_run_view_model.py``.

Helpers:
    display_name_fallback         — for runs with run.name=None
    build_top_scenarios           — Q10=D1=a hybrid (prefer aggregate, fall back to singles)
    build_recent_run_row          — Q13=D4=b $ALE column derivation
    build_residual_ale_card       — Q12=D3=a percent-only formatting

(F9/F10/F11 add the other three helpers.)
"""

from __future__ import annotations

import datetime as _datetime
import itertools
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from idraa.models.enums import ControlDomain
from idraa.models.risk_analysis_run import RunStatus, RunType
from idraa.services.coverage import CoverageResult, coverage


class _RevenueProvider(Protocol):
    """Minimal protocol for ``build_residual_ale_card`` org argument."""

    @property
    def annual_revenue(self) -> Any: ...


class _RunLike(Protocol):
    """Minimal protocol for the run argument across view-model helpers.

    Captures only the attributes ``display_name_fallback`` /
    ``build_top_scenarios`` / ``build_recent_run_row`` actually read,
    so unit-test stubs (`@dataclass _StubRun`) satisfy the type without
    `# type: ignore[arg-type]`.
    """

    id: uuid.UUID
    name: str | None
    run_type: RunType
    status: RunStatus
    scenario_id: uuid.UUID | None
    aggregate_scenario_ids: list[str] | None
    created_at: _datetime.datetime
    simulation_results: dict[str, Any] | None


@dataclass(frozen=True)
class TopScenarioRow:
    scenario_id: uuid.UUID
    scenario_name: str
    residual_ale: float
    base_ale: float | None  # None on the SINGLE-fallback path
    source_run_id: uuid.UUID
    source: Literal["aggregate", "single"]


@dataclass(frozen=True)
class RecentRunRow:
    id: uuid.UUID
    display_name: str
    run_type: RunType
    status: RunStatus
    created_at: _datetime.datetime
    headline_ale: float | None  # None for non-COMPLETED or simulation_results=None


def display_name_fallback(run: _RunLike, scenario_name: str | None = None) -> str:
    """Q15 fallback for runs without a user-supplied name.

    AGGREGATE: ``"Aggregate · {n_scenarios} scenarios"``
    SINGLE w/ scenario_name: ``"{scenario_name}"``
    SINGLE w/o scenario_name: ``"Run {id.hex[:8]}"``

    NO date suffix: the run's ``created_at`` is rendered separately in the
    dashboard's localized "Created" column via the ``format_datetime`` /
    ``<time data-localize>`` pipeline. Baking ``created_at.strftime(...)``
    into this label here would bypass the localizer and render off-by-one
    for users west of UTC (issue #263; forbidden by the CLAUDE.md UI
    rendering conventions).
    """
    if run.run_type == RunType.AGGREGATE:
        n = len(run.aggregate_scenario_ids or [])
        return f"Aggregate · {n} scenarios"
    if scenario_name:
        return scenario_name
    short = run.id.hex[:8]
    return f"Run {short}"


def build_top_scenarios(
    latest_aggregate: _RunLike | None,
    fallback_singles_by_scenario: Mapping[uuid.UUID, _RunLike],
    scenario_names_by_id: Mapping[uuid.UUID, str],
    *,
    top_n: int = 5,
) -> tuple[list[TopScenarioRow], int]:
    """Returns (top_n_rows_sorted_desc_by_residual_ale, total_scenarios_with_runs).

    Q10=D1=a: prefer aggregate's per_scenario rows; fall back to ranking
    each scenario's latest COMPLETED SINGLE run by residual ALE when no
    AGGREGATE exists. Tied ALE values resolve by ``scenario_name``
    ascending (deterministic).

    Aggregate path uses ``ps['scenario_name']`` from the per_scenario row.
    Fallback path uses ``scenario_names_by_id.get(scenario_id, '(unknown)')`` —
    NEVER ``run.name`` (run name and scenario name are different concepts).
    """
    if latest_aggregate is not None and latest_aggregate.simulation_results is not None:
        per_scenario = latest_aggregate.simulation_results.get("per_scenario", [])
        agg_rows: list[TopScenarioRow] = []
        for ps in per_scenario:
            sid_raw = ps.get("scenario_id")
            if not sid_raw:
                continue  # Defensive: malformed row missing scenario_id
            agg_rows.append(
                TopScenarioRow(
                    scenario_id=uuid.UUID(sid_raw),
                    scenario_name=ps.get("scenario_name", "(unknown)"),
                    residual_ale=float(
                        ps.get("residual_risk", {}).get("annualized_loss_expectancy", 0.0)
                    ),
                    base_ale=float(ps.get("base_risk", {}).get("annualized_loss_expectancy", 0.0)),
                    source_run_id=latest_aggregate.id,
                    source="aggregate",
                )
            )
        agg_rows.sort(key=lambda r: (-r.residual_ale, r.scenario_name))
        return agg_rows[:top_n], len(agg_rows)

    # Fallback: rank latest SINGLE per scenario by residual ALE.
    single_rows: list[TopScenarioRow] = []
    for scenario_id, run in fallback_singles_by_scenario.items():
        sr = run.simulation_results or {}
        residual_ale = float(sr.get("residual_risk", {}).get("annualized_loss_expectancy", 0.0))
        single_rows.append(
            TopScenarioRow(
                scenario_id=scenario_id,
                scenario_name=scenario_names_by_id.get(scenario_id, "(unknown)"),
                residual_ale=residual_ale,
                base_ale=None,
                source_run_id=run.id,
                source="single",
            )
        )
    single_rows.sort(key=lambda r: (-r.residual_ale, r.scenario_name))
    return single_rows[:top_n], len(single_rows)


def build_recent_run_row(
    run: _RunLike,
    scenario_name: str | None,
) -> RecentRunRow:
    """Q13=D4=b: derive the $ALE headline column for the recent-runs feed.

    COMPLETED AGGREGATE → simulation_results['aggregate_with_controls']['annualized_loss_expectancy']
    COMPLETED SINGLE    → simulation_results['residual_risk']['annualized_loss_expectancy']
    QUEUED / RUNNING / FAILED / CANCELLED → None  (template renders '—')
    simulation_results is None → None

    display_name uses run.name when set, else display_name_fallback().
    """
    headline_ale: float | None = None
    if run.status == RunStatus.COMPLETED and run.simulation_results is not None:
        sr = run.simulation_results
        if run.run_type == RunType.AGGREGATE:
            ale = sr.get("aggregate_with_controls", {}).get("annualized_loss_expectancy")
        else:
            ale = sr.get("residual_risk", {}).get("annualized_loss_expectancy")
        if ale is not None:
            headline_ale = float(ale)

    display_name = run.name if run.name else display_name_fallback(run, scenario_name)

    return RecentRunRow(
        id=run.id,
        display_name=display_name,
        run_type=run.run_type,
        status=run.status,
        created_at=run.created_at,
        headline_ale=headline_ale,
    )


def build_residual_ale_card(
    latest_aggregate: _RunLike | None,
    org: _RevenueProvider,
) -> dict[str, Any] | None:
    """Q12=D3=a: percent-only formatting (two decimals).

    Returns ``{"value": float, "pct_revenue": float | None}`` when
    ``latest_aggregate`` exists; ``None`` otherwise.

    ``pct_revenue`` is ``None`` when ``org.annual_revenue`` is unset or
    zero; the template branches to a "Set annual revenue" subtitle.
    """
    if latest_aggregate is None or latest_aggregate.simulation_results is None:
        return None
    agg_with = latest_aggregate.simulation_results.get("aggregate_with_controls", {})
    ale = float(agg_with.get("annualized_loss_expectancy", 0.0))

    pct_revenue: float | None = None
    if org.annual_revenue is not None:
        revenue = float(org.annual_revenue)
        if revenue > 0:
            pct_revenue = (ale / revenue) * 100

    return {"value": ale, "pct_revenue": pct_revenue}


# ---------------------------------------------------------------------------
# Task 3 (dashboard redesign #476-#480): posture verdict, budget card,
# FAIR-CAM/framework control coverage, scenario-library coverage.
# ---------------------------------------------------------------------------


class _ControlLike(Protocol):
    """Minimal protocol for ``build_control_coverage``'s org-controls argument.

    Only the attributes the builder actually reads: the derived FAIR-CAM
    domains (``Control.domains`` — a frozenset property built off
    ``ControlFunctionAssignment`` rows) and the per-framework tag fields.
    """

    @property
    def domains(self) -> frozenset[ControlDomain]: ...

    nist_csf_functions: list[str]
    compliance_mappings: dict[str, Any]


def _verdict_from_probability(
    p: float, probability_threshold: float
) -> Literal["within", "exceeds"]:
    """Shared decision rule: appetite is exceeded iff P(loss>=amount) > tolerance.probability."""
    return "exceeds" if p > probability_threshold else "within"


def build_posture_verdict(
    residual_samples: Sequence[float],
    tolerance: Mapping[str, float] | None,
) -> Literal["within", "exceeds"] | None:
    """Risk-appetite posture verdict from raw residual-loss MC samples.

    ``P(loss >= tolerance['amount']) = mean(sample >= amount)`` over
    ``residual_samples``; ``"exceeds"`` when that probability is greater
    than ``tolerance['probability']``, else ``"within"``. Returns ``None``
    when no tolerance is configured (nothing to compare against) — the
    panel then omits the verdict badge entirely.

    Caller note: ``residual_samples`` is the raw per-iteration residual-loss
    array. On the dashboard's data-fetching path that array lives in the
    ``run_samples`` table, which is explicitly NOT to be loaded there
    (``models/run_samples.py``: "Loaded only for full-distribution plotting
    / CSV export — never on list/dashboard paths", the #294 perf fix).
    ``services/dashboard.py`` wiring therefore calls
    ``build_posture_verdict_from_lec`` instead, which derives an equivalent
    probability from the loss-exceedance curve already persisted in the
    run's slim ``simulation_results`` summary. This function stays the pure,
    directly-testable per-sample primitive and remains available to any
    future caller that DOES hold the raw array (e.g. a run-detail page).
    """
    if tolerance is None:
        return None
    samples = list(residual_samples)
    amount = float(tolerance["amount"])
    p = (sum(1 for s in samples if s >= amount) / len(samples)) if samples else 0.0
    return _verdict_from_probability(p, float(tolerance["probability"]))


def _interpolate_exceedance_probability(
    lec_points: Sequence[Mapping[str, float]], amount: float
) -> float:
    """P(loss >= amount) via linear interpolation over a loss-exceedance curve.

    ``lec_points`` (e.g. ``dual_lec['with_controls']``) is a dense set of
    ``{"loss": $, "probability": p}`` points already computed off the raw MC
    samples at persist time (``services/run_executor.py``'s
    ``_build_loss_exceedance_curve`` — 100 log-spaced loss points covering
    the sample range). Linear interpolation between the two bracketing
    points is a reasonable approximation at that density. Clamps to the
    curve's endpoint probabilities outside its domain (below the min sampled
    loss -> the curve's highest recorded probability; above the max sampled
    loss -> the curve's lowest, typically 0.0).
    """
    if not lec_points:
        return 0.0
    pts = sorted(lec_points, key=lambda pt: pt["loss"])
    lo = pts[0]  # adapter-iter: ok — curve endpoint (lowest loss), an intentional clamp
    hi = pts[-1]  # adapter-iter: ok — curve endpoint (highest loss), an intentional clamp
    if amount <= lo["loss"]:
        return lo["probability"]
    if amount >= hi["loss"]:
        return hi["probability"]
    for a, b in itertools.pairwise(pts):
        if a["loss"] <= amount <= b["loss"]:
            if b["loss"] == a["loss"]:
                return a["probability"]
            t = (amount - a["loss"]) / (b["loss"] - a["loss"])
            return a["probability"] + t * (b["probability"] - a["probability"])
    return hi["probability"]  # defensive — unreachable given the clamps above


def build_posture_verdict_from_lec(
    lec_points: Sequence[Mapping[str, float]] | None,
    tolerance: Mapping[str, float] | None,
) -> Literal["within", "exceeds"] | None:
    """Dashboard-wiring variant of ``build_posture_verdict``.

    Sources the exceedance probability from a loss-exceedance curve — already
    present in the run's slim ``simulation_results`` summary — instead of the
    raw per-iteration sample array. See ``build_posture_verdict``'s docstring
    for why the raw array is off-limits on the dashboard path. Returns
    ``None`` when there's no tolerance configured OR no curve data (e.g. a
    legacy pre-loss-exceedance-curve run, or a degenerate all-zero-loss run).
    """
    if tolerance is None or not lec_points:
        return None
    p = _interpolate_exceedance_probability(lec_points, float(tolerance["amount"]))
    return _verdict_from_probability(p, float(tolerance["probability"]))


def posture_appetite_detail(
    lec_points: Sequence[Mapping[str, float]] | None,
    tolerance: Mapping[str, float] | None,
) -> dict[str, Any] | None:
    """Posture verdict + a near-threshold flag, from the loss-exceedance curve.

    Returns ``{"verdict": "within"|"exceeds", "near_threshold": bool}`` — or
    ``None`` under the same guards as ``build_posture_verdict_from_lec`` (no
    tolerance / no curve). ``near_threshold`` is True when
    ``|P(loss >= amount) - tolerance.probability|`` falls within a small band:
    the LEC interpolation plus Monte-Carlo sampling noise make a
    boundary-adjacent verdict genuinely uncertain, so the UI softens the hard
    within/exceeds binary there (2026-07-04 methodology review, item N1). Band =
    ``max(0.5 percentage points, 15% of the tolerance probability)``.
    """
    if tolerance is None or not lec_points:
        return None
    p = _interpolate_exceedance_probability(lec_points, float(tolerance["amount"]))
    prob = float(tolerance["probability"])
    band = max(0.005, prob * 0.15)
    return {
        "verdict": _verdict_from_probability(p, prob),
        "near_threshold": abs(p - prob) <= band,
    }


def interpolate_loss_at_probability(
    lec_points: Sequence[Mapping[str, float]], probability: float
) -> float | None:
    """Loss at a given exceedance probability — the EXACT inverse of
    ``_interpolate_exceedance_probability``: linear interpolation in
    probability between the two bracketing curve points, endpoint-clamped.
    Returns None on an empty curve. The LEC is non-increasing in loss; scan
    the loss-sorted curve for the first segment whose probabilities bracket
    the target."""
    if not lec_points:
        return None
    pts = sorted(lec_points, key=lambda pt: pt["loss"])
    lo = pts[0]  # adapter-iter: ok — curve endpoint (lowest loss), an intentional clamp
    hi = pts[-1]  # adapter-iter: ok — curve endpoint (highest loss), an intentional clamp
    if probability >= lo["probability"]:
        return float(lo["loss"])
    if probability <= hi["probability"]:
        return float(hi["loss"])
    for a, b in itertools.pairwise(pts):
        if a["probability"] >= probability >= b["probability"]:
            if a["probability"] == b["probability"]:
                return float(a["loss"])
            t = (a["probability"] - probability) / (a["probability"] - b["probability"])
            return float(a["loss"] + t * (b["loss"] - a["loss"]))
    return float(hi["loss"])  # defensive — unreachable given the clamps above


def appetite_strip(
    dual_lec: Mapping[str, Any] | None, tolerance: Mapping[str, float] | None
) -> dict[str, Any] | None:
    """Verdict-strip view model (#545 scope A). All verdicts route through
    ``_verdict_from_probability`` — the single appetite decision rule shared
    with the dashboard posture verdict. ``dual_lec`` and ``tolerance`` are the
    SAME reporting-currency-converted values the dashboard posture verdict
    uses (wired at the route layer), so the two pages can never disagree and
    every money output is in one currency space. ``times_over`` and
    ``headroom`` are v3 view-model derivations, not FAIR-grounded.
    ``near_threshold`` reuses ``posture_appetite_detail`` on the with-controls
    curve (the displayed verdict's softening band)."""
    if not dual_lec or tolerance is None:
        return None
    without = dual_lec.get("without_controls") or []
    with_c = dual_lec.get("with_controls") or []
    if not without or not with_c:
        return None
    amount = float(tolerance["amount"])
    prob = float(tolerance["probability"])
    p_without = _interpolate_exceedance_probability(without, amount)
    p_with = _interpolate_exceedance_probability(with_c, amount)
    verdict_without = _verdict_from_probability(p_without, prob)
    verdict_with = _verdict_from_probability(p_with, prob)
    detail = posture_appetite_detail(with_c, tolerance)
    loss_at = interpolate_loss_at_probability(with_c, prob)
    return {
        "p_without": p_without,
        "p_with": p_with,
        "verdict_without": verdict_without,
        "verdict_with": verdict_with,
        "near_threshold": bool(detail and detail.get("near_threshold")),
        "times_over": (p_without / prob) if (verdict_without == "exceeds" and prob > 0) else None,
        "loss_at_tol_prob": loss_at,
        "headroom": (amount - loss_at) if loss_at is not None else None,
        "tol_amount": amount,
        "tol_probability": prob,
    }


def build_budget_card(
    control_spend: float,
    annual_security_budget: float | None,
) -> dict[str, Any]:
    """Control-spend-vs-budget card.

    ``ratio``/``headroom`` are ``None`` when no budget is configured (or the
    budget is exactly ``0``) — the panel falls back to cost+ROI only, no
    gauge.
    """
    spend = float(control_spend)
    budget = float(annual_security_budget) if annual_security_budget is not None else None
    ratio = (spend / budget) if budget else None
    headroom = (budget - spend) if budget is not None else None
    return {"spend": spend, "budget": budget, "ratio": ratio, "headroom": headroom}


def build_scenario_coverage(
    sector_library_ids: Iterable[str],
    pinned_library_ids: Iterable[str],
) -> CoverageResult:
    """Scenario-library coverage for the org's industry sub-sector.

    reference = published library entries applicable to the org's
    ``industry_sub_sector``; covered = distinct ``library_pin.entry_id``
    values across the org's scenarios. Thin wrapper over the shared
    ``coverage()`` primitive (Task 2) — no ratio logic re-derived here.
    """
    return coverage(reference=sector_library_ids, covered=pinned_library_ids)


def _control_tags_for_framework(control: _ControlLike, framework: str) -> list[str]:
    """A control's tag codes for one crosswalk-seeded framework.

    Storage is asymmetric on the ``Control`` ORM for schema-history reasons,
    not a taxonomy choice: ``nist_csf`` has a named column
    (``Control.nist_csf_functions``); ``cis`` is stashed under
    ``compliance_mappings['cis_safeguards']`` (see ``services/controls.py``'s
    D1 tag-mapping comment — there is no named Control column for cis).
    ``SEEDED_FRAMEWORKS`` remains the sole reference enumeration the caller
    iterates (``build_control_coverage`` below); this function only maps an
    already-iterated framework name to where its tags live on the row.
    Unknown/future framework names return ``[]`` rather than raising.
    """
    if framework == "nist_csf":
        return list(control.nist_csf_functions or [])
    if framework == "cis":
        return list(control.compliance_mappings.get("cis_safeguards", []) or [])
    return []


def build_control_coverage(
    controls: Iterable[_ControlLike],
    seeded_frameworks: Iterable[str],
    framework_totals: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """FAIR-CAM domain counts + per-framework crosswalk coverage.

    ``seeded_frameworks`` drives the loop (pass ``SEEDED_FRAMEWORKS`` from
    ``services/control_library_validation.py`` — never a hardcoded list).
    ``framework_totals`` maps each seeded framework name to its full
    reference code list, fetched by the caller via ``CrosswalkService``
    (this function stays pure/DB-free per the reference-fetching split).

    Returns::

        {
          "fair_cam": {ControlDomain.X: count, ...},  # every enum member present, 0 if absent
          "frameworks": [{"name": str, "coverage": CoverageResult}, ...],
        }
    """
    control_list = list(controls)

    fair_cam: dict[ControlDomain, int] = dict.fromkeys(ControlDomain, 0)
    for control in control_list:
        for domain in control.domains:
            fair_cam[domain] = fair_cam.get(domain, 0) + 1

    frameworks: list[dict[str, Any]] = []
    for framework in seeded_frameworks:
        reference = framework_totals.get(framework, [])
        covered: set[str] = set()
        for control in control_list:
            covered.update(_control_tags_for_framework(control, framework))
        frameworks.append(
            {"name": framework, "coverage": coverage(reference=reference, covered=covered)}
        )

    return {"fair_cam": fair_cam, "frameworks": frameworks}
