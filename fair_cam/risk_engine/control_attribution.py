"""Pyfair-free per-control attribution helpers.

Extracted from the legacy calculator's `_build_control_adjustment` (Task 4;
the calculator itself was retired in #328 — these free functions are the live
home). The FAIR math is byte-identical to the original method — the only
change is that `effectiveness_calculator` is passed in as a parameter.

This module touches only pyfair-free routines (`compose_groups`,
`_group_comp_to_node_multipliers`, `effectiveness_calculator.*`). pyfair was
fully removed from the package in epic #324; the whole import chain is now
pyfair-free.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from fair_cam.models.composition_topology import (
    GROUP_NODE_MAPPING,
    KAPPA_META_RELIABILITY,
    BooleanGroup,
    NodeMapping,
)
from fair_cam.models.control import Control
from fair_cam.models.risk_enhanced import ControlAdjustment
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters
from fair_cam.risk_engine.group_composition import GroupComposition, compose_groups

if TYPE_CHECKING:
    from fair_cam.controls.effectiveness import ControlEffectivenessCalculator


def representative_value(dist: FAIRDistribution) -> float:
    """Scalar central value for the per-control closed-form attribution.

    NB (methodology): LOGNORMAL returns the MEDIAN exp(mu); this is a v3
    view-model attribution scalar, NOT the engine ALE value (which is the MC
    mean). Median chosen for the skewed loss central tendency; documented per
    the calibration-philosophy gate.
    """
    p = dist.parameters
    if dist.distribution_type in (DistributionType.PERT, DistributionType.TRIANGULAR):
        return float(p["mode"])
    if dist.distribution_type == DistributionType.UNIFORM:
        return float((p["low"] + p["high"]) / 2)
    if dist.distribution_type == DistributionType.NORMAL:
        return float(p["mean"])
    if dist.distribution_type == DistributionType.LOGNORMAL:
        return float(math.exp(p["mean"]))
    if dist.distribution_type == DistributionType.BETA:
        a, b = p["alpha"], p["beta"]
        return float((a - 1) / (a + b - 2)) if a > 1 and b > 1 else float(a / (a + b))
    return 0.5


def representative_mean(dist: FAIRDistribution) -> float:
    """Scalar EXPECTED VALUE for the mean-basis closed-form attribution.

    Calibration philosophy (documented per the calibration-philosophy gate):
    this is the distribution MEAN, chosen so that the closed-form chain
    ``E[TEF]*E[Vuln]*E[Loss]`` equals the mean ALE under the engine's
    independence assumption (expectation factors over independent draws —
    the same independence the Monte-Carlo engine samples under). This makes
    mean-basis v(S) directly comparable to the MC mean headline, unlike the
    typical-basis chain (:func:`representative_value`), whose mixed
    mode/median scalars do not compose to any exact statistic of the product.

    Closed forms (all exact, standard results):
    - PERT (Vose gamma=4, the engine's sampler shape): (low + 4*mode + high)/6.
    - TRIANGULAR: (low + mode + high)/3.
    - UNIFORM: (low + high)/2.
    - NORMAL: mean.
    - LOGNORMAL (log-space ``mean``=mu, ``sigma``): exp(mu + sigma^2/2).
    - BETA(alpha, beta): alpha/(alpha + beta).

    The unknown-type fallback mirrors :func:`representative_value` (0.5,
    defensive parity — POISSON/EXPONENTIAL are not used for TEF/LM in this
    codebase per fair_core's NotImplementedError note).

    NB (Jensen caveat, documented; corrected per methodology review F1):
    downstream ``reduction_from_composition`` applies
    ``max(0, base_secondary*m - currency_subtractor)``. The exact identity is
    ``E[max(0, m*SL - c)] = m*E[SL] - c + E[(c - m*SL)^+]``, so the closed
    form's residual is biased LOW (and v(S) biased HIGH) by ``E[(c - m*SL)^+]``
    whenever ``P(m*SL < c) > 0`` — for a lognormal SL that is EVERY ``c > 0``,
    regardless of where the mean sits. The chain is therefore exact iff the
    subset's currency-subtractor total is ZERO; with a CURRENCY control
    present the bias is negligible while ``c << m*E[SL]`` (measured ~$20 at
    c = 0.18*E[SL]) but material as ``c`` approaches ``m*E[SL]`` (measured
    ~20% of E[SL] at c = E[SL], sigma = 0.5). In the deep-clip regime
    (``m*E[SL] <= c``, closed form floored to 0) the bias magnitude is
    ``E[(m*SL - c)^+]``, for which the expression above is an upper bound —
    same direction, small in practice.

    NB (MC-agreement noise): this closed form is the TRUE expectation; the MC
    headline is a sample mean whose relative SE grows as
    ``sqrt((e^(sigma^2) - 1)/n)`` — ~0.4% at sigma=1 (n=100k) but ~28% at
    sigma=3. Divergence at large authored sigma is MC noise, not a chain bug.
    """
    p = dist.parameters
    if dist.distribution_type == DistributionType.PERT:
        return float((p["low"] + 4.0 * p["mode"] + p["high"]) / 6.0)
    if dist.distribution_type == DistributionType.TRIANGULAR:
        return float((p["low"] + p["mode"] + p["high"]) / 3.0)
    if dist.distribution_type == DistributionType.UNIFORM:
        return float((p["low"] + p["high"]) / 2)
    if dist.distribution_type == DistributionType.NORMAL:
        return float(p["mean"])
    if dist.distribution_type == DistributionType.LOGNORMAL:
        return float(math.exp(p["mean"] + p["sigma"] ** 2 / 2.0))
    if dist.distribution_type == DistributionType.BETA:
        a, b = p["alpha"], p["beta"]
        return float(a / (a + b))
    return 0.5


def build_control_adjustment(
    control: Control,
    effectiveness_calculator: ControlEffectivenessCalculator,
    base_tef: float,
    base_vuln: float,
    base_primary: float,
    base_secondary: float,
    *,
    availability_self_detection: bool = False,
) -> ControlAdjustment:
    """Build a single control's `ControlAdjustment` closed-form (#130 D9).

    Replaces `effectiveness.calculate_control_risk_adjustment`'s retired
    per-control domain->node multiplier path. Each control's standalone
    isolated effect is computed from `compose_groups([control])` — the SAME
    shared routine the engine ALE path uses — mapped to per-node multipliers
    via `GROUP_NODE_MAPPING` (Response Detection-gated per D8). This keeps the
    #203 attribution matrix + reports populated WITHOUT re-introducing
    engine<->view-model drift (the value derives from the shared routine, not
    stale `effectiveness.py` math) and WITHOUT a per-control FairModel /
    Monte-Carlo re-run (NEW-3: that would be O(controls×scenarios) — forbidden).

    `risk_reduction_value` is the CLOSED-FORM SCALAR ALE delta
    `original_ale − adjusted_ale` computed from the single-control node
    multipliers (multipliers-only, the CURRENCY subtractor is EXCLUDED — it
    lives in `loss_reduction_per_event`, applied separately in
    `_apply_control_adjustments`; this is the #258 invariant pinned by
    `test_risk_reduction_value_excludes_subtractor`). The matrix cell is
    `risk_reduction_value + loss_reduction_per_event × LEF`, so the subtractor
    must NOT also be in `risk_reduction_value` or it double-counts.

    ``availability_self_detection`` — availability effects self-detect
    (FAIR-CAM §3.3.2 p.19); credits raw LEC_RESPONSE. Default False =
    detection-gated (§3.3 p.18).

    **κ seam (Slice 2 D5):** the standalone per-control adjustment is
    self-coupling-free — it composes with ``kappa=0.0`` so a control that
    carries BOTH a meta (VMC/DSC) channel and a Loss-Event channel does NOT
    uplift its OWN reliability via the meta→reliability coupling. Coupling
    credit is carried by the Shapley/coalition value function
    (``subset_reduction_closed_form`` at default κ), where E_meta of one
    control can uplift the co-present LEC reliability of ANY control in the
    subset (Slice 2 D5): standalone means the uncoupled OWN-effect of a single
    control; coupling credit is a property of the coalition/subset, not of any
    individual control, so it is deliberately excluded from this per-control cell.
    """
    comp = compose_groups([control], kappa=0.0)
    node_multipliers = _group_comp_to_node_multipliers(
        comp, availability_self_detection=availability_self_detection
    )
    tef_mult = node_multipliers["threat_event_frequency"]
    vuln_mult = node_multipliers["vulnerability"]
    primary_mult = node_multipliers["primary_loss"]
    secondary_mult = node_multipliers["secondary_loss"]

    # Per-assignment breakdown + CURRENCY subtractor still come from the
    # calculator (PR μ.1b #129 §6 snapshot-debuggability contract — consumed
    # by run_executor payload + reports). Only the calculator's per-control
    # domain->node MULTIPLIER branch is retired (#130 full migration); its
    # breakdown + loss_reduction_per_event accumulation are unchanged and the
    # node multipliers it now returns are identity. We OVERRIDE the multiplier
    # fields + risk_reduction_value with the group-composed values below.
    base_adj = effectiveness_calculator.calculate_control_risk_adjustment(
        control, base_tef, base_vuln, base_primary, base_secondary
    )
    loss_reduction_per_event = base_adj.loss_reduction_per_event

    # Closed-form multipliers-only ALE delta (matches the form
    # effectiveness.py:192-205 used today; subtractor EXCLUDED — Arch-B2 /
    # #258 / R3 N-arch-A: it lives in loss_reduction_per_event and is applied
    # separately, so the matrix cell never double-counts).
    original_ale = base_tef * base_vuln * (base_primary + base_secondary)
    adjusted_ale_multipliers_only = (
        base_tef
        * tef_mult
        * base_vuln
        * vuln_mult
        * (base_primary * primary_mult + base_secondary * secondary_mult)
    )
    risk_reduction_value = original_ale - adjusted_ale_multipliers_only

    return ControlAdjustment(
        control_id=control.control_id,
        control_name=control.name,
        threat_event_frequency_multiplier=tef_mult,
        vulnerability_multiplier=vuln_mult,
        primary_loss_multiplier=primary_mult,
        secondary_loss_multiplier=secondary_mult,
        control_effectiveness=control.calculate_risk_reduction_factor(),
        confidence_level=0.95,
        risk_reduction_value=risk_reduction_value,
        control_cost=control.cost_model.annual_cost,
        loss_reduction_per_event=loss_reduction_per_event,
        breakdown=base_adj.breakdown,
    )


def subset_reduction_closed_form(
    risk_params: FAIRParameters,
    controls: list[Control],
    node_mapping: Mapping[BooleanGroup, NodeMapping] | None = None,
    *,
    availability_self_detection: bool = False,
    kappa: float = KAPPA_META_RELIABILITY,
) -> float:
    """Closed-form ALE reduction v(S) = base_ALE - ALE(S) for a control subset.

    Generalises ``build_control_adjustment``'s single-control closed form
    (#130 D9) to an arbitrary subset, via the SAME shared ``compose_groups``
    routine (no re-derivation in the app layer). Unlike the standalone cell's
    ``risk_reduction_value`` (multipliers-only, #258), v(S) INCLUDES the
    CURRENCY subtractor on secondary loss with the engine's ``max(0, .)``
    floor — Shapley needs one coherent total per subset, and for a
    non-currency subset the two reconcile exactly (currency_total == 0).

    Point-estimate basis: ``representative_value`` per distribution (PERT/TRI
    mode, LOGNORMAL median, NORMAL mean, UNIFORM midpoint, BETA mode-or-mean
    fallback) — this is the TYPICAL chain only (methodology review F4): the
    reconciliation claim below is scoped to ``statistic="typical"``. It is
    IDENTICAL to the basis ``NativeControlAwareRiskCalculator`` feeds
    ``build_control_adjustment`` for the standalone attribution cell
    (``risk_reduction_value``, which has NO mean twin — it is multipliers-only
    and linear in base scalars, so a mean twin is exactly derivable if ever
    juxtaposed with mean-basis figures), so typical-basis v({c}) reconciles
    with that cell. The side-by-side display's PRIMARY figures use the mean
    chain via ``scenario_base_ale(statistic="mean")`` instead. NOT a
    Monte-Carlo run; cheap enough for the 2^n subset evaluations the Shapley
    value function performs. ``v([]) == 0`` (``compose_groups([])`` yields
    identity multipliers + zero currency).

    ``node_mapping`` — optional override for the group→FAIR-node weight table.
    When ``None`` (the default), the module-global ``GROUP_NODE_MAPPING`` is
    used, preserving identical behaviour for all existing callers.  Callers
    that pass an alternative mapping (e.g. the weight-robustness ensemble,
    issue #419) can perturb the Shapley value-function path without touching
    the main MC-mean adjustment path (``build_control_adjustment`` and the
    ``control_aware.py:460`` call are deliberately left on the canonical
    ``GROUP_NODE_MAPPING`` — Spec-N2/Spec-N-Seam1).

    **Invariant guard (Sec-I3 / Meth-I-Floor1):** With ``E ∈ [0, 1]`` and
    ``w ∈ (0, 1]``, the multiplier ``1 − E·w ≥ 0`` by construction, so
    ``adjusted_ale ≤ original_ale`` and the reduction is always non-negative.
    A negative reduction indicates a broken invariant (e.g. ``E > 1`` or
    ``w > 1`` in a perturbed mapping) — this is raised as ``ValueError``
    rather than silently floored, matching the fail-loud pattern in
    ``fair_core.py`` lines 251-252 and 258-260.  FP noise below ``1e-9`` is
    floored to ``0.0`` only.

    Used as the value function for Shapley attribution (cooperative-game
    averaging lives in v3's ``services/shapley.py``, not in fair_cam).

    ``availability_self_detection`` — availability effects self-detect
    (FAIR-CAM §3.3.2 p.19); credits raw LEC_RESPONSE. Default False =
    detection-gated (§3.3 p.18).

    ``kappa`` — the meta→reliability coupling strength (Slice 2 #439). The
    composed meta strength E_meta of the subset uplifts every co-present LEC
    opeff/currency reliability via ``r_eff = r0 + (1-r0)*kappa*E_meta`` inside
    ``compose_groups``. Defaults to the canonical
    :data:`KAPPA_META_RELIABILITY`, so v(S) — the Shapley value function —
    CREDITS a VMC/DSC meta control that co-occurs with a Loss-Event control (a
    meta-only subset still reduces nothing: no LEC to uplift). The
    weight-robustness ensemble (issue #419) will perturb this via the two-phase
    ``precompose_parts`` / ``finalize_composition`` split (param key
    "meta.kappa"); wired in Task 4 — this one-pass wrapper stays on the canonical
    κ. Callers that
    want the UNCOUPLED reduction (e.g. the self-coupling-free standalone seams)
    pass ``kappa=0.0``.
    """
    # Thin wrapper kept for backward compat + the canonical/displayed one-pass
    # callers. The weight-robustness ensemble splits these two steps explicitly
    # (scenario_base_ale once per scenario; precompose_parts cached across draws;
    # finalize_composition + reduction_from_composition per draw) so the
    # expensive weight-INVARIANT compose_groups is not recomputed on every draw
    # — see #419 perf refactor.
    base = scenario_base_ale(risk_params)
    comp = compose_groups(controls, kappa=kappa)
    return reduction_from_composition(
        base, comp, node_mapping, availability_self_detection=availability_self_detection
    )


def scenario_base_ale(
    risk_params: FAIRParameters,
    statistic: str = "typical",
) -> tuple[float, float, float, float, float]:
    """Weight-invariant scenario base values for the closed-form attribution:
    ``(base_tef, base_vuln, base_primary, base_secondary, original_ale)``.

    These depend ONLY on the scenario's FAIR params and the chosen scalar
    ``statistic``, NOT on the control subset NOR on the (possibly perturbed)
    node_mapping weights. The weight-robustness ensemble computes them ONCE per
    (scenario, statistic) and reuses them across every subset and weight draw.

    statistic:
      - ``"typical"`` (default, back-compat): :func:`representative_value` —
        mode/median central scalars; the historical typical-case chain. For
        heavy-tailed lognormal severities this sits well below the mean.
      - ``"mean"``: :func:`representative_mean` — expectation chain; under the
        engine's independence assumption ``E[TEF]*E[Vuln]*E[Loss]`` IS the mean
        ALE, so mean-basis v(S) is directly comparable to the MC mean headline
        (see the Jensen caveat on :func:`representative_mean` for the currency
        subtractor's clip).
    """
    if statistic == "typical":
        rep = representative_value
    elif statistic == "mean":
        rep = representative_mean
    else:  # fail loud — a silent wrong statistic corrupts every downstream figure
        raise ValueError(f"unknown attribution statistic: {statistic!r}")
    base_tef = rep(risk_params.threat_event_frequency)
    base_vuln = rep(risk_params.vulnerability)
    base_primary = rep(risk_params.primary_loss)
    base_secondary = rep(risk_params.secondary_loss)
    original_ale = base_tef * base_vuln * (base_primary + base_secondary)
    return base_tef, base_vuln, base_primary, base_secondary, original_ale


def reduction_from_composition(
    base: tuple[float, float, float, float, float],
    comp: GroupComposition,
    node_mapping: Mapping[BooleanGroup, NodeMapping] | None = None,
    *,
    availability_self_detection: bool = False,
) -> float:
    """``v(S)`` from a PRECOMPUTED ``compose_groups`` result + the scenario base
    values, applying only the node_mapping (weight) layer.

    Composition is weight-INVARIANT, so the ensemble computes the κ-invariant
    ``precompose_parts`` result (``ComposedParts``) once per subset and caches
    THAT across all draws — per draw it runs the cheap ``finalize_composition(κ)``
    to produce the ``comp`` passed here, then this weight application
    (``1 - E*w`` + currency subtractor + arithmetic). (It does NOT cache
    finished ``compose_groups`` results: those bake in one κ, and κ varies per
    draw — Slice 2 #439.) The result is IDENTICAL to
    ``subset_reduction_closed_form`` for the same inputs (same arithmetic, just
    reorganised). Same fail-loud invariant guard.

    ``availability_self_detection`` — availability effects self-detect
    (FAIR-CAM §3.3.2 p.19); credits raw LEC_RESPONSE. Default False =
    detection-gated (§3.3 p.18).
    """
    nm = node_mapping if node_mapping is not None else GROUP_NODE_MAPPING
    base_tef, base_vuln, base_primary, base_secondary, original_ale = base
    m = _group_comp_to_node_multipliers(
        comp, nm, availability_self_detection=availability_self_detection
    )
    adjusted_secondary = max(
        0.0, base_secondary * m["secondary_loss"] - comp.currency_subtractor_total
    )
    adjusted_ale = (
        base_tef
        * m["threat_event_frequency"]
        * base_vuln
        * m["vulnerability"]
        * (base_primary * m["primary_loss"] + adjusted_secondary)
    )
    reduction = original_ale - adjusted_ale
    if reduction < -1e-9:  # broken 1-E*w invariant (E>1?) -> fail loud, never a silent negative
        raise ValueError(f"negative subset reduction {reduction!r}: 1-E*w invariant violated")
    return max(0.0, reduction)  # floor FP noise only
