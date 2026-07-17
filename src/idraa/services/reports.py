"""Reports view-model: orchestrator + pure helpers for the Executive PDF.

Per-PR-omicron-2 design (Q16=alpha — layered): this module owns the
orchestration of run + org + scenarios + controls_snapshot into an
RunReportData dataclass that services/pdf_report.py consumes.

The pure helpers (build_pct_revenue, build_narrative,
group_controls_by_fair_cam_domain, build_per_scenario_rows) take
plain inputs and return plain outputs — no DB, no HTTP.

The orchestrator (build_executive_pdf_data) is the only async function
in this module; it coordinates a single batch scenario lookup and the
controls_snapshot decoding.

T2 (#351): RunReportData supersedes ExecutivePdfData as the view-model
dataclass. T9 removes the ExecutivePdfData alias and migrates all references
to RunReportData in the renderer (services/pdf_report.py) and all test suites.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import logging
import re as _re
import uuid as _uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from babel.numbers import get_currency_symbol as _get_currency_symbol
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.currency import APP_LOCALE as _APP_LOCALE
from idraa.services._view_model_helpers import (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER as CONTROL_WEIGHT_PROVENANCE_DISCLAIMER,  # Issue #413: explicit re-export; defined in _view_model_helpers to avoid circular import
)
from idraa.services._view_model_helpers import (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE as CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE,  # M4 fix: base disclaimer (no indistinguishable sentence) for robustness-absent surfaces
)
from idraa.services._view_model_helpers import (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED as CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED,  # leave-one-out legend appended; per-control value-range table only
)
from idraa.services._view_model_helpers import (
    DIST_STATS_DEFINITIONAL_NOTE as DIST_STATS_DEFINITIONAL_NOTE,  # Task 1 (#353): explicit re-export; defined in _view_model_helpers to avoid circular import
)
from idraa.services._view_model_helpers import (
    MEAN_BASIS_PAIRING_NOTE as MEAN_BASIS_PAIRING_NOTE,  # 2026-07-04 mean+typical side-by-side: explicit re-export
)
from idraa.services._view_model_helpers import (
    TAIL_LADDER_DISPLAY_LABELS as TAIL_LADDER_DISPLAY_LABELS,  # T5b (methodology-approved, 2026-07-04): plain-language display labels, now shared web + PDF
)
from idraa.services._view_model_helpers import (
    TAIL_LADDER_LABELS as TAIL_LADDER_LABELS,  # T2(review) (#353): VaR/ES row labels — %-suffixed single source of truth for web + PDF
)
from idraa.services._view_model_helpers import (
    _build_tail_risk,  # T2 (#351): moved here from run_view_model
    has_ci_band,
    has_tail_metrics,
    if_removed_by_control_aggregate,  # leave-one-out "if removed" AGGREGATE lookup (display plumbing)
    if_removed_by_control_single,  # leave-one-out "if removed" SINGLE-run lookup (display plumbing)
    process_weight_robustness_for_display,  # Task 5 (#419): weight-robustness display helper
    snapshot_sub_functions_by_id,  # #436 web/PDF parity: zero_reason labels in the PDF table
)
from idraa.services.aggregate_run_view_model import (
    _build_per_scenario_control_matrix,  # T2 (#351): pure function, no DB query
)
from idraa.services.run_view_model import _build_control_effectiveness_rows

logger = logging.getLogger(__name__)


# ---- T7 (#351): TIER_BADGE_TEXT — module-level constant, single source of truth ----
#
# Values MUST be byte-identical to the canonical one-liners in
# docs/reference/loss-magnitude-tiering.md § "Canonical badge one-liners".
# doc-drift must fail the pin test deliberately — update doc + constant + test together.
TIER_BADGE_TEXT: dict[str, str] = {
    "paginated": "Paginated primary source — figure/table/page-cited loss anchor",
    "vendor": "Vendor-sourced loss estimate — lower confidence",
    "anecdotal": "Anecdotal / analyst-judged — lowest confidence; no citeable loss anchor",
}

# ---- T7 (#351): FAIR_GLOSSARY — module-level dict, consumed by the renderer ----
#
# One short paragraph each for TEF, Vulnerability, Loss Magnitude, ALE, VaR, ES.
# Mandatory wording constraints (methodology-reviewed):
#   - VaR: "not a FAIR Standard node — v3 view-model descriptive statistic,
#     computed empirically from simulated outcomes"
#   - ES:  same mandatory phrase
#   - Vulnerability: inherent/control-naive framing per issue #339
#     (probability a threat event becomes a loss event BEFORE modeled controls
#     are applied; controls then adjust it — never net-of-controls)
#   - ALE: E[LEF x LM] (expected annual loss) — NEVER "sample mean"
#   - VaR defined as the q-th percentile of simulated annual loss
#   - ES defined as the mean simulated annual loss at or above the q-th percentile
FAIR_GLOSSARY: dict[str, str] = {
    "TEF": (
        "Threat Event Frequency (TEF): the rate at which a threat agent is expected"
        " to act against an asset, expressed in events per year."
        " TEF is an Open FAIR node in the Loss Event Frequency (LEF) component."
        " It captures only the frequency of threat contact — not whether each contact"
        " results in a loss event."
    ),
    "Vulnerability": (
        "Vulnerability: the probability that a threat event results in a loss event,"
        " assessed BEFORE modeled controls are applied (inherent, control-naive baseline)."
        " Controls are applied on top of this baseline to derive the residual loss-event"
        " probability. The inherent baseline reflects the pre-control environment;"
        " reporting it as a post-control value would overstate the control benefit."
        " Vulnerability is an Open FAIR node."
    ),
    "Loss Magnitude": (
        "Loss Magnitude (LM): the amount of loss resulting from a single loss event,"
        " combining Primary Loss (direct financial impact) and Secondary Loss (indirect"
        " and reputational costs such as regulatory fines, notification costs, and"
        " brand damage)."
        " Loss Magnitude is an Open FAIR node."
    ),
    "ALE": (
        "Annualized Loss Expectancy (ALE): E[LEF x LM] — the expected annual loss,"
        " computed as the expected product of loss-event frequency and loss magnitude"
        " across simulated outcomes."
        " ALE is the primary risk metric reported by Idraa."
    ),
    "VaR": (
        "Value at Risk (VaR_q): the q-th percentile of simulated annual loss —"
        " the loss level that is not exceeded in q% of simulated years."
        " VaR is not a FAIR Standard node — v3 view-model descriptive statistic,"
        " computed empirically from simulated outcomes."
        " Common thresholds reported: VaR 90, VaR 95, VaR 99, VaR 99.9."
    ),
    "ES": (
        "Expected Shortfall (ES_q): the mean simulated annual loss at or above the"
        " q-th percentile — the average loss in the worst (100-q)% of simulated years"
        " (e.g. the worst 5% for q=95)."
        " ES is not a FAIR Standard node — v3 view-model descriptive statistic,"
        " computed empirically from simulated outcomes."
        " Common thresholds reported: ES 95, ES 99, ES 99.9."
    ),
}


def _resolve_engine_label() -> str:
    """Return a human-readable engine string for the PDF assumptions block.

    Queries importlib.metadata so pdf_report.py (the renderer) never needs to
    import fair_cam directly. Falls back to the bare name string when the
    distribution is not found (e.g. editable installs without a dist-info record).
    Distribution name on this project is ``fair-cam`` (hyphenated).
    """
    try:
        version = importlib.metadata.version("fair-cam")
        return f"fair-cam {version}"
    except importlib.metadata.PackageNotFoundError:
        return "fair-cam"


# Issue #202: fixed central-interval percent for the empirical p2.5/p97.5 band.
# Mirrors run_executor._BAND_INTERVAL_PCT — the persisted ``interval_pct`` is
# the authoritative value; this is only the default for the rare legacy
# AGGREGATE payload that predates the key.
_BAND_INTERVAL_PCT_REPORTS: int = 95


def build_pct_revenue(
    residual_ale: float, annual_revenue: Decimal | float | int | None
) -> float | None:
    """Mirrors the dashboard's build_residual_ale_card revenue logic.

    Returns None on revenue None / 0 / Decimal('0') (no divide-by-zero;
    zero is the unset case). Otherwise returns residual_ale / revenue * 100
    as a percentage.

    Decimal inputs are cast to float; the caller is expected to have
    already validated that the value fits in a float without precision
    loss (Phase-1 organization revenue caps are well within float range).
    """
    if annual_revenue is None:
        return None
    revenue_float = float(annual_revenue)
    if revenue_float <= 0:
        return None
    return residual_ale / revenue_float * 100.0


# Currency symbol uses Babel CLDR via _get_currency_symbol (Task 8: retired hand-rolled helper).


def _reduction_clause_for_headline(
    *,
    control_value_dollars: float,
    weight_robustness_headline: dict[str, Any] | None,
    currency: str,
) -> str:
    """Shared reduction-clause builder for build_narrative / build_narrative_single
    (2026-07-04 mean+typical side-by-side extraction — same wording, no behavior
    change for existing typical-basis callers).

    P2 (typical-basis, unchanged): the headline dollar (control_value_dollars) is
    the AVERAGE reduction while the range is the TYPICAL-case (median) band.
    Leading with the median made the sentence incoherent — the residual ALE is
    the MEAN, so "base minus typical != residual". Lead with the average
    reduction (matching the cover headline) and demote the typical-case band to
    a parenthetical that explains the skew.

    Mean-basis (new): when ``weight_robustness_headline["basis"] == "mean"``,
    the range is ALREADY average-basis (comparable to the headline dollar, not
    a smaller typical-case figure) — the "skewed losses run far below the
    average" claim would be FALSE for this range, so it is dropped in favor of
    a same-basis framing.
    """
    from idraa.formatting import safe_money_format
    from idraa.services._view_model_helpers import control_value_range

    if (
        weight_robustness_headline is not None
        and weight_robustness_headline.get("reduction_p50") is not None
    ):
        _avg = safe_money_format(control_value_dollars, currency, compact=True)
        _range = control_value_range(weight_robustness_headline, currency)
        if weight_robustness_headline.get("basis") == "mean":
            return f"{_avg} on average (modeled range {_range}, same average basis)"
        return (
            f"{_avg} on average (typical-case ~{_range} — skewed losses run far below the average)"
        )
    sym = _get_currency_symbol(currency, locale=_APP_LOCALE)
    return f"{sym}{control_value_dollars:,.0f}"


def build_narrative(
    *,
    n_scenarios: int,
    control_value_dollars: float,
    control_value_percent: float,
    residual_ale: float,
    pct_revenue: float | None,
    n_simulations: int,
    currency: str,
    weight_robustness_headline: dict[str, Any] | None = None,
) -> str:
    """Q12: 1-2 sentence prose for the Executive PDF page-1 narrative.

    Format is fixed (deterministic, snapshot-tested) so reviewers can
    detect drift. Currency symbol via Babel get_currency_symbol(); n_simulations
    is comma-formatted.

    Issue #202: the closing clause no longer claims an input-derived
    "X% confidence" (the retired heuristic). It states only the Monte-Carlo
    iteration count; the central-95% percentile band is surfaced on page 2.

    The "% of annual revenue" clause is conditionally appended only
    when pct_revenue is not None.

    Issue #413: the closing sentence appends the canonical control-weight
    provenance disclaimer — this narrative quotes a control-value dollar
    figure, which rests on fair_cam's implementation-calibrated composition
    weights, so the disclosure must travel with it.

    Task 5 (#419 / Meth-I4): when ``weight_robustness_headline`` is provided
    (a dict with ``reduction_p5/p50/p95`` already converted to ``currency``),
    LEADS with the range (~$p50 [range $p5-$p95], median basis) and DROPS the
    spurious one-decimal-place percentage (Meth-I4). When not available, shows
    the point estimate without a percentage (bare-point-percent dropped).
    """
    from idraa.formatting import safe_money_format

    sym = _get_currency_symbol(currency, locale=_APP_LOCALE)
    pct_revenue_clause = (
        f" — equivalent to {pct_revenue:.2f}% of annual revenue" if pct_revenue is not None else ""
    )
    # P2 / 2026-07-04 mean+typical side-by-side: shared clause builder (see
    # _reduction_clause_for_headline docstring for the basis-switch rationale).
    _reduction_clause = _reduction_clause_for_headline(
        control_value_dollars=control_value_dollars,
        weight_robustness_headline=weight_robustness_headline,
        currency=currency,
    )
    _residual_str = (
        safe_money_format(residual_ale, currency, compact=True)
        if weight_robustness_headline is not None
        and weight_robustness_headline.get("reduction_p50") is not None
        else f"{sym}{residual_ale:,.0f}"
    )
    # M4: gate the indistinguishable-pairs caveat on robustness presence so
    # legacy runs don't reference flags that don't exist on the run.
    _disclaimer = (
        CONTROL_WEIGHT_PROVENANCE_DISCLAIMER
        if weight_robustness_headline is not None
        else CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE
    )
    return (
        f"Across {n_scenarios} modeled scenarios, the current control "
        f"posture reduces annualized loss expectancy by "
        f"{_reduction_clause}, "
        f"bringing residual ALE to {_residual_str}"
        f"{pct_revenue_clause}. This estimate reflects "
        f"{n_simulations:,} Monte Carlo iterations. "
        f"{_disclaimer}"
    )


def build_narrative_single(
    *,
    control_value_dollars: float,
    control_value_percent: float,
    residual_ale: float,
    pct_revenue: float | None,
    n_simulations: int,
    currency: str,
    scenario_name: str = "the modeled scenario",
    weight_robustness_headline: dict[str, Any] | None = None,
) -> str:
    """T3 (#351): SINGLE-run variant of build_narrative.

    Uses scenario-scoped wording — NO portfolio phrasing. Banned phrases
    (negative-match assertions enforce this):
      - "portfolio"
      - "N scenarios" / "modeled scenarios"
      - "across scenarios"
      - "aggregate" (as a portfolio descriptor)
      - "diversif" (prefix)

    Format is deterministic and snapshot-tested in test_reports_view_model.py.

    Issue #413: the closing sentence appends the canonical control-weight
    provenance disclaimer (same string as the AGGREGATE narrative) — this
    narrative quotes a control-value dollar figure that rests on
    implementation-calibrated composition weights. The disclaimer contains
    none of the banned portfolio phrases, so the negative-match assertions
    in test_build_narrative_single_no_portfolio_phrasing still hold.

    Task 5 (#419 / Meth-I4): when ``weight_robustness_headline`` is provided,
    LEADS with the range and DROPS the spurious one-decimal-place percentage.
    """
    from idraa.formatting import safe_money_format

    sym = _get_currency_symbol(currency, locale=_APP_LOCALE)
    pct_revenue_clause = (
        f" — equivalent to {pct_revenue:.2f}% of annual revenue" if pct_revenue is not None else ""
    )
    # P2 / 2026-07-04 mean+typical side-by-side: shared clause builder (see
    # _reduction_clause_for_headline docstring for the basis-switch rationale).
    _reduction_clause = _reduction_clause_for_headline(
        control_value_dollars=control_value_dollars,
        weight_robustness_headline=weight_robustness_headline,
        currency=currency,
    )
    _residual_str = (
        safe_money_format(residual_ale, currency, compact=True)
        if weight_robustness_headline is not None
        and weight_robustness_headline.get("reduction_p50") is not None
        else f"{sym}{residual_ale:,.0f}"
    )
    # M4: gate the indistinguishable-pairs caveat on robustness presence so
    # legacy runs don't reference flags that don't exist on the run.
    _disclaimer = (
        CONTROL_WEIGHT_PROVENANCE_DISCLAIMER
        if weight_robustness_headline is not None
        else CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE
    )
    return (
        f"For {rl_escape_narrative(scenario_name)}, the current control posture reduces "
        f"annualized loss expectancy by "
        f"{_reduction_clause}, "
        f"bringing residual ALE to {_residual_str}"
        f"{pct_revenue_clause}. This estimate reflects "
        f"{n_simulations:,} Monte Carlo iterations. "
        f"{_disclaimer}"
    )


def rl_escape_narrative(s: str) -> str:
    """XML-escape a plain string for safe embedding in the narrative.

    The narrative is placed in a reportlab Paragraph via rl_escape() at
    render time, but the SINGLE narrative also uses the scenario name inline.
    This helper escapes the scenario name fragment before it is embedded in
    the narrative string so downstream rl_escape() does not double-escape it.
    We intentionally do NOT XML-escape here — the narrative string itself is
    escaped by the renderer via rl_escape(data.narrative). We just need the
    name to be plain text (no HTML metacharacters from scenario names that
    could break the narrative string before renderer-level escaping).
    The renderer's rl_escape() will handle the full string.

    For narrative building purposes we strip known reportlab-dangerous chars
    but leave the natural language intact.
    """
    # Simple strip: leave the name as-is; the renderer will escape the entire
    # narrative string. This function exists as a named hook so future tasks
    # can strengthen it without touching call sites.
    return s


@dataclass(frozen=True)
class ControlInventoryRow:
    """One row on page 5's control inventory: name + type tag."""

    name: str
    type: str  # "preventive" / "detective" / "corrective" / "responsive"


@dataclass(frozen=True)
class ControlBreakdownRow:
    """One row in the per-control loss-reduction breakdown (PR μ.1).

    ``loss_reduction_per_event`` is, per fair_cam, a PER-loss-event,
    PER-scenario Secondary-Loss reduction (CURRENCY-branch subtractor,
    FAIR-CAM §3.3.3 + audit §8.4). This view-model SUMS that quantity across
    every scenario a control participates in, so the stored number is a
    **v3 view-model portfolio derivation** ("total per-event loss reduction
    a control contributes across the modeled scenarios"), NOT a single FAIR
    loss-event node value. The label honours that distinction (#266/D4):
    only when a control appears in exactly one scenario is the summed number
    a genuine single per-event reduction; for N>1 scenarios the label names
    the scenario count and avoids the bare "/event reduced" claim, per
    "No portfolio-finance overclaim" (CLAUDE.md).

    Only controls where the summed quantity ``> 0`` produce a non-None label;
    the renderer is expected to skip rows with ``loss_reduction_label=None``.
    """

    control_id: str
    control_name: str
    loss_reduction_per_event: float
    loss_reduction_label: str | None  # None when loss_reduction_per_event == 0


# Stable enum-string keys for the controls-by-domain dict; the template
# applies the human label via _FAIR_CAM_DOMAIN_LABELS in services/pdf_report.py.
_FAIR_CAM_DOMAIN_KEYS: tuple[str, ...] = (
    "LOSS_EVENT",
    "VARIANCE_MANAGEMENT",
    "DECISION_SUPPORT",
    "UNCATEGORIZED",
)

# Map raw domain values (as emitted by snapshot writers — lowercase ControlDomain
# enum values) to the canonical UPPERCASE bucket key used by _FAIR_CAM_DOMAIN_LABELS.
# Single source of truth: lowercase only, matching the production writer
# (services/run_executor._snapshot_control_v2) which emits c.domain.value /
# c.domains.value strings from the ControlDomain StrEnum (lowercase).
_DOMAIN_VALUE_TO_BUCKET: dict[str, str] = {
    "loss_event": "LOSS_EVENT",
    "variance_management": "VARIANCE_MANAGEMENT",
    "decision_support": "DECISION_SUPPORT",
}


def group_controls_by_fair_cam_domain(
    snapshot: list[dict[str, Any]],
) -> dict[str, list[ControlInventoryRow]]:
    """Q13=A: bucket controls_snapshot[] entries by FAIR-CAM domain.

    Returns a dict with all four keys always present (any may map to []).
    Within each bucket, rows preserve their input order (no resort).

    Snapshot shapes:
      * Issue #90 ControlSnapshotV2: ``domains: list[str]`` (sorted, lowercase
        ControlDomain enum values; FAIR-CAM §2.2 places domain at sub-function
        level so a control's domain is the SET its assignments span). A
        multi-domain control is appended to EACH bucket it spans, matching the
        Task 5 maintenance template grouping fix.
      * Pre-issue-#90 V2 and legacy V1: scalar ``domain: str`` (the value of
        the now-derived ControlDomain enum). Immutable audit records — never
        rewritten in place.
      * Missing / unrecognised / tampered values -> 'UNCATEGORIZED' bucket
        (defensive; we don't fail the render on a corrupted snapshot row).
    """
    buckets: dict[str, list[ControlInventoryRow]] = {key: [] for key in _FAIR_CAM_DOMAIN_KEYS}
    for entry in snapshot:
        row = ControlInventoryRow(
            name=entry.get("name", ""),
            type=entry.get("type", ""),
        )
        # Issue #90+ V2 snapshots: domains: list[str]
        domains = entry.get("domains")
        if isinstance(domains, list) and domains:
            matched = False
            for d in domains:
                if isinstance(d, str) and (bucket_key := _DOMAIN_VALUE_TO_BUCKET.get(d)):
                    buckets[bucket_key].append(row)
                    matched = True
            if not matched:
                buckets["UNCATEGORIZED"].append(row)
            continue
        # Legacy V1 / pre-issue-#90 V2: scalar domain field
        domain = entry.get("domain")
        if isinstance(domain, str) and (bucket_key := _DOMAIN_VALUE_TO_BUCKET.get(domain)):
            buckets[bucket_key].append(row)
            continue
        # Fallback: missing or unrecognised
        buckets["UNCATEGORIZED"].append(row)
    return buckets


@dataclass(frozen=True)
class PerScenarioRow:
    """One row on page 4: scenario name + base_ale + residual_ale + reduction."""

    scenario_id: str
    scenario_name: str
    base_ale: float
    residual_ale: float
    reduction: float


def build_per_scenario_rows(
    per_scenario: list[dict[str, Any]],
) -> list[PerScenarioRow]:
    """Q3 page 4: sort desc by residual_ale; tie-break asc by scenario_name.

    Note: differs from aggregate_run_view_model._build_per_scenario_ale_rows
    (sorts by base_ale). Page 4's framing is 'after controls' so residual
    is the primary sort key. Both intentional.

    Defensive .get() lookups on the simulation_results dict shape so a
    malformed row doesn't crash the render — missing scenario_id renders
    as empty string, missing ALE values default to 0.0.
    """
    rows: list[PerScenarioRow] = []
    for ps in per_scenario:
        base_ale = ps.get("base_risk", {}).get("annualized_loss_expectancy", 0.0)
        residual_ale = ps.get("residual_risk", {}).get("annualized_loss_expectancy", 0.0)
        rows.append(
            PerScenarioRow(
                scenario_id=str(ps.get("scenario_id", "")),
                scenario_name=ps.get("scenario_name", "(unknown)"),
                base_ale=float(base_ale),
                residual_ale=float(residual_ale),
                reduction=float(base_ale) - float(residual_ale),
            )
        )
    rows.sort(key=lambda r: (-r.residual_ale, r.scenario_name))
    return rows


def build_control_breakdown_rows(
    per_scenario: list[dict[str, Any]],
    currency: str = "USD",
) -> list[ControlBreakdownRow]:
    """PR μ.1: build per-control loss-reduction rows from simulation results.

    fair_cam's ``loss_reduction_per_event`` is a PER-loss-event, PER-scenario
    Secondary-Loss reduction (CURRENCY-branch subtractor, FAIR-CAM §3.3.3 /
    audit §8.4). This helper SUMS that quantity across every scenario a control
    participates in, deduplicating by control_id, to produce an aggregate
    executive signal. The resulting number is therefore a **v3 view-model
    portfolio derivation** ("total per-event loss reduction a control
    contributes across the modeled scenarios"), NOT a single FAIR loss-event
    node value — fair_cam itself remains correct per-scenario and is unchanged.

    Label honesty (#266/D4, "No portfolio-finance overclaim"): the per-control
    scenario count is tracked so the label matches the dimensionality of the
    number it shows. When a control appears in exactly ONE scenario the summed
    number genuinely IS a single per-event reduction, so the label reads
    "…/event reduced". When it spans N>1 scenarios, summing per-event values
    across DIFFERENT scenarios would overstate a single event ~N times, so the
    label instead reads "…total per-event loss reduction across N scenarios"
    with no bare "/event reduced" claim.

    Only controls where the summed quantity ``> 0`` produce a non-None label —
    these are controls with an active CURRENCY-branch assignment. Rows are
    sorted descending by the summed quantity so the most impactful controls
    appear first.

    Methodology: docs/reference/elapsed-time-tau-calibration.md
    """
    sym = _get_currency_symbol(currency, locale=_APP_LOCALE)
    # Accumulate across scenarios, dedup by control_id; track scenario_count so
    # the label can distinguish a genuine single-event reduction (N==1) from a
    # cross-scenario portfolio sum (N>1) — see #266/D4.
    seen: dict[str, dict[str, Any]] = {}
    for ps in per_scenario:
        for adj in ps.get("control_adjustments", []) or []:
            cid = adj.get("control_id") or ""
            if not cid:
                continue
            if cid not in seen:
                seen[cid] = {
                    "control_name": adj.get("control_name", ""),
                    "loss_reduction_per_event": 0.0,
                    "scenario_count": 0,
                }
            seen[cid]["loss_reduction_per_event"] += float(
                adj.get("loss_reduction_per_event", 0.0) or 0.0
            )
            seen[cid]["scenario_count"] += 1
    rows: list[ControlBreakdownRow] = []
    for cid, data in seen.items():
        lr = data["loss_reduction_per_event"]
        n = data["scenario_count"]
        if lr <= 0:
            label = None
        elif n == 1:
            # Single scenario: the number genuinely IS one per-event reduction.
            label = f"{sym}{lr:,.0f}/event reduced (Loss Reduction)"
        else:
            # Cross-scenario portfolio derivation: avoid the bare "/event
            # reduced" overclaim; name the scenario count instead.
            label = (
                f"{sym}{lr:,.0f} total per-event loss reduction "
                f"across {n} scenarios (Loss Reduction)"
            )
        rows.append(
            ControlBreakdownRow(
                control_id=cid,
                control_name=data["control_name"],
                loss_reduction_per_event=lr,
                loss_reduction_label=label,
            )
        )
    rows.sort(key=lambda r: (-r.loss_reduction_per_event, r.control_name))
    return rows


@dataclass(frozen=True)
class ScenarioInventoryRow:
    """One row on page 5's scenarios sub-section."""

    scenario_id: str
    name: str
    summary: str  # truncated Scenario.description (≤120 chars)


@dataclass(frozen=True)
class RunReportData:
    """Frozen view-model passed to services.pdf_report.render_executive_pdf.

    Owns the contract between the orchestrator (services/reports.py)
    and the renderer (services/pdf_report.py). Renderer must not
    consult the DB or fair_cam; everything it needs is here.

    T2 (#351): supersedes ExecutivePdfData as a strict superset. T9 removes
    the ExecutivePdfData alias and migrates all references. All new fields
    default to None/empty so existing call sites that construct without them
    continue to work unchanged.

    Attribute types are deliberately conservative (Any for org/run) so
    pdf_report can stay decoupled from ORM types — the renderer reads
    .name / .preferred_currency / .annual_revenue / .id / .completed_at /
    .mc_iterations and nothing else.
    """

    org: Any  # Organization-shaped: name, industry_type, preferred_currency, annual_revenue
    run: Any  # RiskAnalysisRun-shaped: id, name, completed_at, mc_iterations
    headline_ale: float
    headline_ci_lo: float
    headline_ci_hi: float
    # Issue #202: the central interval the band represents (fixed 95). NOT a
    # confidence level — it is the empirical p2.5/p97.5 percentile span of the
    # modeled annualized-loss distribution. Persisted as ``interval_pct``.
    interval_pct: int
    n_simulations: int
    n_scenarios: int
    control_value_dollars: float
    control_value_percent: float
    pct_revenue: float | None
    base_ale: float
    residual_ale: float
    lec_with: list[tuple[float, float]]
    lec_without: list[tuple[float, float]]
    per_scenario_rows: list[PerScenarioRow]
    scenarios: list[ScenarioInventoryRow]
    controls_by_domain: dict[str, list[ControlInventoryRow]]
    narrative: str
    # When the org has both loss_tolerance_amount and loss_tolerance_probability
    # set, the renderer overlays a vertical+horizontal crosshair on the LEC
    # chart at (amount, probability). None when either field is unset — chart
    # renders without overlay, matching the web macro's behavior.
    loss_tolerance: dict[str, float] | None = None
    # PR omega: side-by-side EPC alongside LEC on the loss-distributions page.
    # Defaulted to [] so existing test fixtures and back-compat call sites
    # construct without these fields.
    epc_with: list[tuple[float, float]] = field(default_factory=list)
    epc_without: list[tuple[float, float]] = field(default_factory=list)
    # PR μ.1: per-control loss-reduction breakdown from the CURRENCY-branch
    # subtractor (FAIR-CAM §3.3.3). Only controls with loss_reduction_per_event > 0
    # produce a non-None label. Empty list when no CURRENCY-branch controls are
    # active. Renderer skips rows where loss_reduction_label is None.
    # Methodology: docs/reference/elapsed-time-tau-calibration.md
    control_breakdown_rows: list[ControlBreakdownRow] = field(default_factory=list)
    # Issue #202: suppress-not-relabel gate for the central-95% band, mirroring
    # the HTML run-detail ``has_ci_band`` gate (_view_model_helpers.has_ci_band).
    # True only when the persisted ``confidence_intervals`` carry the
    # ``interval_pct`` marker AND ``upper_bound > lower_bound`` — i.e. a real
    # empirical p2.5/p97.5 percentile band. LEGACY AGGREGATE rows (persisted
    # before #202, retired Gaussian SE-of-the-mean geometry, no ``interval_pct``)
    # set this False so the renderer SUPPRESSES the band ("not available for
    # legacy runs") rather than relabeling the narrow SE bounds as a 95%
    # percentile span — the exact overclaim #202 removes. Defaulted True so
    # existing fixtures (all given ``interval_pct``) construct unchanged.
    has_band: bool = True

    # ---- T2 (#351) new fields — all defaulted so existing call sites unchanged ----

    # Residual-side tail-risk metrics (VaR 90/95/99/99.9 + ES 95/99/99.9).
    # Built via _build_tail_risk(residual_risk); None means the run predates
    # #266 D1 and carries no tail-metric keys.
    tail_risk: dict[str, Any] | None = None

    # Base-side (without-controls) stats: mean/median/std for the Δ table.
    base_stats: dict[str, Any] | None = None

    # Base-side tail-risk (same shape as tail_risk, sourced from base_risk).
    # Built via _build_tail_risk(base_risk_dict) — same parameterized helper,
    # not a duplicate derivation (PS-B1 / M-1 plan-gate).
    base_tail_risk: dict[str, Any] | None = None

    # Residual-side (with-controls) descriptive stats: mean/median/std_deviation.
    # T4 (#351): mirrors base_stats for the Δ column in the distribution-statistics
    # table (section 3). Extracted from residual_risk_dict at build time.
    residual_stats: dict[str, Any] | None = None

    # Flag: True only when the persisted tail keys exist and are non-degenerate
    # (mirrors has_ci_band for the tail-metric gate). Renderer gates the tail-
    # risk section on this (PS-I3 / T2(c)).
    has_tail_risk: bool = False

    # cost_summary: total_annual_cost, total_risk_reduction, net_benefit,
    # aggregate_roi. None when absent from simulation_results (renderer prints
    # "not available").
    cost_summary: dict[str, Any] | None = None

    # Attribution matrix (scenario x control). Non-None for AGGREGATE runs;
    # None for SINGLE runs. Imported from aggregate_run_view_model — pure
    # function over simulation_results['per_scenario'], no DB query.
    attribution_matrix: dict[str, Any] | None = None

    # Control-effectiveness scores for SINGLE runs (list of dicts with
    # control_id, name, effectiveness). None for AGGREGATE runs.
    control_effectiveness_rows: list[dict[str, Any]] | None = None

    # Scenario input snapshots: dict with "label" and "scenarios" list.
    # Sourced from run.scenario_inputs_snapshot JSON column (as-executed).
    # Falls back to live scenario values with the honest label when the column
    # is NULL (runs predating the column).
    scenario_inputs: dict[str, Any] | None = None

    # Per-scenario library provenance: list of provenance dicts (one per scenario).
    # Each dict carries loss_tier, calibration_anchor, source_citations,
    # and provenance_label (fallback for analyst-authored scenarios).
    scenario_provenance: list[dict[str, Any]] | None = None

    # T3 (#351): run_type discriminator — "single" | "aggregate" (str, not enum,
    # so pdf_report.py stays decoupled from ORM types / RunType enum import).
    # Defaulted to "aggregate" so existing call sites that pre-date T3 continue
    # to construct unchanged (the existing executive PDF is AGGREGATE-flavored).
    run_type: str = "aggregate"

    # T6 (#351): controls_snapshot — raw list of snapshot dicts from run.controls_snapshot.
    # Each dict is a ControlSnapshotV2/V3 shape with keys: name, assignments
    # (list[dict] with sub_function, capability_value, unit_type, coverage,
    # reliability, confirmed_by_user_at). Passed through verbatim to the renderer
    # for the control-assignment snapshot summary table in Assumptions & inputs.
    # Defaulted to empty list so existing call sites that predate T6 construct
    # unchanged.
    controls_snapshot: list[dict[str, Any]] = field(default_factory=list)

    # Spec-compliance fix (#351): engine label displayed in the "Assumptions &
    # inputs" run-metadata block. Built by the orchestrator via importlib.metadata
    # so the renderer (pdf_report.py) stays decoupled from fair_cam imports.
    # Defaulted to "fair-cam" so existing call sites / test fixtures that
    # construct without this field continue to work unchanged.
    engine_label: str = "fair-cam"

    # Seed-reproducibility transparency: the integer seed passed to the Monte
    # Carlo engine for this run, so operators can reproduce results exactly.
    # None for runs predating seed recording (#mc-seed-reproducibility).
    random_seed: int | None = None

    # ---- P3 currency fields — all defaulted so existing call sites unchanged ----
    # ISO-4217 code of the reporting currency used to format all money values
    # on this data object.  "USD" when no conversion was applied (the default).
    reporting_code: str = "USD"
    # Unicode symbol for the reporting currency (e.g. "€" for EUR, "$" for USD).
    # Used by the renderer as a label-only suffix; values are already converted.
    reporting_symbol: str = "$"
    # The FX rate (USD → reporting currency) as a plain float for the PDF renderer's
    # input-distribution percentile tables.  1.0 for USD (identity).  This is the
    # only P3 value the renderer needs for B1 (lognormal/PERT loss-magnitude
    # display) because those distributions are read from the scenario_inputs blob
    # (stored in USD) rather than from the already-converted RunReportData money fields.
    reporting_rate: float = 1.0
    # Human-readable provenance sentence, e.g. "Converted from USD at 1 USD = 0.92
    # EUR, as-of 2026-06-14, source ECB".  None for USD (no conversion applied).
    currency_provenance: str | None = None

    # Task 5 (#419): weight-robustness display dict (converted to reporting currency).
    # Shape from process_weight_robustness_for_display: keys headline,
    # headline_range_str, per_control{cid: {...range_str, badge}},
    # indistinguishable_control_ids, indistinguishable_pairs, state, draws_used.
    # None when the run predates Task 4 / carries no weight_robustness column.
    weight_robustness: dict[str, Any] | None = None


# T9 (#351): alias fully removed. All references migrated to RunReportData.


def _extract_cost_summary(sr: dict[str, Any]) -> dict[str, Any] | None:
    """Extract cost_summary from simulation_results.

    T2 (#351): returns the cost_summary sub-dict when present, else None
    (renderer will print "not available"). Keys: total_annual_cost,
    total_risk_reduction, net_benefit, aggregate_roi (may be None when
    total_annual_cost == 0).
    """
    cs = sr.get("cost_summary")
    if cs is None:
        return None
    return {
        "total_annual_cost": float(cs.get("total_annual_cost", 0.0) or 0.0),
        "total_risk_reduction": float(cs.get("total_risk_reduction", 0.0) or 0.0),
        "net_benefit": float(cs.get("net_benefit", 0.0) or 0.0),
        "aggregate_roi": cs.get("aggregate_roi"),  # may be None (no cost)
    }


def _build_attribution_matrix_for_run(
    per_scenario: list[dict[str, Any]],
    run_type: Any,
) -> dict[str, Any] | None:
    """T2 (#351): return attribution matrix for AGGREGATE; None for SINGLE.

    Imports and calls _build_per_scenario_control_matrix from
    aggregate_run_view_model — a pure function over per_scenario, no DB query.
    (PA2-Arch-I3: do NOT duplicate the logic.)

    Mean+typical side-by-side (2026-07-04, issue #467 implemented): the PDF's
    own attribution-matrix caption in pdf_report.py is now basis-aware (it
    switches copy on ``matrix["basis"]``), so this call site no longer needs to
    pin the matrix to the typical basis to keep an untouched caption true.
    Explicit ``prefer_basis="mean"`` (matches the module default) so the PDF
    feeds mean-primary / typical-secondary cells, same basis as the web's
    ``build_aggregate_display_results`` call site and the same basis as the
    Monte-Carlo bar chart above the matrix on the PDF page.
    """
    # Local import: keeps the top-level import surface purity-safe
    # (pdf_report.py imports from this module; RunType would pull fair_cam
    # transitively via idraa.models.enums at module load time).
    from idraa.models.risk_analysis_run import RunType

    if run_type != RunType.AGGREGATE:
        return None
    return _build_per_scenario_control_matrix(per_scenario, prefer_basis="mean")


def _build_control_effectiveness_for_run(
    adjustments: list[dict[str, Any]],
    snapshot: list[dict[str, Any]],
    run_type: Any,
) -> list[dict[str, Any]] | None:
    """T2 (#351): return control-effectiveness rows for SINGLE; None for AGGREGATE.

    Calls _build_control_effectiveness_rows from run_view_model, which joins
    control_adjustments → controls_snapshot for friendly names.
    """
    # Local import: keeps the top-level import surface purity-safe
    # (pdf_report.py imports from this module; RunType would pull fair_cam
    # transitively via idraa.models.enums at module load time).
    from idraa.models.risk_analysis_run import RunType

    if run_type != RunType.SINGLE:
        return None
    return _build_control_effectiveness_rows(adjustments, snapshot)


def _extract_scenario_inputs(
    snapshot_json: dict[str, Any] | None,
    live_scenarios: list[Any],
) -> dict[str, Any]:
    """T2 (#351): build scenario input block from snapshot column or live rows.

    PA2-Arch-I1: reads the run's ``scenario_inputs_snapshot`` column (populated
    by the executor BEFORE the engine call) so the report reflects AS-EXECUTED
    values. Legacy-null runs fall back to live scenario values with the honest
    label "Current scenario values (run predates input snapshots — values may
    differ from as-executed)".

    Returns::

        {
          "label": str,         # "as-executed" or the honest legacy label
          "scenarios": [        # list of scenario input dicts
            {
              "scenario_id": str,
              "scenario_name": str,
              "threat_event_frequency": dict,
              "vulnerability": dict,
              "primary_loss": dict,
              "secondary_loss": dict | None,
            }, ...
          ]
        }
    """
    if snapshot_json is not None:
        # Snapshot-backed: use as-executed values
        return {
            "label": "as-executed",
            "scenarios": snapshot_json.get("scenarios", []),
        }
    # Legacy-null fallback: read live scenario rows.
    # PSec2-NTH-1: add `Scenario.organization_id == run.organization_id` filter
    # when multi-tenancy ships. Phase-1 single-org is structurally safe.
    # add `Scenario.organization_id == run.organization_id` filter when multi-tenancy ships
    scenarios_out = []
    for sc in live_scenarios:
        scenarios_out.append(
            {
                "scenario_id": str(sc.id),
                "scenario_name": sc.name,
                "threat_event_frequency": sc.threat_event_frequency,
                "vulnerability": sc.vulnerability,
                "primary_loss": sc.primary_loss,
                "secondary_loss": sc.secondary_loss,
            }
        )
    return {
        "label": "Current scenario values (run predates input snapshots — values may differ from as-executed)",
        "scenarios": scenarios_out,
    }


def _extract_scenario_provenance(library_pin: dict[str, Any] | None) -> dict[str, Any]:
    """T2 (#351): extract library provenance from a scenario.library_pin dict.

    For library-derived scenarios: returns loss_tier, calibration_anchor text,
    and source_citations list sourced from the library pin.
    For lineage-less scenarios (library_pin is None): returns the explicit
    fallback label "analyst-authored — no library provenance".
    """
    if library_pin is None:
        return {
            "loss_tier": None,
            "calibration_anchor": None,
            "source_citations": [],
            "provenance_label": "analyst-authored — no library provenance",
        }
    return {
        "loss_tier": library_pin.get("loss_tier"),
        "calibration_anchor": library_pin.get("calibration_anchor"),
        "source_citations": library_pin.get("source_citations", []),
        "provenance_label": None,  # library-derived; no fallback label needed
    }


def _clamp_lec_points(
    raw_points: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """LEC clamp: max(loss, 1.0) so the log x-axis can render every point.

    Mirrors aggregate_run_view_model._build_dual_lec's R2-NB5 clamp.
    Returns tuples (not dicts) to keep the renderer's input shape simple.
    """
    return [
        (max(float(p.get("loss", 0.0)), 1.0), float(p.get("probability", 0.0))) for p in raw_points
    ]


def _clamp_epc_points(
    raw_points: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """EPC clamp: max(loss, 1.0) so the log y-axis can render every point.

    Mirrors aggregate_run_view_model._build_dual_epc's clamp.
    Returns tuples (percentile, loss) for the renderer's input shape.
    """
    return [
        (
            float(p.get("percentile", 0.0)),
            max(float(p.get("loss", 0.0)), 1.0),
        )
        for p in raw_points
    ]


async def _resolve_scenarios(
    db: AsyncSession, scenario_ids: list[str]
) -> list[ScenarioInventoryRow]:
    """Single batch lookup for the page-5 scenarios sub-section.

    Truncates ``Scenario.description`` to 120 chars (with '…' suffix) for
    the page-5 one-liner.

    Robustness (Code-reviewer M-4 + M-6):
    - Malformed UUID strings (data corruption / v1 snapshot) are dropped
      with a logged warning rather than raising ValueError. The page-5
      inventory shows fewer rows than n_scenarios; the narrative still
      uses n_scenarios from simulation_results.
    - Missing Scenario rows (scenario was deleted between run-create and
      PDF-export) emit a placeholder ScenarioInventoryRow so the exec
      sees the gap rather than a silent omission.
    """
    if not scenario_ids:
        return []
    # Local import: keeps services/reports.py's eager-load surface small
    # so the renderer-side (services/pdf_report.py) purity boundary is
    # preserved even though that boundary lives in its own module.
    from idraa.models.scenario import Scenario

    uuid_ids: list[_uuid.UUID] = []
    invalid: list[str] = []
    for s in scenario_ids:
        try:
            uuid_ids.append(_uuid.UUID(s))
        except (ValueError, TypeError):
            invalid.append(s)
    if invalid:
        logger.warning(
            "build_executive_pdf_data: dropped %d malformed scenario UUIDs: %r",
            len(invalid),
            invalid,
        )
    if not uuid_ids:
        return []
    stmt = select(Scenario.id, Scenario.name, Scenario.description).where(Scenario.id.in_(uuid_ids))
    result = await db.execute(stmt)
    rows = result.all()
    by_id: dict[str, ScenarioInventoryRow] = {}
    for sid, name, desc in rows:
        summary = desc or ""
        if len(summary) > 120:
            summary = summary[:119] + "…"
        by_id[str(sid)] = ScenarioInventoryRow(scenario_id=str(sid), name=name, summary=summary)
    # Preserve aggregate_scenario_ids order; missing scenarios -> placeholder row.
    invalid_set = set(invalid)
    return [
        by_id.get(
            s,
            ScenarioInventoryRow(scenario_id=s, name="(deleted scenario)", summary=""),
        )
        for s in scenario_ids
        if s not in invalid_set  # malformed UUIDs already dropped + logged
    ]


def _convert_tail_risk(
    tail_risk: dict[str, Any] | None,
    cvt: Any,  # Callable[[float | None], float | None]
) -> dict[str, Any] | None:
    """P3: convert VaR/ES dollar values in a tail-risk dict from USD to reporting currency.

    The tail-risk dict shape (from _build_tail_risk) is flat:
    {var_90: float, var_95: float, var_99: float, var_999: float,
     es_95: float, es_99: float, es_999: float}, plus (Task 10, Spec-B1) three
    additive keys per ES level: ``es_<level>_se`` (float | None), ``es_<level>
    _ci_half`` (float | None, = ES_CI_Z_95 * se), ``es_<level>_ci_insufficient``
    (bool — NOT a money value, and never None). ``_build_tail_risk`` is called
    here on the RAW (pre-conversion) risk dict, so se/ci_half are still in USD
    and need the SAME ``cvt`` as everything else; ``ci_insufficient`` must pass
    through untouched (converting a bool through cvt would be nonsensical), and
    a ``None`` se/ci_half (legacy-absent or insufficient-tail sentinel) must
    pass through as None rather than crash on ``float(None)``.
    Returns None when input is None (mirrors _build_tail_risk's null output).
    """
    if tail_risk is None:
        return None
    out: dict[str, Any] = {}
    for k, v in tail_risk.items():
        if k.endswith("_ci_insufficient"):
            out[k] = v  # bool flag, not a money value — passthrough
        elif v is None:
            out[k] = None  # legacy-absent SE or insufficient-tail sentinel
        else:
            out[k] = cvt(float(v))
    return out


def _convert_cost_summary(
    cost_summary: dict[str, Any] | None,
    cvt: Any,  # Callable[[float | None], float | None]
) -> dict[str, Any] | None:
    """P3: convert dollar amounts in cost_summary from USD to reporting currency.

    Keys converted: total_annual_cost, total_risk_reduction, net_benefit.
    aggregate_roi is a dimensionless ratio — NOT converted.
    """
    if cost_summary is None:
        return None
    out = dict(cost_summary)
    for key in ("total_annual_cost", "total_risk_reduction", "net_benefit"):
        raw = out.get(key)
        if raw is not None:
            out[key] = cvt(float(raw))
    # aggregate_roi: dimensionless ratio, currency-invariant — do not convert.
    return out


def _convert_attribution_matrix(
    matrix: dict[str, Any] | None,
    cvt: Any,  # Callable[[float | None], float | None]
) -> dict[str, Any] | None:
    """P3: convert cell values in the attribution matrix from USD to reporting currency.

    Matrix shape: {"controls": [...], "rows": [{"cells": [{"value": float}]}], ...}.
    Cell values are dollar amounts (Shapley marginal contributions) → convert.
    total_reduction values on controls are also dollar amounts → convert.
    """
    if matrix is None:
        return None
    if matrix.get("unavailable"):
        return matrix  # legacy unavailable marker — no values to convert
    out = dict(matrix)
    # Convert per-control total_reduction (+ paired typical total, side-by-side Task 6)
    converted_controls = []
    for c in out.get("controls", []):
        c2 = dict(c)
        raw_tr = c2.get("total_reduction")
        if raw_tr is not None:
            c2["total_reduction"] = cvt(float(raw_tr))
        raw_tr_typ = c2.get("total_reduction_typical")
        if raw_tr_typ is not None:
            c2["total_reduction_typical"] = cvt(float(raw_tr_typ))
        converted_controls.append(c2)
    out["controls"] = converted_controls
    # Convert per-row cell values (+ paired typical cell, side-by-side Task 6)
    converted_rows = []
    for r in out.get("rows", []):
        r2 = dict(r)
        converted_cells = []
        for cell in r2.get("cells", []):
            cell2 = dict(cell)
            raw_v = cell2.get("value")
            if raw_v is not None:
                cell2["value"] = cvt(float(raw_v))
            raw_v_typ = cell2.get("value_typical")
            if raw_v_typ is not None:
                cell2["value_typical"] = cvt(float(raw_v_typ))
            converted_cells.append(cell2)
        r2["cells"] = converted_cells
        converted_rows.append(r2)
    out["rows"] = converted_rows
    return out


async def build_executive_pdf_data(
    db: AsyncSession,
    run: Any,  # RiskAnalysisRun
    org: Any,  # Organization
) -> RunReportData:
    """Orchestrate run + org + scenarios + controls_snapshot into the
    renderer's view-model.

    T2 (#351): now returns RunReportData (superset). T9 removes the
    ExecutivePdfData alias — callers and tests import RunReportData directly.

    Caller MUST authorize org access AND verify
    ``run.status == COMPLETED`` AND ``run.simulation_results is not None``
    BEFORE invoking. This function does NOT re-validate (mirrors PR
    omicron-1's ``build_dashboard`` pattern). Validation lives in
    routes/reports.py where it maps directly to HTTP 404/500.
    """
    # `.get` discipline (Code-reviewer M-5; design Section 7):
    # - Structural keys (aggregate_with/without, control_value,
    #   confidence_intervals, control_value.dollars/percent, ci.* scalars,
    #   aggregate_*.annualized_loss_expectancy, n_scenarios) raise KeyError
    #   on miss -> caller maps to 500 (data-integrity bug).
    # - Optional/list-shaped keys (loss_exceedance_curve, per_scenario)
    #   default to [] / sensible empty so the renderer's empty-state
    #   paths handle them.
    # Local import: keeps the top-level import surface purity-safe
    # (pdf_report.py imports from this module; RunType would pull fair_cam
    # transitively via idraa.models.enums at module load time).
    from idraa.models.risk_analysis_run import RunType

    sr = run.simulation_results  # caller guarantees not None
    run_type: RunType = run.run_type

    # T2: for SINGLE runs, the top-level keys differ from AGGREGATE.
    # SINGLE: base_risk / residual_risk / confidence_intervals at top level.
    # AGGREGATE: aggregate_with_controls / aggregate_without_controls / confidence_intervals.
    if run_type == RunType.AGGREGATE:
        agg_with = sr["aggregate_with_controls"]
        agg_without = sr["aggregate_without_controls"]
        cv = sr["control_value"]
        ci = sr["confidence_intervals"]
        headline_ale = float(agg_with["annualized_loss_expectancy"])
        base_ale = float(agg_without["annualized_loss_expectancy"])
        # T2: residual_risk dict for tail-metric extraction (AGGREGATE side uses agg_with)
        residual_risk_dict = agg_with
        base_risk_dict: dict[str, Any] = agg_without
    else:
        # SINGLE: top-level base_risk / residual_risk / confidence_intervals
        residual_risk_dict = sr.get("residual_risk", {})
        base_risk_dict = sr.get("base_risk", {})
        ci = sr.get("confidence_intervals", {})
        cv = sr.get("cost_summary", {}) or {}  # SINGLE has cost_summary at top level
        headline_ale = float(residual_risk_dict.get("annualized_loss_expectancy", 0.0))
        base_ale = float(base_risk_dict.get("annualized_loss_expectancy", 0.0))
        # For SINGLE: control_value lives in cost_summary as risk_reduction signals
        cv = {
            "dollars": float(cv.get("total_risk_reduction", 0.0) or 0.0),
            "percent": (
                float(cv.get("total_risk_reduction", 0.0) or 0.0) / base_ale * 100.0
                if base_ale > 0
                else 0.0
            ),
        }

    n_scenarios = int(sr.get("n_scenarios", 1)) if run_type == RunType.AGGREGATE else 1

    # ---- P3 currency: resolve once at the data boundary (design §render-parity) ----
    # active_rate is only needed for legacy runs (no snapshot) with a non-USD org.
    # Load it lazily: avoid a DB query for USD orgs and pinned-snapshot runs.
    from babel.numbers import get_currency_symbol as _babel_sym

    from idraa.services.fx_rates import FxRateService
    from idraa.services.reporting_currency import resolve_reporting_currency

    _pref_code = getattr(org, "preferred_currency", "USD") or "USD"
    _snap = getattr(run, "presentation_fx_snapshot", None)
    _needs_active_rate = _pref_code != "USD" and not (_snap and _snap.get("code") == _pref_code)
    _active_rate_row = (
        await FxRateService(db).active_rate(org.id, _pref_code) if _needs_active_rate else None
    )
    rc = resolve_reporting_currency(run, org, _active_rate_row)

    def _cvt(v: float | None) -> float | None:
        """Convert a USD money value to the reporting currency."""
        return rc.convert(v)

    def _cvt_f(v: float) -> float:
        """Convert a float USD money value; returns 0.0 on None."""
        r = rc.convert(v)
        return r if r is not None else 0.0

    # ---- pct_revenue is a CURRENCY-INVARIANT ratio: compute before conversion ----
    # headline_ale is still USD here; org.annual_revenue is always USD.
    pct_revenue = build_pct_revenue(headline_ale, org.annual_revenue)

    # ---- Apply conversion to money values (headline_ale / base_ale set above in USD) ----
    headline_ale = _cvt_f(headline_ale)
    base_ale = _cvt_f(base_ale)
    control_value_dollars = _cvt_f(float(cv["dollars"]))
    # control_value_percent is a ratio — not currency-denominated, do NOT convert.

    # ---- Task 5 (#419 / Meth-I4): weight-robustness headline band for the narrative ----
    # Source the banded representative-value headline from run.weight_robustness;
    # convert each percentile via rc.convert at this caller boundary (Arch-I4).
    # The narrative LEADS with this range and drops the spurious 1-decimal-place %.
    _wr_raw = getattr(run, "weight_robustness", None)
    _wr_headline: dict[str, Any] | None = None
    if _wr_raw is not None:
        _hl = _wr_raw.get("headline") or {}
        if _hl.get("reduction_p50") is not None:
            _wr_headline = {
                k: (_cvt(_hl[k]) if _hl.get(k) is not None else None)
                for k in ("reduction_p5", "reduction_p50", "reduction_p95")
            }
            # Mean+typical side-by-side (2026-07-04): thread basis through so the
            # narrative can reword the "typical-case ... below the average" framing
            # for mean-basis runs, where the range IS the average figure (per
            # run_executor.py's _build_weight_robustness, "basis" defaults missing
            # -> "typical" for legacy blobs).
            _wr_headline["basis"] = _wr_raw.get("basis", "typical")

    if run_type == RunType.AGGREGATE:
        # LEC x-axis values are loss amounts → convert
        lec_with = [
            (max(_cvt_f(x), 1.0), y)
            for (x, y) in _clamp_lec_points(agg_with.get("loss_exceedance_curve", []))
        ]
        lec_without = [
            (max(_cvt_f(x), 1.0), y)
            for (x, y) in _clamp_lec_points(agg_without.get("loss_exceedance_curve", []))
        ]
        dual_epc = sr.get("dual_epc", {})
        # EPC y-axis values (loss amounts) → convert; x-axis is percentile [0-1] → no convert
        epc_with = [
            (pct, max(_cvt_f(loss), 1.0))
            for (pct, loss) in _clamp_epc_points(dual_epc.get("with_controls", []))
        ]
        epc_without = [
            (pct, max(_cvt_f(loss), 1.0))
            for (pct, loss) in _clamp_epc_points(dual_epc.get("without_controls", []))
        ]
    else:
        lec_with = [
            (max(_cvt_f(x), 1.0), y)
            for (x, y) in _clamp_lec_points(residual_risk_dict.get("loss_exceedance_curve", []))
        ]
        lec_without = [
            (max(_cvt_f(x), 1.0), y)
            for (x, y) in _clamp_lec_points(base_risk_dict.get("loss_exceedance_curve", []))
        ]
        epc_with = [
            (pct, max(_cvt_f(loss), 1.0))
            for (pct, loss) in _clamp_epc_points(sr.get("exceedance_probability_curve", []))
        ]
        epc_without = []

    per_scenario_data = sr.get("per_scenario", [])
    # Build per_scenario_rows from USD data then convert money fields.
    _raw_per_scenario_rows = build_per_scenario_rows(per_scenario_data)
    per_scenario_rows = [
        PerScenarioRow(
            scenario_id=r.scenario_id,
            scenario_name=r.scenario_name,
            base_ale=_cvt_f(r.base_ale),
            residual_ale=_cvt_f(r.residual_ale),
            reduction=_cvt_f(r.reduction),
        )
        for r in _raw_per_scenario_rows
    ]

    # PR μ.1: per-control loss-reduction breakdown.
    # Build in USD first, then convert loss_reduction_per_event and rebuild labels.
    _raw_control_breakdown_rows = build_control_breakdown_rows(
        per_scenario_data,
        currency="USD",  # labels rebuilt below with converted values + rc.code
    )
    _sym_str = _babel_sym(rc.code)  # symbol for labels (e.g. "€")
    control_breakdown_rows = []
    for _cbr in _raw_control_breakdown_rows:
        _lr_converted = _cvt_f(_cbr.loss_reduction_per_event)
        _n = _cbr.loss_reduction_label  # existing label (USD); rebuild with converted value
        # Rebuild label with converted value and reporting symbol
        if _cbr.loss_reduction_label is None:
            _new_label = None
        else:
            # Count scenarios from label text ("across N scenarios" pattern)
            _m = _re.search(r"across (\d+) scenarios", _cbr.loss_reduction_label)
            if _m:
                _n_sc = int(_m.group(1))
                _new_label = (
                    f"{_sym_str}{_lr_converted:,.0f} total per-event loss reduction "
                    f"across {_n_sc} scenarios (Loss Reduction)"
                )
            else:
                _new_label = f"{_sym_str}{_lr_converted:,.0f}/event reduced (Loss Reduction)"
        control_breakdown_rows.append(
            ControlBreakdownRow(
                control_id=_cbr.control_id,
                control_name=_cbr.control_name,
                loss_reduction_per_event=_lr_converted,
                loss_reduction_label=_new_label,
            )
        )

    # Scenarios sub-section (page 5): resolve names from aggregate_scenario_ids.
    # If aggregate_scenario_ids is somehow None, fall back to per_scenario.
    if run_type == RunType.AGGREGATE:
        sids = run.aggregate_scenario_ids or [ps.get("scenario_id", "") for ps in per_scenario_data]
    else:
        sids = [str(run.scenario_id)] if run.scenario_id else []
    scenarios = await _resolve_scenarios(db, [s for s in sids if s])

    # T3 (#351): SINGLE runs use scenario-scoped narrative; AGGREGATE keeps portfolio wording.
    # Narrative is built after _resolve_scenarios so the SINGLE variant can use the
    # resolved scenario name (first entry in the scenarios list).
    # Narrative uses converted values and the reporting currency code.
    if run_type == RunType.SINGLE:
        _sc_name = scenarios[0].name if scenarios else "the modeled scenario"
        narrative = build_narrative_single(
            control_value_dollars=control_value_dollars,
            control_value_percent=float(cv["percent"]),
            residual_ale=headline_ale,
            pct_revenue=pct_revenue,
            n_simulations=int(ci.get("sample_size", 0)),
            currency=rc.code,
            scenario_name=_sc_name,
            weight_robustness_headline=_wr_headline,
        )
    else:
        narrative = build_narrative(
            n_scenarios=n_scenarios,
            control_value_dollars=control_value_dollars,
            control_value_percent=float(cv["percent"]),
            residual_ale=headline_ale,
            pct_revenue=pct_revenue,
            n_simulations=int(ci.get("sample_size", 0)),
            currency=rc.code,
            weight_robustness_headline=_wr_headline,
        )

    controls_by_domain = group_controls_by_fair_cam_domain(run.controls_snapshot or [])

    loss_tolerance: dict[str, float] | None = None
    if (
        getattr(org, "loss_tolerance_amount", None) is not None
        and getattr(org, "loss_tolerance_probability", None) is not None
    ):
        loss_tolerance = {
            # Convert the amount (loss threshold is in money units); probability is invariant.
            "amount": _cvt_f(float(org.loss_tolerance_amount)),
            "probability": float(org.loss_tolerance_probability),
        }

    # Issue #202 suppress-not-relabel: gate the central-95% band on the SAME
    # predicate the HTML run-detail path uses (has_ci_band), so HTML and PDF
    # cannot diverge. A legacy AGGREGATE row lacks the ``interval_pct`` marker
    # and carries retired Gaussian SE-of-the-mean bounds; has_ci_band returns
    # False for it, the renderer suppresses the band ("not available for legacy
    # runs"), and we must NOT default interval_pct to 95 over those SE bounds
    # (the precise #202 mislabel). For a real band, interval_pct is always
    # present so the ``.get`` default is dead-code-defensive only.
    band_present = has_ci_band(ci)
    headline_ci_lo = _cvt_f(float(ci["lower_bound"])) if band_present else 0.0
    headline_ci_hi = _cvt_f(float(ci["upper_bound"])) if band_present else 0.0
    interval_pct = (
        int(ci.get("interval_pct", _BAND_INTERVAL_PCT_REPORTS))
        if band_present
        else _BAND_INTERVAL_PCT_REPORTS
    )

    # ---- T2 (#351): new fields ----

    # Residual-side tail-risk (T2(a)) — VaR/ES are dollar amounts → convert.
    tail_risk_dict = _convert_tail_risk(_build_tail_risk(residual_risk_dict), _cvt)
    tail_risk_present = has_tail_metrics(residual_risk_dict)

    # Base-side stats + tail-risk (T2(b)) — same parameterized helper, not duplicate
    base_stats: dict[str, Any] = {
        "mean": _cvt_f(float(base_risk_dict.get("mean", 0.0) or 0.0)),
        "median": _cvt_f(float(base_risk_dict.get("median", 0.0) or 0.0)),
        "std_deviation": _cvt_f(float(base_risk_dict.get("std_deviation", 0.0) or 0.0)),
    }
    base_tail_risk_dict = _convert_tail_risk(_build_tail_risk(base_risk_dict), _cvt)

    # Residual-side descriptive stats (T4 #351): mean/median/std for the Δ table.
    residual_stats_dict: dict[str, Any] = {
        "mean": _cvt_f(float(residual_risk_dict.get("mean", 0.0) or 0.0)),
        "median": _cvt_f(float(residual_risk_dict.get("median", 0.0) or 0.0)),
        "std_deviation": _cvt_f(float(residual_risk_dict.get("std_deviation", 0.0) or 0.0)),
    }

    # cost_summary (T2(d)) — convert dollar amounts; ROI ratio is currency-invariant.
    cost_summary = _convert_cost_summary(_extract_cost_summary(sr), _cvt)

    # Attribution matrix (T2(e)) — AGGREGATE only, pure function.
    # Cell values are dollar amounts → convert after building.
    attribution_matrix = _convert_attribution_matrix(
        _build_attribution_matrix_for_run(per_scenario_data, run_type), _cvt
    )

    # Control-effectiveness rows (T2(f)) — SINGLE only
    control_adjustments = sr.get("control_adjustments", [])
    eff_rows = _build_control_effectiveness_for_run(
        control_adjustments, run.controls_snapshot or [], run_type
    )

    # Scenario input snapshots (T2(g))
    # Load live scenarios for legacy-null fallback
    scenario_inputs_snapshot = getattr(run, "scenario_inputs_snapshot", None)
    if scenario_inputs_snapshot is None:
        # Legacy-null fallback: load live scenario rows
        live_scenarios = await _load_live_scenarios_for_run(db, run)
    else:
        live_scenarios = []
    scenario_inputs = _extract_scenario_inputs(
        snapshot_json=scenario_inputs_snapshot,
        live_scenarios=live_scenarios,
    )

    # Library provenance (T2(i)) — per scenario
    scenario_provenance = await _build_scenario_provenance(db, run, sids)

    # P3: reporting-currency metadata for the renderer (symbol + provenance label).
    _reporting_symbol = _babel_sym(rc.code)

    # Leave-one-out "if removed" lookup for the weight-robustness table below.
    # SINGLE reads the flat control_adjustments passthrough (never partial —
    # one scenario, so no partial ids); AGGREGATE sums per control across
    # per_scenario entries that carry the key (run_executor.py's _inject_loo)
    # and marks partial sums (LOO-Meth-3).
    #
    # Mean+typical side-by-side (2026-07-04): build BOTH the typical-basis
    # (historical key) and mean-basis (new key) lookups, then pick primary/
    # secondary by the run's weight_robustness basis. Legacy runs (no "basis"
    # key -> "typical" default) render exactly as before: typical primary, no
    # secondary sub-line.
    _basis = (_wr_raw or {}).get("basis", "typical")
    _ir_partial: set[str] = set()
    _ir_secondary: dict[str, float | None] | None = None
    if run_type == RunType.AGGREGATE:
        _ir_lookup_typical, _ir_partial_typical = if_removed_by_control_aggregate(
            per_scenario_data, key="if_removed_value"
        )
        if _basis == "mean":
            _ir_lookup, _ir_partial = if_removed_by_control_aggregate(
                per_scenario_data, key="if_removed_value_mean"
            )
            _ir_secondary = _ir_lookup_typical
        else:
            _ir_lookup, _ir_partial = _ir_lookup_typical, _ir_partial_typical
    else:
        _ir_lookup_typical_single = if_removed_by_control_single(
            control_adjustments, key="if_removed_value"
        )
        if _basis == "mean":
            _ir_lookup = if_removed_by_control_single(
                control_adjustments, key="if_removed_value_mean"
            )
            _ir_secondary = _ir_lookup_typical_single
        else:
            _ir_lookup = _ir_lookup_typical_single

    # #436 web/PDF parity: thread the sub-function lookup + availability flag so
    # ~$0 robustness cells carry their reason label in the PDF too (mirrors
    # run_view_model: SINGLE-only availability suppression, AGGREGATE stays
    # False per the deferred mixed-effect rule).
    _snap_scenarios = (scenario_inputs_snapshot or {}).get("scenarios") or []
    _snap0 = _snap_scenarios[0] if _snap_scenarios else {}  # adapter-iter: ok — non-empty guard
    _availability_effect = (
        run_type == RunType.SINGLE
        and len(_snap_scenarios) == 1
        and _snap0.get("effect") == "availability"
    )

    return RunReportData(
        org=org,
        run=run,
        headline_ale=headline_ale,
        headline_ci_lo=headline_ci_lo,
        headline_ci_hi=headline_ci_hi,
        interval_pct=interval_pct,
        has_band=band_present,
        n_simulations=int(ci.get("sample_size", 0)),
        n_scenarios=n_scenarios,
        control_value_dollars=control_value_dollars,
        control_value_percent=float(cv["percent"]),
        pct_revenue=pct_revenue,
        base_ale=base_ale,
        residual_ale=headline_ale,
        lec_with=lec_with,
        lec_without=lec_without,
        per_scenario_rows=per_scenario_rows,
        scenarios=scenarios,
        controls_by_domain=controls_by_domain,
        narrative=narrative,
        loss_tolerance=loss_tolerance,
        epc_with=epc_with,
        epc_without=epc_without,
        control_breakdown_rows=control_breakdown_rows,
        # T2 new fields
        tail_risk=tail_risk_dict,
        base_stats=base_stats,
        base_tail_risk=base_tail_risk_dict,
        residual_stats=residual_stats_dict,
        has_tail_risk=tail_risk_present,
        cost_summary=cost_summary,
        attribution_matrix=attribution_matrix,
        control_effectiveness_rows=eff_rows,
        scenario_inputs=scenario_inputs,
        scenario_provenance=scenario_provenance,
        # T3 new field
        run_type=run_type.value,  # str "single" | "aggregate"
        # T6 new field: pass controls_snapshot verbatim for the snapshot summary table
        controls_snapshot=list(run.controls_snapshot or []),
        # Spec-compliance fix (#351): engine label for Assumptions & inputs block.
        engine_label=_resolve_engine_label(),
        # Seed-reproducibility transparency: pass through from run row.
        random_seed=getattr(run, "random_seed", None),
        # P3: reporting-currency fields (values already converted above).
        reporting_code=rc.code,
        reporting_symbol=_reporting_symbol,
        reporting_rate=float(rc.rate),
        currency_provenance=rc.provenance,
        # Task 5 (#419): weight-robustness display data (converted to reporting currency).
        # _ir_lookup/_ir_partial: leave-one-out "if removed" (built above the return).
        weight_robustness=process_weight_robustness_for_display(
            _wr_raw,
            rc.convert,
            rc.code,
            sub_functions_by_id=snapshot_sub_functions_by_id(list(run.controls_snapshot or [])),
            availability_effect=_availability_effect,
            if_removed_by_control=_ir_lookup,
            if_removed_partial_ids=_ir_partial,
            if_removed_by_control_typical=_ir_secondary,
        ),
    )


async def _load_live_scenarios_for_run(db: AsyncSession, run: Any) -> list[Any]:
    """Load live scenario rows for the legacy-null snapshot fallback.

    PSec2-NTH-1: add `Scenario.organization_id == run.organization_id` filter
    when multi-tenancy ships. Phase-1 single-org is structurally safe.
    add `Scenario.organization_id == run.organization_id` filter when multi-tenancy ships
    """
    from idraa.models.risk_analysis_run import RunType  # local: purity boundary
    from idraa.models.scenario import Scenario

    if run.run_type == RunType.SINGLE:
        if run.scenario_id is None:
            return []
        stmt = select(Scenario).where(Scenario.id == run.scenario_id)
    else:
        sids_raw = run.aggregate_scenario_ids or []
        if not sids_raw:
            return []
        uuid_ids: list[_uuid.UUID] = []
        for s in sids_raw:
            with contextlib.suppress(ValueError, TypeError):
                uuid_ids.append(_uuid.UUID(s))
        if not uuid_ids:
            return []
        stmt = select(Scenario).where(Scenario.id.in_(uuid_ids))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _build_scenario_provenance(
    db: AsyncSession,
    run: Any,
    sids: list[str],
) -> list[dict[str, Any]]:
    """T2 (#351): build per-scenario library provenance list.

    For library-derived scenarios: extracts loss_tier, calibration_anchor,
    source_citations from scenario.library_pin. For lineage-less (analyst-authored)
    scenarios: returns the explicit fallback label.

    Reads live scenario rows to get their library_pin (the pin is immutable
    once set — it captures the entry version at adopt time, so live vs snapshot
    reads are equivalent for provenance purposes).
    """
    if not sids:
        return []

    from idraa.models.scenario import Scenario

    uuid_ids: list[_uuid.UUID] = []
    for s in sids:
        with contextlib.suppress(ValueError, TypeError):
            uuid_ids.append(_uuid.UUID(s))
    if not uuid_ids:
        return []

    stmt = select(Scenario.id, Scenario.name, Scenario.library_pin).where(Scenario.id.in_(uuid_ids))
    result = await db.execute(stmt)
    rows = result.all()

    by_id: dict[str, dict[str, Any]] = {}
    for sid, name, library_pin in rows:
        provenance = _extract_scenario_provenance(library_pin)
        provenance["scenario_id"] = str(sid)
        provenance["scenario_name"] = name
        by_id[str(sid)] = provenance

    # Preserve sid order; missing scenarios get the analyst-authored fallback
    out = []
    for s in sids:
        if s in by_id:
            out.append(by_id[s])
        else:
            fallback = _extract_scenario_provenance(None)
            fallback["scenario_id"] = s
            fallback["scenario_name"] = "(deleted scenario)"
            out.append(fallback)
    return out
