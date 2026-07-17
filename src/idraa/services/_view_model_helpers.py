"""Shared helpers for run_view_model + aggregate_run_view_model.

Promoted from PR nu's private helpers in run_view_model.py during PR xi
to avoid cross-module private import.

T2 (enterprise-pdf-reports): `_build_tail_risk` MOVED here from
run_view_model.py so both run_view_model (SINGLE view) and reports.py
(PDF report orchestrator) can share the same pure dict reader without a
circular import or logic duplication. `has_tail_metrics` mirrors
`has_ci_band` for the tail-metric gate.

Task 1 (#353): `DIST_STATS_DEFINITIONAL_NOTE` defined here (not in
services/reports.py) to avoid a circular import — reports.py already
imports from aggregate_run_view_model, which would need to import the
constant back from reports.py. services/reports.py re-exports the name
as a public alias so pdf_report.py and web consumers both see it under
the same public path.

Task 5 (#419): `control_value_range`, `stability_badge`, and
`process_weight_robustness_for_display` added here (alongside the other
shared note constants) to avoid a circular import and so all surfaces
(web, PDF, Excel) share one canonical formatter.

Issue #436: `control_zero_value_reason` and `snapshot_sub_functions_by_id`
added here so the structural-zero reason label is computed at the view-model
boundary (same convert-once pattern as the monetary values).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# ---- Task 1 (#353): DIST_STATS_DEFINITIONAL_NOTE — single source of truth ----
#
# Defined here (not in services/reports.py) to avoid a circular import:
# reports.py imports from aggregate_run_view_model.py, which would need to
# import this constant back from reports.py.  services/reports.py re-exports
# this name as a public alias so all consumers (pdf_report.py, view-models)
# see it under one canonical public path.
#
# Methodology-gated wording constraints:
#   - VaR_q: "q-th percentile of simulated annual loss (empirical)"
#   - ES_q: "mean simulated annual loss at or above the q-th percentile (empirical)"
#   - "at or above" is REQUIRED (NOT "beyond") — executor computes samples >=
#     threshold (not > threshold).  Byte-identical to the PDF renderer's
#     former _DIST_STATS_DEFINITIONAL_NOTE.
DIST_STATS_DEFINITIONAL_NOTE: str = (
    "VaR_q: q-th percentile of simulated annual loss (empirical). "
    "ES_q: mean simulated annual loss at or above the q-th percentile (empirical)."
)

# ---- T2(review) (#353): TAIL_LADDER_LABELS — single source of truth for VaR/ES row labels ----
#
# Defined here alongside DIST_STATS_DEFINITIONAL_NOTE to prevent label fork
# between the web builder (build_dist_stats_rows) and the PDF renderer
# (pdf_report.py _build_dist_stats_section).  The canonical form includes the
# '%' suffix — "VaR 95%", NOT "VaR 95".
#
# services/reports.py re-exports this dict as a public alias so pdf_report.py
# can consume it via the allowed ``from idraa.services.reports import …``
# pattern without touching _view_model_helpers directly.
TAIL_LADDER_LABELS: dict[str, str] = {
    # Core descriptive stats (always rendered, non-gated)
    "mean": "Mean",
    "median": "Median",
    "std_dev": "Std dev",
    # VaR rows — gated on has_tail_metrics (except var_95 / var_99 on legacy runs)
    "var_90": "VaR 90%",
    "var_95": "VaR 95%",
    "var_99": "VaR 99%",
    "var_999": "VaR 99.9%",
    # ES rows — gated on has_tail_metrics
    "es_95": "ES 95%",
    "es_99": "ES 99%",
    "es_999": "ES 99.9%",
}

# ---- Run-detail redesign (2026-07-03 spec §Label map) ----------------------
#
# Shared web + PDF display labels, keyed on the canonical TAIL_LADDER_LABELS
# VALUES. Originally a deliberate, documented WEB-ONLY fork (the web run-detail
# page showed plain-language + technical, e.g. "1-in-10 year (VaR 90%)", while
# the PDF kept the bare canonical technical strings). Adopted for the PDF too
# per the 2026-07-04 methodology gate (T5b, dual-form labels judged faithful —
# see below — with the PDF additionally carrying a PDF-local return-period
# gloss in _draw_dist_stats_page's definitional note, since the PDF has no
# adjacent web tooltip to anchor "1-in-T year" phrasing). services/reports.py
# re-exports this dict as a public alias so pdf_report.py can consume it via
# the allowed ``from idraa.services.reports import …`` pattern.
#
# Methodology rule (plan-gate reviewed): the plain-language half must
# faithfully render the statistic — return-period phrasing is exact for VaR
# quantiles (P(annual loss > x) = 1-q), "typical case" is the median, "average"
# is the mean. Never "expected loss" for the median, never "worst case" for VaR.
TAIL_LADDER_DISPLAY_LABELS: dict[str, str] = {
    "Mean": "Mean (average)",
    "Median": "Typical case (median)",
    "Std dev": "Std deviation",
    "VaR 90%": "1-in-10 year (VaR 90%)",
    "VaR 95%": "1-in-20 year (VaR 95%)",
    "VaR 99%": "1-in-100 year (VaR 99%)",
    "VaR 99.9%": "1-in-1000 year (VaR 99.9%)",
    "ES 95%": "Expected shortfall (95%)",
    "ES 99%": "Expected shortfall (99%)",
    "ES 99.9%": "Expected shortfall (99.9%)",
}

# ---- Issue #413 / Task 5 (#419): control-weight provenance disclaimer — single source of truth ----
#
# Defined here (alongside the other shared note constants) to avoid a circular
# import and so all three surfaces — web (Jinja global), PDF renderer
# (pdf_report.py) and Excel verification workbook (verification_workbook.py) —
# share one byte-identical string.  services/reports.py re-exports it as a
# public alias.
#
# Provenance anchor (NOT new copy semantics): every weight in fair_cam's
# GROUP_NODE_MAPPING is tagged ``weights_provenance="implementation-calibration"``
# (see fair_cam/models/composition_topology.py) and the FAIR Standard gives only
# qualitative relationships — so any control-value / risk-reduction dollar figure
# rests on composition weights that are implementation-calibrated, not
# Standard-grounded.  Surface this wherever those dollars render.
#
# Task 5 (#419): robustness-framing disclosure. Plain-English rewrite (2026-06):
# "modeled estimates shown as ranges" + a "too close to call" caveat; the
# technical detail lives in the /help/control-value-robustness article.
# All three surfaces receive the same byte-identical string — do NOT re-word
# per-surface (drift would fork the disclosure across web/PDF/Excel). Kept
# apostrophe/quote-free so it substring-matches under Jinja autoescape.
#
# BASE variant (M4 fix): first sentence only — used on surfaces where
# weight_robustness is absent so the "too close to call" caveat doesn't
# reference flags that don't exist on the run.
CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE: str = (
    "These are modeled estimates shown as ranges, not measured or guaranteed loss reductions."
)

# Full variant: BASE + the too-close-to-call caveat.  Only render on
# surfaces where weight_robustness data (and thus indistinguishable_pairs)
# is actually present (Spec-I1 / M4).
CONTROL_WEIGHT_PROVENANCE_DISCLAIMER: str = (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE
    + " Controls marked too close to call cannot be reliably ranked against each other."
)

# ---- Leave-one-out "if removed" legend (2026-07-03 methodology adjudication) ----
#
# The per-control value-RANGE table (both web surfaces + PDF) gains an "If
# removed" column next to "Value range". The two figures are deliberately
# different things — the range is a Shapley FAIR-SHARE allocation (cells sum
# to the total), "if removed" is a leave-one-out DROP-COST counterfactual
# (LOO_i = v(N) - v(N minus i)); they diverge on purpose for redundant and
# gating controls (see run_executor.py:_compute_loo_by_scenario docstring).
# Analysts reading only the range column could otherwise mistake "my fair
# share of the reduction" for "what I'd lose by removing this control" — the
# legend below heads that off. Appended (not merged into) the base disclaimer
# constants above because ONLY the per-control value-range table surfaces
# carry the "If removed" column; other CONTROL_WEIGHT_PROVENANCE_DISCLAIMER
# call sites (headline explainer boxes, the attribution-matrix intro) do not
# and must not gain this sentence.
IF_REMOVED_LEGEND: str = (
    "The value range is each control's fair share of the combined reduction "
    "— overlaps and synergies are split evenly, so it is not the cost of "
    "removing the control. 'If removed' is that cost: the increase in "
    "modeled annual loss if this control were dropped from the current "
    "portfolio."
)

# Per-control value-range table variant: full disclaimer + the if-removed
# legend, byte-identical across web (SINGLE + AGGREGATE) and PDF.
CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED: str = (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER + " " + IF_REMOVED_LEGEND
)

# ---- Mean-basis pairing note (2026-07-04 mean+typical side-by-side) ----
#
# The fair-share ("value range") and drop-cost ("if removed") legend sentences
# above are UNCHANGED (byte-identical, kept verbatim). This is a SEPARATE
# trailing sentence, appended (never merged in) by callers ONLY when the
# run's weight_robustness blob is basis=="mean" (every run executed after the
# mean-basis chain landed) — legacy (typical-basis) runs render the
# disclaimer exactly as before, with no reference to a pairing that doesn't
# exist on that run's data.
MEAN_BASIS_PAIRING_NOTE: str = (
    "The value range and if-removed figures above are on the same average "
    "(mean) basis as the headline figures, so they are directly comparable. "
    "The smaller typical-case figures shown alongside them are the "
    "typical-case counterpart — a median-like central case, not a second "
    "measurement of the same thing."
)


def strip_samples(risk_dict: dict[str, Any]) -> dict[str, Any]:
    """Drop the raw sample array from a base/residual risk dict.

    Keeps Jinja context size bounded — sample arrays are 1000+ floats per
    side. Templates only need percentiles + LEC summaries, not raw samples.
    """
    return {k: v for k, v in risk_dict.items() if k != "simulation_results"}


def has_ci_band(ci: dict[str, Any]) -> bool:
    """Detect a real empirical percentile band vs a legacy / degenerate row.

    Issue #202: the displayed band is now the empirical central-95% percentile
    interval (p2.5/p97.5), persisted with an ``interval_pct`` key. LEGACY runs
    (persisted before #202) carry the retired Gaussian SE-of-the-mean geometry
    under ``lower_bound``/``upper_bound`` but have NO ``interval_pct`` key — they
    are SUPPRESSED here (return False) rather than relabeled, because that
    narrow SE band is NOT a 2.5/97.5 percentile span; relabeling it "95%
    interval" would re-introduce the exact overclaim #202 removes.

    Real band: ``interval_pct`` present AND ``upper_bound > lower_bound``
    (strictly). Missing ``interval_pct`` (legacy), default-zeros, and degenerate
    equal-bound cases all return False, falling back to the "not available for
    legacy runs" hint in the headline macro.
    """
    if "interval_pct" not in ci:
        return False
    upper = ci.get("upper_bound", 0.0)
    lower = ci.get("lower_bound", 0.0)
    if isinstance(upper, (int, float)) and isinstance(lower, (int, float)):
        return upper > lower
    return False


# ---- Task 10: ES Monte Carlo standard error -> 95% MC interval ------------
#
# Task 9 additively persisted ``expected_shortfall_se`` (a sibling of
# ``expected_shortfall``, same {es_95, es_99, es_999} shape, float | None) on
# each risk dict. This surfaces it as a 95% two-sided normal interval
# (``es +/- 1.96*se``) so a consumer can tell a converged deep-tail ES from a
# noisy one. Named constant (not a scattered magic literal) per Spec-B1.
ES_CI_Z_95: float = 1.96  # two-sided 95% normal z-score (P(|Z|<z) = 0.95)

# Canonical ES levels — single source of truth for the three loop sites below.
_ES_LEVELS: tuple[str, ...] = ("es_95", "es_99", "es_999")


def _es_ci_fields(risk_dict: dict[str, Any], level: str) -> dict[str, Any]:
    """Derive the ``{se, ci_half, ci_insufficient}`` triple for one ES level.

    Three distinct cases (Task 10 / Spec-B1 — do not conflate them):

    1. ``expected_shortfall_se`` ABSENT from ``risk_dict`` entirely (legacy row,
       persisted before Task 9) -> ``{se: None, ci_half: None,
       ci_insufficient: False}``. The template renders a bare ES with no
       annotation — there is no SE to report, and it is not "insufficient",
       it simply predates the feature.
    2. ``expected_shortfall_se`` dict present but this level's value is
       ``None`` (persisted as JSON null; the engine saw < 2 tail samples at
       this N, SE undefined) -> ``{se: None, ci_half: None,
       ci_insufficient: True}``. The template renders the "insufficient tail
       samples at this N" label.
    3. Dict present with a float value -> ``{se: <float>, ci_half:
       ES_CI_Z_95 * se, ci_insufficient: False}``. The template renders
       "95% MC interval +/- ci_half".

    ``se`` is expected to already be in the caller's target currency (the
    money-boundary conversion happens once, at ``_convert_risk_dict``,
    mirroring ``expected_shortfall`` — this function is currency-agnostic and
    just does the dimensionless *1.96 scaling on whatever units it is given).
    """
    se_dict = risk_dict.get("expected_shortfall_se")
    if se_dict is None:
        return {"se": None, "ci_half": None, "ci_insufficient": False}
    se_val = se_dict.get(level)
    if se_val is None:
        return {"se": None, "ci_half": None, "ci_insufficient": True}
    return {"se": se_val, "ci_half": ES_CI_Z_95 * se_val, "ci_insufficient": False}


def _build_tail_risk(risk_dict: dict[str, Any]) -> dict[str, Any]:
    """Pull tail-VaR + Expected Shortfall from a persisted risk dict.

    Pure ``.get()`` reads off any risk dict (base_risk or residual_risk).
    OLD runs persisted before #266 D1 lack ``var_90`` / ``var_999`` /
    ``expected_shortfall`` entirely — they surface as 0.0 here (the
    "not-available" sentinel the template already uses for absent CI bands),
    NOT a KeyError.

    Moved from run_view_model.py to _view_model_helpers.py (T2 of
    enterprise-pdf-reports epic, #351) so both run_view_model.py and
    services/reports.py can call the SAME helper for both the residual-side
    and base-side tail metrics without logic duplication (PA2-Arch-I2).

    Reliability caveat: ``var_999`` / ``es_999`` are statistically reliable
    only as the iteration count approaches the 100k server cap. At the 10k
    form default the p99.9 tail holds only ~10 samples, so the deepest level
    is indicative.

    Task 10 (Spec-B1): also surfaces, per ES level, ``<level>_se`` (float |
    None), ``<level>_ci_half`` (``ES_CI_Z_95 * se``, float | None), and
    ``<level>_ci_insufficient`` (bool) — see ``_es_ci_fields`` for the
    absent-vs-None-value distinction. These three are ADDITIVE keys; the
    original ``has_tail_metrics`` degenerate-check below only reads the
    original 7 core keys, never these, so they must never be included in a
    blanket ``tail.values()`` scan.
    """
    es = risk_dict.get("expected_shortfall") or {}
    out: dict[str, Any] = {
        "var_90": risk_dict.get("var_90", 0.0),
        "var_95": risk_dict.get("var_95", 0.0),
        "var_99": risk_dict.get("var_99", 0.0),
        "var_999": risk_dict.get("var_999", 0.0),
        "es_95": es.get("es_95", 0.0),
        "es_99": es.get("es_99", 0.0),
        "es_999": es.get("es_999", 0.0),
    }
    for level in _ES_LEVELS:
        fields = _es_ci_fields(risk_dict, level)
        out[f"{level}_se"] = fields["se"]
        out[f"{level}_ci_half"] = fields["ci_half"]
        out[f"{level}_ci_insufficient"] = fields["ci_insufficient"]
    return out


# The original 7 tail-metric keys _build_tail_risk always populated before
# Task 10 — used by has_tail_metrics's degenerate-check below so the Task 10
# additive keys (which are legitimately None / False on many real runs) can
# never be misread as "all zero" / "non-degenerate" signal.
_CORE_TAIL_KEYS: tuple[str, ...] = (
    "var_90",
    "var_95",
    "var_99",
    "var_999",
    "es_95",
    "es_99",
    "es_999",
)


def build_dist_stats_rows(base: dict[str, Any], residual: dict[str, Any]) -> dict[str, Any]:
    """Build the 10-row distribution-stats ladder for the run-detail page (#353).

    Returns a dict:
        {
            "rows": [
                {"label": str, "base": float, "residual": float,
                 "delta": float, "gated": bool},
                ...
            ],
            "has_tail": bool,  # has_tail_metrics(base) AND has_tail_metrics(residual)
        }

    Row order: Mean, Median, Std dev, VaR 90%, VaR 95%, VaR 99%, VaR 99.9%,
               ES 95%, ES 99%, ES 99.9%.

    ``delta = base - residual`` (positive = risk reduced = green in the template).

    Gated rows (VaR 90%, VaR 99.9%, ES 95%, ES 99%, ES 99.9%) are OMITTED from
    ``rows`` entirely when ``has_tail`` is False — the builder, not the template,
    owns the honesty gate. This mirrors the suppress-not-relabel convention used
    by ``has_ci_band`` and ``has_tail_metrics`` throughout this module.

    Mixed-side safety: if EITHER side lacks tail metrics (one legacy, one
    modern) ``has_tail`` is False and the gated rows are suppressed — never
    render a table with one fabricated side.

    Task 10 (Spec-B1): the three ES rows additionally carry, per side,
    ``<side>_se`` / ``<side>_ci_half`` / ``<side>_ci_insufficient`` (see
    ``_es_ci_fields``) so the template can render "95% MC interval +/-
    ci_half", the insufficient-tail-samples label, or nothing (legacy row —
    bare ES), without a label-based lookup. Non-ES rows carry the same three
    keys defaulted to ``{None, None, False}`` so every row has a uniform
    schema; the template's rendering decision is driven purely by these
    values, never by matching row.label against an ES-row list.
    """
    has_tail = has_tail_metrics(base) and has_tail_metrics(residual)

    base_es = base.get("expected_shortfall") or {}
    residual_es = residual.get("expected_shortfall") or {}

    no_ci: dict[str, Any] = {"se": None, "ci_half": None, "ci_insufficient": False}

    def _row(
        label: str,
        bv: float,
        rv: float,
        *,
        gated: bool,
        base_ci: dict[str, Any] | None = None,
        residual_ci: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        b_ci = base_ci or no_ci
        r_ci = residual_ci or no_ci
        return {
            "label": label,
            "base": bv,
            "residual": rv,
            "delta": bv - rv,
            "gated": gated,
            "base_se": b_ci["se"],
            "base_ci_half": b_ci["ci_half"],
            "base_ci_insufficient": b_ci["ci_insufficient"],
            "residual_se": r_ci["se"],
            "residual_ci_half": r_ci["ci_half"],
            "residual_ci_insufficient": r_ci["ci_insufficient"],
        }

    labels = TAIL_LADDER_LABELS  # local alias for brevity

    # Always-present core rows (non-gated)
    rows: list[dict[str, Any]] = [
        _row(labels["mean"], base.get("mean", 0.0), residual.get("mean", 0.0), gated=False),
        _row(labels["median"], base.get("median", 0.0), residual.get("median", 0.0), gated=False),
        _row(
            labels["std_dev"],
            base.get("std_deviation", 0.0),
            residual.get("std_deviation", 0.0),
            gated=False,
        ),
        _row(labels["var_95"], base.get("var_95", 0.0), residual.get("var_95", 0.0), gated=False),
        _row(labels["var_99"], base.get("var_99", 0.0), residual.get("var_99", 0.0), gated=False),
    ]

    if has_tail:
        # Insert VaR 90% BEFORE VaR 95% — rebuild rows in ladder order
        rows = [
            _row(labels["mean"], base.get("mean", 0.0), residual.get("mean", 0.0), gated=False),
            _row(
                labels["median"], base.get("median", 0.0), residual.get("median", 0.0), gated=False
            ),
            _row(
                labels["std_dev"],
                base.get("std_deviation", 0.0),
                residual.get("std_deviation", 0.0),
                gated=False,
            ),
            _row(
                labels["var_90"], base.get("var_90", 0.0), residual.get("var_90", 0.0), gated=True
            ),
            _row(
                labels["var_95"], base.get("var_95", 0.0), residual.get("var_95", 0.0), gated=False
            ),
            _row(
                labels["var_99"], base.get("var_99", 0.0), residual.get("var_99", 0.0), gated=False
            ),
            _row(
                labels["var_999"],
                base.get("var_999", 0.0),
                residual.get("var_999", 0.0),
                gated=True,
            ),
            _row(
                labels["es_95"],
                base_es.get("es_95", 0.0),
                residual_es.get("es_95", 0.0),
                gated=True,
                base_ci=_es_ci_fields(base, "es_95"),
                residual_ci=_es_ci_fields(residual, "es_95"),
            ),
            _row(
                labels["es_99"],
                base_es.get("es_99", 0.0),
                residual_es.get("es_99", 0.0),
                gated=True,
                base_ci=_es_ci_fields(base, "es_99"),
                residual_ci=_es_ci_fields(residual, "es_99"),
            ),
            _row(
                labels["es_999"],
                base_es.get("es_999", 0.0),
                residual_es.get("es_999", 0.0),
                gated=True,
                base_ci=_es_ci_fields(base, "es_999"),
                residual_ci=_es_ci_fields(residual, "es_999"),
            ),
        ]

    return {"rows": rows, "has_tail": has_tail}


def has_tail_metrics(risk_dict: dict[str, Any]) -> bool:
    """Detect whether a persisted risk dict carries real tail metrics.

    Mirrors ``has_ci_band`` for the tail-metric gate (PS-I3 / T2(c)).
    Returns True only when the persisted tail keys are ALL present AND at
    least one is non-zero (non-degenerate). Returns False for:
    - dicts lacking ``var_90`` (runs persisted before #266 D1)
    - dicts with all-zero tail values (degenerate empty-sample fallback)
    - dicts lacking ``expected_shortfall`` (incomplete tail-metrics block)

    The ``has_tail_risk`` boolean in ``RunReportData`` is set from this
    helper; the renderer gates the tail-risk section on it (suppress-not-
    relabel, mirroring #202's CI-band gate).
    """
    # All four VaR keys must be present
    for key in ("var_90", "var_95", "var_99", "var_999"):
        if key not in risk_dict:
            return False
    # expected_shortfall dict must be present and non-empty
    es = risk_dict.get("expected_shortfall")
    if not isinstance(es, dict) or not es:
        return False
    # At least one tail value must be non-zero (non-degenerate). Scoped to the
    # original 7 core keys only — Task 10's additive es_*_se/ci_half/
    # ci_insufficient keys are legitimately None/False on real runs and must
    # never feed this scan (a None here would make `v != 0.0` spuriously True).
    tail = _build_tail_risk(risk_dict)
    return any(tail[k] != 0.0 for k in _CORE_TAIL_KEYS)


# ---- Task 5 (#419): weight-robustness display helpers ----


def control_value_range(cell: dict[str, Any], code: str = "USD") -> str:
    """Format a per-control or headline weight-robustness cell as a range string.

    Reads ``reduction_p5/p50/p95`` from ``cell`` (the pinned §4 contract keys).
    Values must already be converted to the reporting currency by the caller
    (convert-once invariant).

    Returns:
        ``"$120k  [$80k-$190k]"`` (compact form, center + [p5-p95]) when all
        three percentiles are present (the bracket separator is a unicode EN DASH
        in the actual output).
        ``"$120k"`` (point-only, compact) when p5/p95 are absent.
        ``"—"`` when p50 is absent.

    Uses ``safe_money_format`` (compact=True) — the canonical currency formatter —
    so currency symbol and compact units (k / M) are always CLDR-correct.
    NOT a fabricated format_compact (plan-gate constraint).
    """
    from idraa.formatting import safe_money_format  # canonical money formatter (avoid circular)

    p50 = cell.get("reduction_p50")
    if p50 is None:
        return "—"
    p5 = cell.get("reduction_p5")
    p95 = cell.get("reduction_p95")
    if p5 is None or p95 is None:
        return safe_money_format(p50, code, compact=True)
    return (
        f"{safe_money_format(p50, code, compact=True)}"
        f"  [{safe_money_format(p5, code, compact=True)}"
        f"–{safe_money_format(p95, code, compact=True)}]"  # noqa: RUF001 — EN DASH range separator
    )


def stability_badge(cell: dict[str, Any], *, in_indistinguishable_pair: bool = False) -> str:
    """Return a human-readable stability label for a per-control cell.

    The ``"indistinguishable"`` / "too close to call" marker is NEVER emitted
    here — it is driven solely from ``weight_robustness.
    indistinguishable_control_ids`` (the pair-set, Spec-I1 / M3), which every
    consumer (web ledger, PDF) checks BEFORE falling back to this badge.

    Maps ``stability_class`` values to plain-English display strings:
    - ``"not_assessed"``   → ``"not assessed"`` (insufficient K or deterministic envelope)
    - ``"not_applicable"`` → ``"not assessed"`` (SINGLE-run path, stability not computed)
    - ``"unstable"``, in a pair → ``"stable"`` (the pair-set marker supersedes
      this badge in every consumer, so the value is never displayed; "stable"
      keeps the fallback from double-reporting "indistinguishable")
    - ``"unstable"``, NOT in any pair → ``"rank sensitive"`` (#421 item 2: a
      control whose ±1-rank hold fraction is below threshold but whose
      instability is spread thinly across MANY pairs — no single pair reaches
      the flip threshold — previously badged "stable", overclaiming. "rank
      sensitive" states the observed per-control fact without claiming a
      pairwise equivalence the pair-set did not find. v3 view-model display
      derivation, not FAIR-grounded.)
    - ``"stable"`` / None / missing → ``"stable"`` (safe default)

    #454 item 6: reworded "not checked" → "not assessed" (the SINGLE-run
    template pairs it with a title= tooltip explaining rank stability is a
    multi-scenario measure). Display strings only; ``stability_class`` values
    are unchanged.
    """
    cls = cell.get("stability_class")
    if cls in ("not_assessed", "not_applicable"):
        return "not assessed"
    if cls == "unstable" and not in_indistinguishable_pair:
        return "rank sensitive"
    return "stable"


# ---- Leave-one-out "if removed" per-control lookups (display plumbing only) ----
#
# run_executor.py's _inject_loo writes ``if_removed_value`` into each
# control_adjustments dict (SINGLE: flat top-level list; AGGREGATE: per
# per_scenario[i].control_adjustments). Absent key = attribution unavailable
# for that (control[, scenario]) — NEVER coerced to 0.0 (same absent≠0.0
# convention as Shapley's ``shapley_value`` / aggregate_run_view_model._cell_value).
# These two functions build the control_id -> float|None lookup that
# ``process_weight_robustness_for_display`` merges into ``per_control`` cells.

_IF_REMOVED_ABSENT = object()  # distinguishes "no if_removed_value key" from a real 0.0


def if_removed_by_control_single(
    adjustments: list[dict[str, Any]], key: str = "if_removed_value"
) -> dict[str, float | None]:
    """SINGLE-run passthrough: control_id -> <key> (USD, raw), or
    None when the key is absent or explicitly null on that control's
    adjustment (legacy run, or the leave-one-out pass degraded/skipped this
    run's one scenario).

    ``key`` (2026-07-04 mean+typical side-by-side): defaults to the historical
    typical-basis ``"if_removed_value"``. Callers building the MEAN-basis
    primary figure pass ``key="if_removed_value_mean"``; the typical-basis
    call (secondary/paired figure) uses the default. Both keys are injected
    independently by run_executor.py's ``_inject_loo`` (absent≠0.0 for each).
    """
    out: dict[str, float | None] = {}
    for adj in adjustments:
        cid = adj.get("control_id")
        if cid is None:
            continue
        raw = adj.get(key, _IF_REMOVED_ABSENT)
        out[cid] = None if raw is _IF_REMOVED_ABSENT or raw is None else float(raw)
    return out


def if_removed_by_control_aggregate(
    per_scenario: list[dict[str, Any]],
    key: str = "if_removed_value",
) -> tuple[dict[str, float | None], set[str]]:
    """AGGREGATE: (control_id -> SUM of <key> (USD, raw) across every
    scenario that carries the key for that control, set of PARTIAL control_ids).

    ``key`` (2026-07-04 mean+typical side-by-side): defaults to the historical
    typical-basis ``"if_removed_value"``. Callers building the MEAN-basis
    primary figure pass ``key="if_removed_value_mean"``.

    Linearity of expectation makes the sum exact: each scenario's LOO figure is
    the modeled increase in THAT scenario's mean annual loss if the control
    were dropped from it, and AGGREGATE ALE is the per-iteration sum of
    scenario losses — means add, so summing per-scenario LOO gives the
    portfolio-level drop-cost for that control.

    A control that never carries the key on ANY scenario it appears in
    (attribution unavailable everywhere) maps to None -> renders '—'. A
    control present on some scenarios and absent on others (e.g. one
    scenario's leave-one-out pass errored — the only reachable trigger, the
    eval budget being unreachable at linear cost) sums only the present
    scenarios (never a fabricated 0.0) AND lands in the returned partial set,
    so the display can mark the figure as covering only part of the portfolio
    (LOO-Meth-3): an unmarked understated drop-cost would bias exactly the
    remove-this-control decision the column informs. The marker is "(partial)",
    deliberately NOT "≥": a missing scenario's LOO can be negative (the
    weak-AND dilution case, see _compute_loo_by_scenario), so the partial sum
    is not a lower bound.
    """
    sums: dict[str, float] = {}
    present: set[str] = set()
    seen: dict[str, None] = {}
    missing: set[str] = set()
    for ps in per_scenario:
        for adj in ps.get("control_adjustments", []) or []:
            cid = adj.get("control_id")
            if cid is None:
                continue
            seen.setdefault(cid, None)
            raw = adj.get(key, _IF_REMOVED_ABSENT)
            if raw is not _IF_REMOVED_ABSENT and raw is not None:
                sums[cid] = sums.get(cid, 0.0) + float(raw)
                present.add(cid)
            else:
                missing.add(cid)
    lookup = {cid: (sums[cid] if cid in present else None) for cid in seen}
    return lookup, present & missing


def process_weight_robustness_for_display(
    wr: dict[str, Any] | None,
    convert: Callable[[float | None], float | None],
    code: str = "USD",
    sub_functions_by_id: dict[str, list[str]] | None = None,
    availability_effect: bool = False,
    if_removed_by_control: dict[str, float | None] | None = None,
    if_removed_partial_ids: set[str] | None = None,
    if_removed_by_control_typical: dict[str, float | None] | None = None,
) -> dict[str, Any] | None:
    """Convert USD weight_robustness blob to reporting currency + attach display strings.

    Used by both aggregate_run_view_model and run_view_model to expose
    weight_robustness in the template context (convert-once invariant: all
    money values converted here; templates and formatters only format).

    Args:
        wr: raw ``run.weight_robustness`` dict (USD) or None (no robustness data).
        convert: ``rc.convert`` from the caller's ReportingCurrency (USD→reporting).
        code: ISO-4217 code of the reporting currency (for money formatting).
        sub_functions_by_id: optional mapping of control_id → list of sub-function
            slug strings (from the run's controls_snapshot). When provided and a
            cell's reduction_p50 is ~$0 (< $1 USD pre-conversion), the classifier
            ``control_zero_value_reason`` is called and its output is attached as
            ``cell["zero_reason"]`` (str or None). Callers build this via
            ``snapshot_sub_functions_by_id(run.controls_snapshot)``.
        availability_effect: when True (single-run availability scenario), the
            "No detection partner" label (Rule 4) is suppressed inside
            ``control_zero_value_reason`` — availability events self-detect
            (FAIR-CAM §3.3.2 p.19) so the recovery benefit IS creditable.
            The AGGREGATE view-model always passes the default False (mixed-effect
            reconciliation deferred per the Slice-1 spec Open Questions); the
            value-gate (abs(raw_p50) < $1) is the aggregate stale-label guard:
            a control that actually scores is never routed to the classifier.
        if_removed_by_control: optional control_id -> raw USD if_removed_value
            (or None) lookup, built by the caller via
            ``if_removed_by_control_single`` (SINGLE) or
            ``if_removed_by_control_aggregate`` (AGGREGATE). When a cid has no
            entry in this dict, or the dict is None entirely (legacy caller /
            no LOO data), the cell's ``if_removed`` is None (renders '—').
            Converted with the same ``convert`` callable as the other money
            fields (convert-once invariant).
        if_removed_partial_ids: optional set of control_ids whose aggregate
            "if removed" sum covers only PART of the scenarios the control
            appears on (second element of ``if_removed_by_control_aggregate``;
            SINGLE callers omit it — one scenario is never partial). Cells for
            these ids get ``if_removed_partial=True`` so templates/PDF append
            the "(partial)" marker (LOO-Meth-3). Applies to the PRIMARY
            ``if_removed`` figure only.
        if_removed_by_control_typical: optional SECONDARY control_id -> raw USD
            "if removed" lookup (2026-07-04 mean+typical side-by-side), paired
            alongside the primary figure for display as a muted sub-line. Callers
            pass this ONLY when the primary lookup is mean-basis (i.e. ``wr``'s
            ``basis`` key is ``"mean"``) — legacy/typical-basis callers omit it so
            legacy runs render with no secondary figure (unchanged today's shape).
            None entries render as no sub-line, same absent≠0.0 convention.

    Returns:
        Processed dict with converted money fields and pre-formatted display
        strings, or None when wr is None.

    Keys on the returned dict (all others from wr pass through):
        ``basis``: ``"mean"`` | ``"typical"`` — the statistic chain of every
            dollar figure in this blob (``wr.get("basis")``, defaulting to
            ``"typical"`` for legacy blobs persisted before the mean-basis chain
            landed, per run_executor.py's ``_build_weight_robustness``).
        ``headline``: converted headline dict (reduction_p5/p50/p95).
        ``headline_range_str``: pre-formatted range string via control_value_range.
        ``per_control``: {cid: {...converted cell..., "range_str": str, "badge": str,
            "zero_reason": str | None, "typical_value": float | None,
            "if_removed": float | None, "if_removed_typical": float | None,
            "if_removed_partial": bool}}.
            ``typical_value``: the paired typical-case canonical point (from
            ``wr["canonical_value_typical"]``, converted); None on legacy blobs
            (empty dict) or when this control has no typical pass entry.
            ``if_removed_typical``: the paired typical-case "if removed" figure
            from ``if_removed_by_control_typical``; None when that lookup is not
            supplied (legacy/typical-basis callers) or the cid is absent from it.
        ``indistinguishable_control_ids``: list[str] of all cids appearing in any pair.
    """
    if wr is None:
        return None

    result: dict[str, Any] = dict(wr)  # shallow copy; deep structures re-assigned below
    # Basis marker (2026-07-04): normalize the missing key (legacy blob) to the
    # explicit "typical" default so templates/PDF never branch on a missing key.
    result["basis"] = wr.get("basis", "typical")

    # --- Convert headline percentiles ---
    headline_raw = wr.get("headline") or {}
    converted_headline: dict[str, Any] = {}
    for k in ("reduction_p5", "reduction_p50", "reduction_p95"):
        v = headline_raw.get(k)
        if v is not None:
            converted_v = convert(float(v))
            converted_headline[k] = converted_v if converted_v is not None else 0.0
        else:
            converted_headline[k] = None
    result["headline"] = converted_headline
    result["headline_range_str"] = control_value_range(converted_headline, code)

    # --- Convert per_control percentiles + attach display strings ---
    per_control_raw = wr.get("per_control") or {}
    # Paired typical-basis canonical point per control (2026-07-04 side-by-side).
    # None-safe: {} on legacy blobs (persisted before the dual-canonical-pass
    # commit) or when the caller's typical pass produced nothing for a cid.
    typical_canonical: dict[str, Any] = wr.get("canonical_value_typical") or {}
    # Pair-set membership FIRST (moved above the cell loop for #421 item 2):
    # the badge needs to know whether a control is captured by any
    # indistinguishable pair. Driven from indistinguishable_pairs (Spec-I1),
    # NOT from per-control stability_class.
    pairs = wr.get("indistinguishable_pairs") or []
    indistinguishable_ids: list[str] = []
    for pair in pairs:
        if isinstance(pair, (list, tuple)):
            for _pcid in pair:
                if isinstance(_pcid, str) and _pcid not in indistinguishable_ids:
                    indistinguishable_ids.append(_pcid)

    per_control_out: dict[str, Any] = {}
    for cid, cell in per_control_raw.items():
        converted_cell: dict[str, Any] = dict(cell)
        for k in ("reduction_p5", "reduction_p50", "reduction_p95"):
            v = cell.get(k)
            if v is not None:
                cv = convert(float(v))
                converted_cell[k] = cv if cv is not None else 0.0
            else:
                converted_cell[k] = None
        converted_cell["range_str"] = control_value_range(converted_cell, code)
        converted_cell["badge"] = stability_badge(
            cell, in_indistinguishable_pair=cid in indistinguishable_ids
        )  # stability_class is non-monetary, no convert
        # Issue #436: attach zero_reason when p50 is ~$0 and sub-function data available.
        # Use the RAW (pre-conversion) p50 for the structural-zero threshold test so the
        # check is currency-agnostic (threshold is $1 USD regardless of reporting currency).
        raw_p50 = cell.get("reduction_p50")
        if (
            sub_functions_by_id is not None
            and raw_p50 is not None
            and abs(float(raw_p50)) < _ZERO_THRESHOLD
        ):
            sfs = sub_functions_by_id.get(cid, [])
            # #439 Slice-2: a meta control credits ONLY by uplifting a co-present
            # Loss-Event control's reliability, so the classifier needs to know
            # whether ANOTHER control on this run carries an lec_* channel. The
            # classifier sees only this control's own slugs (Spec-I2), so compute
            # the partner signal here from the run's other controls.
            has_co_present_lec = _any_other_control_has_lec(cid, sub_functions_by_id)
            converted_cell["zero_reason"] = control_zero_value_reason(
                sfs,
                availability_effect=availability_effect,
                has_co_present_lec=has_co_present_lec,
            )
        else:
            converted_cell["zero_reason"] = None
        # Paired typical-case point (2026-07-04 side-by-side): None-safe convert,
        # same convert-once invariant as every other money field on this cell.
        _typ_raw = typical_canonical.get(cid)
        converted_cell["typical_value"] = convert(float(_typ_raw)) if _typ_raw is not None else None
        # Leave-one-out "if removed" (display plumbing only, 2026-07-03): absent
        # lookup or absent/None entry -> None -> template/PDF render '—', never
        # a fabricated $0 (same absent≠0.0 convention as zero_reason/shapley_value).
        _ir_raw = if_removed_by_control.get(cid) if if_removed_by_control is not None else None
        converted_cell["if_removed"] = convert(float(_ir_raw)) if _ir_raw is not None else None
        converted_cell["if_removed_partial"] = (
            _ir_raw is not None
            and if_removed_partial_ids is not None
            and cid in if_removed_partial_ids
        )
        # Paired typical-case "if removed" (2026-07-04 side-by-side): only
        # populated when the caller supplies a secondary lookup (mean-basis
        # primary callers); absent lookup or absent/None cid -> None.
        _ir_typ_raw = (
            if_removed_by_control_typical.get(cid)
            if if_removed_by_control_typical is not None
            else None
        )
        converted_cell["if_removed_typical"] = (
            convert(float(_ir_typ_raw)) if _ir_typ_raw is not None else None
        )
        per_control_out[cid] = converted_cell
    result["per_control"] = per_control_out

    # indistinguishable_control_ids: computed above (hoisted for the badge).
    result["indistinguishable_control_ids"] = indistinguishable_ids

    return result


# ---- Issue #436 / #439 Slice-2: structural-zero reason labels ----
#
# When a control's modeled value is ~$0 the template must explain WHY instead
# of silently showing "$0 [$0-$0]". The classifier maps the control's
# sub-function set to an honest label. It only fires for structural zeros —
# controls whose FAIR-CAM topology cannot produce a loss-event reduction at
# this run's control mix — NOT for genuine small-but-nonzero values
# (overlap-dominated controls that do score standalone).
#
# Sub-function categories (derived from fair_cam composition topology, post-D1):
#   Standalone scorers (return None — real small value, not structural zero):
#     lec_prev_avoidance, lec_prev_deterrence, lec_prev_resistance (OR-trio)
#     lec_resp_loss_reduction (currency subtractor)
#
#   Meta — credits ONLY via the κ reliability coupling on a co-present LEC control
#   (#439 D1: VMC/DSC families lost their direct FAIR-node targets on §2.2 p.5
#   "Indirectly Affect Risk" grounds; E_meta now uplifts co-present LEC reliability
#   r_eff = r0 + (1-r0)·κ·E_meta, §2.2 p.5 / §2.3 pp.5-6 / §4 p.21). A meta-only
#   control at ~$0 is one of two honest cases, keyed on ``has_co_present_lec``:
#     vmc_prev_* — variance-management prevention (OR leaf, empty targets)
#     vmc_id_*   — VMC identification (OR leaf, empty targets)
#     vmc_corr_* — VMC correction (AND leaf, empty targets)
#     dsc_*      — decision-support (weak-AND leaf, empty targets; labeled v3 proxy)
#
#   Non-scoring LEC gaps (structural zeros without a partner):
#     lec_det_*  — detection, empty targets (needs response partner)
#     lec_resp_* (except loss_reduction) — response, detection-gated
#
# THRESHOLD: abs(reduction_p50) < 1.0 (less than $1 USD pre-conversion).


_ZERO_THRESHOLD: float = 1.0  # USD; structural zeros are exactly 0.0 in practice

# Meta (variance-management / decision-support) reliability-coupling labels (#439 D1).
# A meta control credits value ONLY by uplifting a co-present Loss-Event control's
# reliability. So a meta control that rounds to ~$0 is honestly one of:
#   - no co-present LEC to uplift → the uplift has nothing to act on; or
#   - a co-present LEC exists but the uplift is too small to show at the display
#     threshold (the value IS modeled — it is just sub-threshold, NOT "not modeled").
_META_NO_PARTNER_REASON: str = (
    "Variance-management/decision-support control with no co-present loss-event "
    "control to strengthen — its reliability uplift has nothing to act on"
)
_META_SUBTHRESHOLD_REASON: str = (
    "Reliability uplift to co-present controls below the display threshold"
)


def control_zero_value_reason(
    sub_functions: list[str],
    *,
    availability_effect: bool = False,
    has_co_present_lec: bool = True,
) -> str | None:
    """Return a plain-English reason label when a control's modeled value is
    structurally zero, or None when the zero is genuine (overlap-dominated) or
    when the sub-function set cannot be classified.

    Args:
        sub_functions: list of raw sub-function slug strings for all assignments
            on this control (from the run's controls_snapshot). May be empty for
            V1 snapshots (no assignment data — caller passes []).
        availability_effect: when True, the scenario is an availability scenario
            and the "No detection partner" label (Rule 4) is suppressed — availability
            events self-detect (FAIR-CAM §3.3.2 p.19) so the recovery benefit IS
            creditable → None. Default False preserves all existing labels.
        has_co_present_lec: whether the run carries a co-present Loss-Event control
            (on ANOTHER control) that a meta control's κ reliability coupling could
            uplift. The classifier sees only this control's own slugs and CANNOT
            infer partner presence, so the caller supplies this signal (Spec-I2,
            mirroring ``availability_effect``). Default True is the fail-safe
            direction (assume a partner exists → "sub-threshold" copy rather than
            the stronger "nothing to strengthen" claim). Only consulted for a
            pure-meta control (Rule 3). Granularity note (T7 final-review
            adjudication): on AGGREGATE runs this signal is computed at RUN level
            (any co-present LEC control anywhere in the run), not per-scenario —
            a meta control alone on one scenario but with an LEC partner elsewhere
            in the run gets the sub-threshold copy rather than "nothing to
            strengthen". This is defensible (aggregate ranges ARE the display unit)
            but per-scenario granularity is a possible future refinement.

    Returns:
        A human-readable label string, or None when:
        - Any sub-function ``scores_standalone`` (real small value, not structural).
        - The sub-function list is empty (V1 snapshot — reason unknowable).
        - Rule 4 would fire but ``availability_effect`` is True (recovery creditable).

    Priority order (first matching rule wins):
        1. Any scorer → None (genuine nonzero contribution, even if near-zero).
        2. Empty list → None (legacy V1 snapshot; reason unknowable).
        3. Pure meta (all slugs VMC id/corr/prev or DSC; no LEC channel of its own)
           → reliability-coupling copy. ``has_co_present_lec`` picks the case:
           False → "…no co-present loss-event control to strengthen…";
           True  → "Reliability uplift to co-present controls below the display
           threshold". A HYBRID control (its own LEC channel present) is NOT pure
           meta and falls through to the LEC rules below — its own LEC channel is
           itself strengthenable.
        4. Has non-currency LEC Response but no LEC Detection **and not availability_effect**
           → "No detection partner…" (availability effects self-detect, FAIR-CAM §3.3.2
           p.19, so the response benefit IS creditable → None).
        4b. Has LEC Detection but no LEC Response → "No response partner…".
        5. Catch-all (lec_det+lec_resp together with no scorer, or other sparse
           non-scoring sets where a direct channel plausibly should exist) →
           "Incomplete — no direct loss-event channel assigned".

    Magnitude gate note: this classifier is only called when abs(reduction_p50)
    < $1 USD (``_ZERO_THRESHOLD``). A meta control whose κ coupling DOES move a
    co-present LEC materially produces v(S) ≫ $1 and is excluded by the magnitude
    gate BEFORE reaching this function; when it reaches here (``has_co_present_lec``
    True) the uplift is genuinely sub-threshold, which the Rule-3 copy states.

    Sub-function prefix taxonomy (Rules 3-6): the prefix checks below track the
    channel catalog defined in ``control_library_scoring`` and the rubric §2 topology
    tables. If issue #439 adds or renames a channel prefix (e.g. a new ``lec_corr_*``
    tier), update the prefix sets here to match.
    """
    from idraa.services.control_library_scoring import (
        scores_standalone,  # avoid circular at module level
    )

    # Rule 1: any standalone-scoring sub-function → real small value, not structural
    if any(scores_standalone(sf) for sf in sub_functions):
        return None

    # Rule 2: empty list → V1 snapshot (no assignment data), cannot classify
    if not sub_functions:
        return None

    sfs = set(sub_functions)

    # Sub-function sets by channel category (prefix-based taxonomy — see docstring).
    # Post-D1 every vmc_* family (id / corr / prev) is meta (empty node targets); the
    # prefix ``vmc_`` covers all three so a vmc_prev-only control is meta, not a
    # standalone scorer or a Rule-6 catch-all.
    vmc_meta = {sf for sf in sfs if sf.startswith("vmc_")}
    dsc_sfs = {sf for sf in sfs if sf.startswith("dsc_")}
    lec_resp_non_currency = {
        sf for sf in sfs if sf.startswith("lec_resp_") and sf != "lec_resp_loss_reduction"
    }
    lec_det = {sf for sf in sfs if sf.startswith("lec_det_")}

    # Rule 3: pure meta control (all slugs are VMC id/corr/prev or DSC — no LEC channel
    # of its own). Post-D1 meta value flows exclusively via the κ meta→reliability
    # coupling, so the honest reason depends on whether a co-present LEC exists to
    # uplift. A hybrid control (meta + its own LEC channel) is NOT ``sfs.issubset``
    # here and drops to the LEC rules below.
    meta_all = vmc_meta | dsc_sfs
    if meta_all and sfs.issubset(meta_all):
        if has_co_present_lec:
            return _META_SUBTHRESHOLD_REASON
        return _META_NO_PARTNER_REASON

    # Rule 4: has non-currency response sub-function(s) but no detection partner.
    # Suppressed for availability effects — the event self-detects (§3.3.2 p.19),
    # so the recovery benefit is creditable (Slice 1) and there is no structural
    # $0 to explain → return None (genuine small value). Stealth C/I keeps the
    # label (detection-gated, §3.3 p.18).
    if lec_resp_non_currency and not lec_det:
        if availability_effect:
            return None  # recovery creditable; not a structural gap
        return "No detection partner — response benefit not creditable"

    # Rule 4b: has detection sub-function(s) but no response partner (symmetric to Rule 4)
    if lec_det and not lec_resp_non_currency:
        return "No response partner — detection benefit not creditable"

    # Rule 5: catch-all for genuinely sparse/under-authored sets where a direct channel
    # plausibly should exist (e.g. lec_det + lec_resp together with no scorer, or
    # other unusual non-scoring combinations)
    return "Incomplete — no direct loss-event channel assigned"


def _any_other_control_has_lec(cid: str, sub_functions_by_id: dict[str, list[str]]) -> bool:
    """Return True iff some control OTHER than ``cid`` carries an lec_* sub-function.

    Feeds ``control_zero_value_reason(has_co_present_lec=...)`` (#439 D1): a meta
    control's κ reliability coupling only credits value when a co-present Loss-Event
    control exists to uplift. "co-present" = on a DIFFERENT control in the same run
    (the meta control's own LEC channel is handled by the classifier's hybrid path,
    not this signal). Any ``lec_`` prefix (prevention / detection / response) counts.
    """
    for other_cid, sfs in sub_functions_by_id.items():
        if other_cid == cid:
            continue
        if any(sf.startswith("lec_") for sf in sfs):
            return True
    return False


def snapshot_sub_functions_by_id(
    controls_snapshot: list[Any],
) -> dict[str, list[str]]:
    """Extract sub-function slugs keyed by control_id from a run's controls_snapshot.

    Supports V1 (no assignments key → empty list), V2, and V3 snapshot shapes.
    The returned dict maps str(control_id) → list[str] of sub-function slug values.

    Accepts ``list[Any]`` so callers can pass raw JSON from the ORM column (which
    may be a heterogeneous list) without a type error. Non-dict elements are skipped.

    Called at view-model build time by run_view_model and aggregate_run_view_model
    to provide sub-function data to process_weight_robustness_for_display without
    adding DB access to the view-model layer.
    """
    result: dict[str, list[str]] = {}
    for c in controls_snapshot:
        if not isinstance(c, dict):
            continue
        cid = c.get("control_id")
        if not cid:
            continue
        assignments = c.get("assignments") or []
        sfs: list[str] = []
        for a in assignments:
            if not isinstance(a, dict):
                continue
            sf = a.get("sub_function")
            if sf is not None:
                sfs.append(str(sf))
        result[str(cid)] = sfs
    return result
