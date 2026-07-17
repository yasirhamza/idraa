"""Caveat registry for the run-detail views (spec 2026-07-03 §Caveat registry).

Single source of caveat prose. Views render numbered superscript chips that
anchor into a consolidated panel; both are driven from this registry so a
caveat's wording can never fork between views (the failure mode that produced
seven scattered disclaimer paragraphs on the old page).

Methodology-approved strings that already exist as module constants are
EMBEDDED BY REFERENCE (f-string interpolation of the constant), never retyped.
tests/unit/test_run_caveats.py pins this by identity.
"""

from typing import Any

from idraa.services._view_model_helpers import (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER,
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE,
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED,
    DIST_STATS_DEFINITIONAL_NOTE,
    MEAN_BASIS_PAIRING_NOTE,
)

# Mean+typical side-by-side (2026-07-04): both bodies below asserted unqualified
# typical-basis claims about surfaces (weight_robustness ranges, Shapley
# attribution) that are now MEAN-basis on runs executed after the mean-basis
# chain landed. `active_run_caveats(basis=...)` selects LEGACY (byte-identical
# to today) vs MEAN below. The MEAN variant appends/substitutes honest text —
# it embeds MEAN_BASIS_PAIRING_NOTE BY REFERENCE (never paraphrased) for the
# same reason every other adjudicated disclaimer in this module is embedded by
# identity, not retyped (module docstring).
_MEAN_VS_TYPICAL_BODY_LEGACY: str = (
    "Typical-case figures use the median of the loss distribution, which "
    "sits below the average (mean) because a few rare, very large losses "
    "pull the average up. A wider gap means a more skewed loss profile, "
    "not a model error."
)
_MEAN_VS_TYPICAL_BODY_MEAN: str = _MEAN_VS_TYPICAL_BODY_LEGACY + " " + MEAN_BASIS_PAIRING_NOTE

_FAIR_SHARE_BODY_LEGACY: str = (
    "Attribution values are each control's fair share (Shapley) of the "
    "scenario's modeled risk reduction; they sum to the scenario total and "
    "to each control's column total. These are typical-case estimates, "
    "which for skewed losses run below the average (mean) control value "
    "shown in the summary above. Cells shown as — had no attribution "
    "computed (legacy or skipped scenarios) and are excluded from totals — "
    "a row/column total over — cells is a partial sum over the attributed "
    "controls only, not the scenario's full modeled reduction."
)
_FAIR_SHARE_BODY_MEAN: str = (
    "Attribution values are each control's fair share (Shapley) of the "
    "scenario's modeled risk reduction; they sum to the scenario total and "
    "to each control's column total. These are now on the same average "
    "(mean) basis as the control value shown in the summary above, so they "
    "are directly comparable to it — the smaller typical-case figures paired "
    "alongside them in the ledger show the skew. Cells shown as — had no "
    "attribution computed (legacy or skipped scenarios) and are excluded "
    "from totals — a row/column total over — cells is a partial sum over "
    "the attributed controls only, not the scenario's full modeled "
    "reduction."
)

# Ordered registry: key -> (title, static body or None when built per-call).
# Bodies are plain escaped text (never markup — do not add |safe downstream).
# ORDER IS LOAD-BEARING: it is the chip-numbering order and matches the Summary
# view's top-to-bottom render order (plan-gate Arch-N4/Spec-N3).
CAVEAT_REGISTRY: dict[str, dict[str, str | None]] = {
    "mean-vs-typical": {
        "title": "Mean vs typical case.",
        "body": None,  # built per-call: basis-switched (2026-07-04 mean+typical side-by-side)
    },
    "weight-provenance": {
        "title": "Ranges and rankings.",
        "body": None,  # built per-call: embeds the adjudicated disclaimer variant verbatim
    },
    "cost-dedup": {
        "title": "Cost dedup.",
        "body": (
            "Aggregate control cost counts each unique control once, even when it "
            "is shared by several scenarios."
        ),
    },
    "dist-note": {
        "title": "How the statistics are computed.",
        "body": None,  # built per-call: embeds DIST_STATS_DEFINITIONAL_NOTE verbatim
    },
    "independence": {
        "title": "Tail metrics are a lower bound.",
        "body": (
            "Aggregate VaR/ES assume the scenarios are independent (the aggregate "
            "loss is the per-iteration sum of independently-drawn scenario losses). "
            "If scenarios share a common cause (shared infrastructure, asset, "
            "control, or a single triggering event) they are positively correlated "
            "and the true aggregate tail is higher than shown — treat aggregate "
            "VaR/ES as a lower bound. The aggregate ALE (mean) is additive "
            "regardless."
        ),
    },
    "mc-interval": {
        "title": "What the “95% MC interval” means.",
        # Issue #508 (PR2 final-gate Meth-N1/N2): the ES ± interval is Monte
        # Carlo *sampling* error, not epistemic uncertainty in the loss. This
        # prose is methodology-honest — it must not imply the interval is a
        # range of possible losses or confidence in the loss itself.
        "body": (
            "The “95% MC interval” shown beside each Expected Shortfall (ES) is "
            "Monte Carlo sampling error: roughly how much this ES estimate would "
            "move if the simulation were re-run with a different random seed. It "
            "measures how well the simulation has converged — NOT uncertainty in "
            "the loss itself, and NOT a range of possible losses. It shrinks as "
            "the iteration count rises (about 1/√N). “Insufficient tail samples "
            "at this N” means the deep tail had too few simulated losses to "
            "estimate the interval; raise the iteration count for a stable figure."
        ),
    },
    "fair-share": {
        "title": "Fair-share attribution.",
        "body": None,  # built per-call: basis-switched (2026-07-04 mean+typical side-by-side)
    },
    "if-removed-partial": {
        "title": "“If removed” and partial sums.",
        "body": (
            "“If removed” is the leave-one-out value: the increase in modeled "
            "annual loss if the control were absent. “(partial)” marks controls "
            "where “if removed” could not be computed on some scenarios that "
            "include the control; the figure sums only the scenarios where it "
            "could."
        ),
    },
    "structural-zeros": {
        "title": "Reason labels instead of dollars.",
        # Shipped #452 semantics (plan-gate Meth-B1/I3): metas ARE Shapley
        # players; a reason label means the computed attribution was below the
        # $1 display threshold or structurally absent — the label states which.
        "body": (
            "When a control's fair-share cell shows a reason label instead of a "
            "dollar figure, its computed attribution was below the display "
            "threshold or structurally absent, and the label states why. "
            "Variance-management and decision-support controls act by "
            "strengthening the reliability of co-present loss-event controls; "
            "when that coupling moves losses materially it flows into the "
            "control's own fair share, and when it is below the threshold the "
            "label says so. Other labels mark controls missing a detection or "
            "response partner, or with no direct loss-event channel assigned."
        ),
    },
}


def active_run_caveats(
    *,
    has_cost: bool,
    has_ranges: bool,
    has_zero_reasons: bool,
    has_tail: bool,
    wr_present: bool,
    has_if_removed: bool,
    has_attribution: bool,
    basis: str = "typical",
) -> dict[str, Any]:
    """Filter + number the registry for one render.

    Numbering is compact (1..N over active entries) and identical for chips and
    panel because both consume this one result. Flags:
      has_attribution — matrix present, non-unavailable, controls non-empty;
        gates every attribution caveat (Meth-I6/Arch-I5).
      has_zero_reasons — at least one weight_robustness.per_control cell
        carries a zero_reason (any reason class — not meta-specific, Meth-I3).
      has_if_removed — the ledger's if-removed column renders; forces the
        _WITH_IF_REMOVED disclaimer variant (Meth-B2 adjudication).
      basis — "mean" | "typical" (2026-07-04 mean+typical side-by-side): the
        run's weight_robustness/matrix basis. Selects the LEGACY (byte-identical
        to today) vs MEAN wording for the "mean-vs-typical" and "fair-share"
        bodies — those two are the only caveats whose prose asserts a
        typical-basis claim about the underlying figures. Default "typical"
        preserves today's behavior for callers that don't yet thread a run's
        basis (all existing call sites keep rendering byte-identical text).
    """
    active: list[str] = []
    for key in CAVEAT_REGISTRY:
        if key == "cost-dedup" and not has_cost:
            continue
        if key == "independence" and not has_tail:
            continue
        if key == "mc-interval" and not has_tail:
            continue
        if key == "fair-share" and not has_attribution:
            continue
        if key == "weight-provenance" and not (has_attribution or has_ranges):
            continue
        if key == "if-removed-partial" and not has_if_removed:
            continue
        if key == "structural-zeros" and not (has_zero_reasons and has_attribution):
            continue
        active.append(key)

    entries: list[dict[str, Any]] = []
    for n, key in enumerate(active, start=1):
        spec = CAVEAT_REGISTRY[key]
        body = spec["body"]
        if key == "weight-provenance":
            # Adjudicated variant selection (Meth-B2/Spec-I2): the if-removed
            # column REQUIRES the legend variant; otherwise wr-present gets the
            # full disclaimer, legacy runs the base one. All three constants
            # are embedded by identity — never paraphrased.
            if has_if_removed:
                disclaimer = CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED
            elif wr_present:
                disclaimer = CONTROL_WEIGHT_PROVENANCE_DISCLAIMER
            else:
                disclaimer = CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE
            range_sentence = " Ranges show p5-p95 under weight perturbation." if has_ranges else ""
            body = f"{disclaimer}{range_sentence}"
        elif key == "dist-note":
            body = DIST_STATS_DEFINITIONAL_NOTE
        elif key == "mean-vs-typical":
            body = _MEAN_VS_TYPICAL_BODY_MEAN if basis == "mean" else _MEAN_VS_TYPICAL_BODY_LEGACY
        elif key == "fair-share":
            body = _FAIR_SHARE_BODY_MEAN if basis == "mean" else _FAIR_SHARE_BODY_LEGACY
        entries.append({"key": key, "number": n, "title": spec["title"], "body": body})

    return {"entries": entries, "numbers": {e["key"]: e["number"] for e in entries}}
