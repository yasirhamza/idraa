"""Canonical τ calibration table for ELAPSED_TIME sub-functions.

Per FAIR-CAM Standard §2.3 the composition formula is implementation-
defined. Per audit §8 preamble: "no recommendation is made here." PR μ.1
SELECTED exponential normalization with τ per sub-function as v3's
implementation choice.

Issue #131 recalibration (2026-05-16): τ values are now strict-cite-or-
drop. Every entry below carries a primary citation (page/figure number)
AND an explicit calibration philosophy line documenting whether the
input statistic is a **mean** (anchor: ``τ = mean``, exponential mean-
lifetime semantics) or a **median** (anchor: ``τ = median / ln(2)``,
median-half-life semantics). Sub-functions that previously held v3-
default "no canonical source" τ values were re-classified to
``UnitType.PROBABILITY`` in the SUB_FUNCTION_UNITS table (both the v3
mirror in ``idraa.models.enums`` and this package's
``fair_cam.models.sub_function``) and removed from the table entirely.

The values are SHA-256 pinned by fair_cam/tests/calibration/test_elapsed_time_taus.py.
Modifying any entry requires:
  1. Update the baseline digest in the pinning test.
  2. Update docs/plans/2026-05-15-issue-131-tau-calibration-design.md §3
     (the calibration-philosophy + hand-math source of truth) with the
     new value, source citation, and rationale.
  3. Re-pin any backtest fixtures with side-by-side hand-math + actual
     output per CLAUDE.md "Verification reporting."
  4. PR description must call out the calibration change explicitly.

Per Spec-I3, the accessor `get_canonical_tau(sub_function)` is the
recommended call site. Future per-org override layer composes via this
accessor without re-editing the calculator.

Sensitivity (whole-project-eval methodology item)
-------------------------------------------------
Under exponential normalization ``opeff(t) = exp(-t/τ)`` the relative
sensitivity of opeff to a τ perturbation is closed-form::

    ∂opeff/opeff = (t/τ) · (∂τ/τ)

so at the anchor point ``t = τ`` a ±20% τ swing moves opeff from
``exp(-1) ≈ 0.368`` to ``exp(-1/1.2) ≈ 0.435`` (+18%) or
``exp(-1/0.8) ≈ 0.287`` (-22%); short elapsed times (``t ≪ τ``) are
nearly insensitive, long ones amplify. ALE-level impact is NOT stated
here — it depends on the composition operator, the scenario's other
factors, and the control mix; quantify per scenario via a what-if run,
not from this table.

Cross-anchor caveat (mean vs median): the two anchor philosophies in
this table are NOT interchangeable. ``τ_mean-anchor = mean`` gives
``opeff(mean) = exp(-1) ≈ 0.368``; ``τ_median-anchor = median / ln(2)``
gives ``opeff(median) = 0.5``. Plugging a published MEAN into the
median-half-life formula (``mean / ln(2)``) inflates τ by ~44% for the
same data and was exactly the issue-#131 mean/median conflation the
methodology-reviewer gate now exists to catch. Each entry below names
its anchor explicitly — keep it that way.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from fair_cam.models.sub_function import FairCamSubFunction

_TAU_RAW: dict[FairCamSubFunction, float] = {
    # ── LEC Detection / Response ──────────────────────────────────────────
    # IBM CODB 2024 p10 Fig 4 — MTTI **mean** = 194d (label "Mean time to identify").
    # Calibration philosophy: exponential mean lifetime (τ = mean). Under exp
    # distribution: opeff(t=mean) = exp(-1) ≈ 0.368; opeff(t=median=134.5d) = 0.5.
    # See docs/plans/2026-05-15-issue-131-tau-calibration-design.md §3
    # "Calibration philosophy" subsection.
    FairCamSubFunction.LEC_DET_MONITORING: 194.0,
    # IBM CODB 2024 p10 Fig 4 — MTTC **mean** = 64d (label "Mean time to contain").
    # Same exponential mean-anchor as LEC_DET_MONITORING above.
    # opeff(t=mean) = exp(-1) ≈ 0.368; opeff(t=median=44.4d) = 0.5.
    FairCamSubFunction.LEC_RESP_EVENT_TERMINATION: 64.0,
    # ── VMC Correction ────────────────────────────────────────────────────
    # DBIR 2024 p21 Fig 19 — CISA KEV survival-curve median = 55d (read at the
    # survival=0.5 line; distribution-agnostic by construction — survival
    # curves give medians directly without assuming an underlying parametric
    # family).
    # Calibration philosophy: median half-life (τ = median / ln(2)).
    # τ = 55 / ln(2) ≈ 79.3 → opeff(t=median) = 0.5 by construction.
    FairCamSubFunction.VMC_CORR_IMPLEMENTATION: 79.3,
}

# Wrap in MappingProxyType for runtime immutability (Spec-N2).
TAU_BY_SUB_FUNCTION: Mapping[FairCamSubFunction, float] = MappingProxyType(_TAU_RAW)


def get_canonical_tau(sub_function: FairCamSubFunction) -> float:
    """Canonical τ accessor (Spec-I3).

    Per-org overrides will compose on top of this in a follow-up PR via
    a wrapping accessor `get_tau(sub_function, org_id=None)`. Keep this
    accessor stable.

    Raises KeyError if sub_function is not one of the three primary-cited
    ELAPSED_TIME sub-functions (LEC_DET_MONITORING,
    LEC_RESP_EVENT_TERMINATION, VMC_CORR_IMPLEMENTATION). The KeyError is
    intentional — surfaces missing-entry bugs at the call site instead of
    silently returning a default.
    """
    return TAU_BY_SUB_FUNCTION[sub_function]
