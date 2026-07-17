"""Dashboard orchestrator (omicron-1).

Q14=alpha layered: orchestrates RunRepo + ScenarioRepo + view-model
helpers. Does NOT issue raw SQL — the existing
ScenarioRepo.fetch_by_ids_for_org is the canonical org-scoped batch-by-id
fetch (services consume repos; never bypass them).

CALLER MUST AUTHORIZE ORG ACCESS BEFORE INVOKING. Phase-1 single-org
via require_sole_org is the only authorized caller. The signature
accepts any Organization for testability; future multi-tenancy work
must add a User-vs-Organization membership check before relying on
this function.

Security invariant: user-supplied labels (run.name, Scenario.name,
latest_aggregate_label) are rendered via Jinja autoescape — NEVER
mark them safe with the | safe filter, which would silently introduce
stored XSS. The display_name_fallback function is purely interpolated
strings and does not bypass autoescape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunType
from idraa.repositories.run_repo import RunRepo
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.services.aggregate_run_view_model import build_aggregate_display_results
from idraa.services.attack_coverage import AttackDomainSummary, build_attack_coverage_summary
from idraa.services.control_library_validation import SEEDED_FRAMEWORKS
from idraa.services.controls import list_controls
from idraa.services.coverage import CoverageResult
from idraa.services.crosswalk import CrosswalkService, MultipleVersionsError
from idraa.services.dashboard_view_model import (
    RecentRunRow,
    TopScenarioRow,
    build_budget_card,
    build_control_coverage,
    build_recent_run_row,
    build_residual_ale_card,
    build_scenario_coverage,
    build_top_scenarios,
    display_name_fallback,
    posture_appetite_detail,
)
from idraa.services.fx_rates import FxRateService
from idraa.services.reporting_currency import resolve_reporting_currency

# Scenario-library corpus is small and curated (dozens of tiered entries,
# not thousands — see Epic C re-curation); a fixed generous limit lets the
# dashboard fetch the FULL sector-applicable reference set in one page
# without adding pagination plumbing to a coverage-ratio computation.
_LIBRARY_REFERENCE_LIMIT = 1000

if TYPE_CHECKING:
    # Used only for ``cast`` below — the ``_RunLike`` Protocol describes the
    # attributes the view-model helpers read. Mapped[X] descriptors on
    # SQLAlchemy ORM classes evaluate to X at runtime, but mypy treats them
    # as Mapped[X] in static analysis, so the structural Protocol match
    # fails statically. The cast is sound because RiskAnalysisRun's mapped
    # columns ARE structurally _RunLike at runtime.
    from idraa.services.dashboard_view_model import _ControlLike, _RunLike


@dataclass(frozen=True)
class DashboardData:
    org: Organization
    latest_aggregate: RiskAnalysisRun | None
    latest_aggregate_label: str | None
    recent_runs: list[RecentRunRow]
    top_scenarios: list[TopScenarioRow]
    total_scenarios_with_runs: int
    dual_lec: dict[str, Any] | None
    dual_epc: dict[str, Any] | None
    control_value: dict[str, Any] | None
    residual_ale: dict[str, Any] | None
    loss_tolerance: dict[str, Any] | None
    # Org-wide scenario count (any status). 0 == brand-new org → the
    # dashboard renders the quick-start card instead of dead-end CTAs
    # ("Run analysis" with nothing to run against).
    scenario_count: int = 0
    # P3: reporting currency metadata for templates.
    # Matches the display_results["currency"] dict shape for consistency.
    currency: dict[str, str] = field(default_factory=lambda: {"code": "USD", "symbol": "$"})
    # Task 5 (#419): weight-robustness display data from the latest aggregate run.
    # Sourced from agg_view["weight_robustness"] (already converted to reporting
    # currency by build_aggregate_display_results). None when no aggregate run exists
    # or the run predates Task 4.
    weight_robustness: dict[str, Any] | None = None
    # Task 3 (#478): posture verdict + budget/coverage aggregates for the
    # redesigned dashboard. None/empty defaults mirror the "no aggregate run
    # yet" / "no controls yet" cold-start states the existing cards already
    # handle.
    posture: dict[str, Any] | None = None
    budget: dict[str, Any] = field(
        default_factory=lambda: {"spend": 0.0, "budget": None, "ratio": None, "headroom": None}
    )
    control_coverage: dict[str, Any] = field(
        default_factory=lambda: {"fair_cam": {}, "frameworks": []}
    )
    scenario_coverage: CoverageResult = field(
        default_factory=lambda: CoverageResult(
            covered_count=0, reference_count=0, ratio=0.0, missing=[], present=[]
        )
    )
    # #475 follow-up: ATT&CK tactic rollup per catalog domain. Empty list
    # when the catalog is unseeded (template hides the block) — reference
    # data comes from the seeded tables, never literals.
    attack_coverage: list[AttackDomainSummary] = field(default_factory=list)


async def build_dashboard(db: AsyncSession, org: Organization) -> DashboardData:
    """Build the dashboard view-model for ``org``.

    CALLER MUST AUTHORIZE ORG ACCESS — see module docstring.
    """
    run_repo = RunRepo(db)
    scenario_repo = ScenarioRepo(db)

    latest_aggregate = await run_repo.latest_aggregate_for_org(org.id)
    recent_runs_orm = await run_repo.list_recent_for_org(org.id, limit=10)

    fallback_singles: dict[uuid.UUID, RiskAnalysisRun] = {}
    if latest_aggregate is None:
        fallback_singles = await run_repo.latest_single_per_scenario_for_org(org.id)

    # Single batch lookup for scenario names needed on the fallback path
    # AND for SINGLE rows in the recent-runs feed.
    needed_scenario_ids: set[uuid.UUID] = set(fallback_singles.keys())
    for r in recent_runs_orm:
        if r.run_type == RunType.SINGLE and r.scenario_id is not None:
            needed_scenario_ids.add(r.scenario_id)

    scenario_names_by_id: dict[uuid.UUID, str] = {}
    if needed_scenario_ids:
        # Use the existing canonical org-scoped batch fetcher (Q14=alpha:
        # services consume repos). fetch_by_ids_for_org returns full Scenario
        # rows; project to the {id: name} mapping the view-model needs.
        scenarios = await scenario_repo.fetch_by_ids_for_org(
            org.id,
            list(needed_scenario_ids),
        )
        scenario_names_by_id = {s.id: s.name for s in scenarios}

    top_scenarios, total_with_runs = build_top_scenarios(
        cast("_RunLike | None", latest_aggregate),
        cast("dict[uuid.UUID, _RunLike]", fallback_singles),
        scenario_names_by_id,
        top_n=5,
    )
    recent_runs = [
        build_recent_run_row(
            cast("_RunLike", r),
            scenario_names_by_id.get(r.scenario_id) if r.scenario_id else None,
        )
        for r in recent_runs_orm
    ]

    from babel.numbers import get_currency_symbol as _get_sym

    from idraa.currency import APP_LOCALE

    if latest_aggregate is not None:
        # P3: resolve reporting currency for the aggregate run's view-model.
        _rc_code = getattr(org, "preferred_currency", "USD") or "USD"
        _rc_snap = getattr(latest_aggregate, "presentation_fx_snapshot", None)
        _active_rate = None
        if _rc_code != "USD" and not _rc_snap:
            _active_rate = await FxRateService(db).active_rate(org.id, _rc_code)
        rc = resolve_reporting_currency(latest_aggregate, org, _active_rate)

        agg_view = build_aggregate_display_results(latest_aggregate, rc=rc) or {}
        dual_lec = agg_view.get("dual_lec")
        dual_epc = agg_view.get("dual_epc")
        control_value = agg_view.get("control_value_headline")
        confidence_intervals = agg_view.get("confidence_intervals")
        dashboard_weight_robustness = agg_view.get("weight_robustness")
        latest_aggregate_label = (
            latest_aggregate.name
            if latest_aggregate.name
            else display_name_fallback(cast("_RunLike", latest_aggregate))
        )
        # ROI is a dimensionless ratio ($ reduction / $ cost) — currency-invariant,
        # so it's read straight off the raw summary (no rc.convert needed).
        # cost_summary is absent on legacy pre-cost-model runs; .get() degrades
        # to None ('—' in the UI) rather than fabricating a 0.0 ROI.
        aggregate_roi = (
            (latest_aggregate.simulation_results or {}).get("cost_summary", {}).get("aggregate_roi")
        )
    else:
        from idraa.services.run_view_model import _USD_IDENTITY

        rc = _USD_IDENTITY
        dual_lec = None
        dual_epc = None
        control_value = None
        confidence_intervals = None
        latest_aggregate_label = None
        dashboard_weight_robustness = None
        aggregate_roi = None

    # P3: currency metadata for the dashboard template (matches display_results["currency"] shape).
    currency_meta: dict[str, str] = {
        "code": rc.code,
        "symbol": _get_sym(rc.code, locale=APP_LOCALE),
    }

    residual_ale_raw = build_residual_ale_card(cast("_RunLike | None", latest_aggregate), org)
    # P3: convert the ALE value to reporting currency.
    # pct_revenue is a ratio (ALE_USD / revenue_USD) — do NOT convert.
    if residual_ale_raw is not None:
        residual_ale: dict[str, Any] | None = {
            **residual_ale_raw,
            "value": rc.convert(residual_ale_raw["value"]) or 0.0,
        }
    else:
        residual_ale = None

    # P3: convert top_scenarios ALE values to reporting currency.
    top_scenarios = [
        TopScenarioRow(
            scenario_id=ts.scenario_id,
            scenario_name=ts.scenario_name,
            residual_ale=rc.convert(ts.residual_ale) or 0.0,
            base_ale=rc.convert(ts.base_ale) if ts.base_ale is not None else None,
            source_run_id=ts.source_run_id,
            source=ts.source,
        )
        for ts in top_scenarios
    ]

    # P3: convert recent_runs headline_ale values to reporting currency.
    recent_runs = [
        RecentRunRow(
            id=row.id,
            display_name=row.display_name,
            run_type=row.run_type,
            status=row.status,
            created_at=row.created_at,
            headline_ale=rc.convert(row.headline_ale) if row.headline_ale is not None else None,
        )
        for row in recent_runs
    ]

    loss_tolerance: dict[str, Any] | None = None
    if org.loss_tolerance_amount is not None and org.loss_tolerance_probability is not None:
        # P3: convert loss_tolerance amount to reporting currency (probability unchanged).
        loss_tolerance = {
            "amount": rc.convert(float(org.loss_tolerance_amount)),
            "probability": float(org.loss_tolerance_probability),
        }

    scenario_count = await scenario_repo.count_for_org(organization_id=org.id)

    # --- Task 3 (#478): posture verdict + budget/coverage aggregates -----

    posture: dict[str, Any] | None = None
    if latest_aggregate is not None:
        lec_with_controls = (dual_lec or {}).get("with_controls") if dual_lec else None
        _appetite = posture_appetite_detail(lec_with_controls, loss_tolerance)
        posture = {
            "verdict": _appetite["verdict"] if _appetite is not None else None,
            "near_threshold": _appetite["near_threshold"] if _appetite is not None else False,
            "residual_ale": residual_ale["value"] if residual_ale is not None else 0.0,
            "range_lo": (confidence_intervals or {}).get("lower_bound"),
            "range_hi": (confidence_intervals or {}).get("upper_bound"),
            "pct_revenue": residual_ale["pct_revenue"] if residual_ale is not None else None,
            "tol_amount": loss_tolerance["amount"] if loss_tolerance is not None else None,
            "tol_prob": loss_tolerance["probability"] if loss_tolerance is not None else None,
            "control_value": control_value,
            "control_reduction_pct": (control_value or {}).get("percent"),
            "roi": aggregate_roi,
        }

    # Controls list backs BOTH the budget card (Σ annual_cost) and the
    # control-coverage aggregate (FAIR-CAM domains + framework tags) — one
    # fetch, reused, matching how the Controls page enumerates the org's
    # controls (services.controls.list_controls; excludes only deleted rows).
    controls = await list_controls(db, org_id=org.id)

    control_spend = sum(float(c.annual_cost) for c in controls)
    annual_budget = (
        float(org.annual_security_budget) if org.annual_security_budget is not None else None
    )
    budget = build_budget_card(control_spend, annual_budget)

    crosswalk = CrosswalkService(db)
    framework_totals: dict[str, list[str]] = {}
    for framework in SEEDED_FRAMEWORKS:
        try:
            framework_totals[framework] = await crosswalk.codes_for(framework)
        except MultipleVersionsError:
            # Gate M4 (crosswalk.py): a framework with >1 seeded version and no
            # explicit version is ambiguous — skip it from the coverage panel
            # rather than silently unioning versions or failing the whole
            # dashboard over one framework's seed data.
            framework_totals[framework] = []
    # cast: like the _RunLike cast above, Control's Mapped[X] descriptors are X
    # at runtime but Mapped[X] to mypy, so the structural _ControlLike match
    # fails statically. Sound — Control IS structurally _ControlLike at runtime.
    control_coverage = build_control_coverage(
        cast("list[_ControlLike]", controls), SEEDED_FRAMEWORKS, framework_totals
    )

    library_repo = ScenarioLibraryRepo(db)
    if org.industry_sub_sector is not None:
        sector_entries = await library_repo.list_published(
            applicable_sub_sectors=[org.industry_sub_sector],
            limit=_LIBRARY_REFERENCE_LIMIT,
        )
    else:
        # No sub-sector configured: list_published's own semantics for an
        # unset filter is "no narrowing" (all published entries) rather than
        # an empty reference set.
        sector_entries = await library_repo.list_published(limit=_LIBRARY_REFERENCE_LIMIT)
    sector_library_ids = [str(e.id) for e in sector_entries]

    pinned_library_ids = await scenario_repo.list_pinned_library_entry_ids_for_org(org.id)
    scenario_coverage = build_scenario_coverage(sector_library_ids, pinned_library_ids)

    attack_coverage = await build_attack_coverage_summary(db, organization_id=org.id)

    return DashboardData(
        org=org,
        latest_aggregate=latest_aggregate,
        latest_aggregate_label=latest_aggregate_label,
        recent_runs=recent_runs,
        top_scenarios=top_scenarios,
        total_scenarios_with_runs=total_with_runs,
        scenario_count=scenario_count,
        dual_lec=dual_lec,
        dual_epc=dual_epc,
        control_value=control_value,
        residual_ale=residual_ale,
        loss_tolerance=loss_tolerance,
        currency=currency_meta,
        weight_robustness=dashboard_weight_robustness,
        posture=posture,
        budget=budget,
        control_coverage=control_coverage,
        scenario_coverage=scenario_coverage,
        attack_coverage=attack_coverage,
    )
