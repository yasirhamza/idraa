# Control-Weight Robustness (Rank-Stability)

**Issue:** #419
**Spec:** `docs/superpowers/specs/2026-06-25-control-weight-robustness-design.md`
**Identifiability rationale:** `docs/reviews/2026-06-25-faircam-control-roi-identifiability.md`
**Code:** `src/riskflow/services/weight_robustness.py`

---

## Why this feature exists

FAIR-CAM's composition weights are **not statistically identifiable** from any
single organisation's loss data — by ~3–5 orders of magnitude. No per-org
calibration layer changes that; it relocates the guess. The honest move is to
**propagate the weights' uncertainty through the model** and report how stable
the control decisions are under that uncertainty. This is a decision-aid, not a
validated measurement.

## 1 — The perturbation kernel (band model)

The weight-robustness ensemble perturbs each canonical weight in **logit space**
using a logit-normal kernel:

```
w_draw = expit(logit(w) + σ · Z),   Z ~ N(0,1)
```

This is **a perturbation kernel CENTERED on the unvalidated canonical guess** —
it is not a non-informative prior (Meth-I1). True ignorance over a bounded
proportion would be uniform or Jeffreys-Beta, not a distribution centred on the
SME value. The feature tests rank-stability *under perturbation*, not Bayesian
ignorance about the weights.

### Why logit space?

`expit` / `logit` is the natural link for a proportion `w ∈ (0,1)`. The
logit-normal kernel has no atom at 0 or 1 and no hard clamp, and it gives
**equal dispersion in logit (relative) space** across all five canonical
parameters (four node-mapping weights + the `meta.kappa` coupling gain).

Earlier band shapes were rejected for worse, removable artifacts:
- Pre-truncation over `[w/2, 2w]` shifted the centre down ~25%
  (false under-crediting of Prevention controls).
- A hard clamp at 1 created a `w=1` atom that compressed LEF variance ~60–74%
  while magnitude parameters lost none (an asymmetric artificial artefact).

### Absolute-vs-logit caveat (Meth-B7)

Equal dispersion is in **logit (relative) space, not absolute-`w` or
ALE-effect space**. Because the engine multiplier `1 - E·w` (`1 − E·w`) is linear
in absolute `w`, near-ceiling weights (e.g., prevention.vuln = 0.9) swing less
in absolute `w` (p5–p95 abs-Δw ≈ 0.19) than mid-range weights (e.g.,
magnitude.secondary = 0.5, ≈ 0.46). Controls dominated by near-ceiling weights
will therefore read as somewhat more rank-stable. This is a **genuine consequence
of bounded uncertainty** (logit is the natural metric for a bounded proportion),
NOT an artifact to remove.

### σ=0.6 is not validated (Meth-I2)

The default logit-space perturbation width σ=0.6 is an engineering choice,
**not validated** against empirical calibration data (none exists at single-org
scale). It may be too narrow to honour the identifiability review's "wide
plausibility" mandate. A σ-sensitivity fixture (σ ∈ {0.6, 1.0, 1.5}) is part
of the test harness; the rank-stability verdict is documented as **conditional
on σ**. Widening σ only makes verdicts more pessimistic (the safe direction).

## 2 — Co-variation: the distinct-canonical-parameter set

The engine-applied `(group, node)` slots collapse to **four distinct node-weight
parameters** (Slice 2 #439/D1 retired the VMC/DSC direct node targets, leaving
`LEC_PREVENTION` + `LEC_RESPONSE` slots only); the fifth canonical parameter is
`meta.kappa`, the meta→reliability coupling gain, which is **not** a
`(group, node)` slot at all. Each node-weight parameter is sampled once per draw
and broadcast to all its slots. Within each FAIR-CAM function, the two or more
weights **co-vary** (share ONE logit-space draw `Z`). Sampling them independently
would understate instability (overstate apparent stability) via partial
cancellation: two same-function weights moving in opposite directions partially
cancel each other's contribution to the control's total reduction, narrowing the
draw-to-draw band and making rank fluctuations appear smaller than they truly are.

| Canonical parameter | Canonical value | Slots driven |
|---|---|---|
| `prevention.tef` | 0.8 | `LEC_PREVENTION.threat_event_frequency` |
| `prevention.vuln` | 0.9 | `LEC_PREVENTION.vulnerability` |
| `magnitude.secondary` | 0.5 | `LEC_RESPONSE.secondary_loss` |
| `magnitude.primary` | 0.2 | `LEC_RESPONSE.primary_loss` |
| `meta.kappa` | 0.5 (`KAPPA_META_RELIABILITY`) | none — coupling gain, not a node-weight slot; threaded as the second member of the `(node_mapping, kappa)` `EnsembleDraw` |

`magnitude.*` co-variation is unambiguous — each group receives a
`dict(_MAGNITUDE_WEIGHTS)` COPY (not a live shared reference) in
`fair_cam/models/composition_topology.py`, so each group owns its own mutable
weights dict that the sampler's `_apply_param_values` can overwrite
independently. Since Slice 2 (#439) only `LEC_RESPONSE` reaches the
`magnitude.*` keys (the DSC groups' direct magnitude targets were retired); the
shared keys are kept even at N=1 slots for forward-compat. **Falsifier:** if a
future group re-targets magnitude with a canonical literal that DIVERGES from
`LEC_RESPONSE`'s, the shared `magnitude.*` key must be split into independent
parameters.

**`meta.kappa` anchor:** kappa enters via
`r_eff = r0 + (1 − r0) · κ · E_meta` — half of composed meta strength converts
to recovery of the `(1 − r0)` reliability headroom at the canonical 0.5. It has
its **own correlation group** (`"meta"`), so it draws an independent `Z` from
the prevention/magnitude weight groups. It is perturbed with the SAME
logit-sigma as the node-mapping weights — a convenience convention (reusing a
routing-weight sigma for a coupling gain), not a calibrated choice; do NOT
introduce per-param sigma (#419 discipline).

### Retired parameters (history)

`vmc.vuln` (canonical 0.3; drove `VMC_VARIANCE_PREVENTION.vulnerability` +
`VMC_IDENTIFICATION_CORRECTION_PAIR.vulnerability`) was **RETIRED by #439**
(Slice 2 D1): the VMC/DSC direct FAIR-node targets were removed on §2.2 p.5
"Indirectly Affect Risk" grounds, and meta value now flows exclusively through
the `meta.kappa` reliability coupling. The old falsifier ("split `vmc.vuln` if
the two 0.3 literals ever diverge") is obsolete with the parameter.

### Within-function vs across-function independence (Meth-I4/N4)

**Within** each FAIR-CAM function (prevention / magnitude / meta), the parameters
share ONE logit-Z: they co-vary by construction. **Across** functions the five
parameters are sampled independently (`meta.kappa` is a single-member group, so
its independent `Z` is the whole story for kappa). This is a deliberate
**band-widening simplification** — cross-function correlation is unknown, and
independence is the conservative (honest) direction. The Meth-N11 caveat
applies: independent sampling of the two `LEC_PREVENTION` weights can slightly
narrow a prevention-dominated control's band via partial cancellation in one
place — this is second-order and accepted.

### LEC_RESPONSE vs Detection-Response pair (Arch-I5)

The engine reads LEC_RESPONSE's **weights** for the magnitude multiplier `1 − E·w`
(the `E` comes from the D/R-pair effectiveness, but `w` comes from the
`LEC_RESPONSE` entry in the `node_mapping`). The `LEC_DETECTION_RESPONSE_PAIR`
group is **skipped** as a standalone application; its weights in the node_mapping
are never read. The ensemble therefore only perturbs `LEC_RESPONSE` weights (not
the D/R-pair entry), and a contract test (`test_drpair_weights_are_inert`) guards
this invariant.

## 3 — Ensemble and rank-stability metrics

K draws (default 256) per run. Each draw:
1. Sample each of the five canonical parameters logit-normal (one Z per FAIR-CAM
   function; kappa gets its own independent Z).
2. Build the sampled `(node_mapping, kappa)` `EnsembleDraw` via
   `sample_ensemble_draw(rng)` — the live sampler (it replaced the pre-Slice-2
   weights-only `sample_node_mapping`, deleted by #439).
3. Recompute per-control risk-reduction-$ via the same closed-form value function
   Shapley uses (the two-phase `precompose_parts` / `finalize_composition(κ)` +
   `reduction_from_composition` split of `subset_reduction_closed_form`).
4. Record the control ranking for this draw.

Aggregate across K draws:

**Per control:**
- `reduction_p5 / p50 / p95` — the stochastic dollar range.
- `rank_p50 / rank_min / rank_max` — rank distribution.
- `stability_class` (`stable` / `unstable` / `not_applicable` / `not_assessed`):
  fraction of draws where the control's rank is **within ±1 of its canonical rank**
  (Meth-I4 — exact rank-hold is too strict for dense middle-ranking controls).
  `stability_class` is a **SECONDARY signal**; the primary decision-relevant
  verdicts are top-k preservation and pairwise indistinguishability.

**Per pair / overall:**
- `indistinguishable_pairs` — ordered pairs where the canonical order flips in
  ≥ 10% of draws (default threshold). Pairs near this boundary carry ~±few% MC
  error at K=256 — treat them as "borderline" (Meth-N6).
- `kendall_tau_p50` — Kendall τ-a (no tie correction; ties negligible with
  continuous values and alphabetical tiebreak; near-ties counting as discordances
  is intentional for surfacing indistinguishability — Meth-N2).
- `topk_preservation_k / topk_preservation_prob` — fraction of draws where the
  top-k set is identical to canonical.

### Stability-boundary MC noise (Meth-N10)

The 0.90 stable-fraction threshold is unvalidated. `held_fraction` is a
K-dependent MC estimate (~±2 pp at K=256, ~±5 pp at K_min). Controls near 0.90
are "borderline"; top-k and pairwise are the primary signals.

## 4 — Representative-value basis and the mean/median gap (Meth-B1/I3)

The displayed control ranking lives in **representative-value (PERT mode /
lognormal median) space** — the same closed-form `subset_reduction_closed_form`
basis that Shapley uses. The ensemble perturbs weights inside that same closed
form, so the rank-stability verdict is **faithful to the displayed ranking by
construction**.

This is **NOT** a claim that the closed-form scalar equals the MC-mean ALE delta.
The Monte-Carlo mean is higher than the closed-form median for skewed losses by a
factor of `exp(σ²/2)` (where σ is the lognormal shape parameter of the loss
distribution). For cyber losses with moderate to high skew, this gap can be **3–23×**
or larger. The representative-value ranges therefore sit below the MC-mean headline;
both figures are presented on the surface with explicit labels.

Concretely, `fair_cam/risk_engine/control_attribution.py:representative_value`
defines the per-distribution scalar (PERT mode for PERT/Triangular inputs, lognormal
median for lognormal inputs, mean for Normal, midpoint for Uniform). The displayed
control ranking is ordered by `displayed_control_order` in
`src/riskflow/services/aggregate_run_view_model.py`, which uses these same
representative values as the sort key.

## 5 — SINGLE-scenario: ranges only (Meth-B6)

**SINGLE** scenario runs get per-control dollar ranges and a headline range.
Rank-stability verdicts and indistinguishable-pair markers are **not shown**.
The SINGLE displayed order is effectiveness-sorted (not the Shapley attribution
basis the ensemble ranks), so a stability badge against a different ranking
basis would be misleading. `rank_stability_available = False` and
`stability_class = "not_applicable"` on all per-control entries.

## 6 — Calibrated ≠ validated (honest disclosure)

The canonical weights are **calibrated** (grounded in SME judgment and the
FAIR-CAM standard), but **not validated** against org-specific loss outcome data
— because validation in that sense is infeasible at single-org scale (see
`docs/reviews/2026-06-25-faircam-control-roi-identifiability.md`). This feature
does not change what the weights mean; it propagates their irreducible uncertainty
into the outputs so consumers can see whether their decisions are robust to it.

Dollar figures produced by the model are **model-relative ranges under
composition-weight uncertainty, not validated estimates of realized loss
reduction**. Controls flagged *indistinguishable* cannot be ordered given that
uncertainty.

## 7 — Performance and determinism

The ensemble runs as a synchronous in-process closed-form loop after the main
Monte Carlo and Shapley pass. `asyncio.to_thread` is used for responsiveness
only — it is GIL-bound, not parallel. Wall-clock ≈ (1+K) × the canonical pass.

**Budget and degradation:**
- `K_effective = min(K_target, eval_budget // C)` where C is the realized per-draw
  Shapley cost (capped at the 2M inner limit). Large portfolios whose canonical
  pass saturates the 2M cap get `K_effective ≈ 5` at the default 10M budget —
  `state="insufficient_budget"` and the deterministic band-endpoint envelope
  is the expected outcome for large-portfolio runs.
- Below `weight_ensemble_min_draws` (default 32), no stability verdict is emitted;
  `state="insufficient_budget"` surfaces as "robustness not assessed (K=n)".

**Determinism:** the ensemble RNG is a `SeedSequence` child spawned from the run
seed using a distinct spawn key (`WEIGHT_ROBUSTNESS_SPAWN_DOMAIN`) so it is
decorrelated from the main MC stream. The band config (logit_sigma, draws, seed)
is snapshotted into `weight_robustness.band` and re-read by the sampler on
re-run, so settings changes between run and report regeneration cannot alter
already-issued ranges.

## 8 — Phase-2 roadmap: full-MC tail bands

Phase-2 extends the same band model, sampler, and parameter grouping (§2) to put
uncertainty bands on the loss-exceedance / VaR tail by running a full per-iteration
MC per draw (heavier: minutes-to-more, needs background orchestration). The
deterministic band endpoints from §5 of the spec are the natural stepping stone
(the extremes of the same band the ensemble samples). Not built in v1.

## See Also

- `docs/reference/product-form-tail-approximation.md` — the product-form LEF×LM
  tail approximation disclosure; note the mean/median gap (`exp(σ²/2)`) that
  separates the representative-value ranges from the MC-mean headline.
- `docs/reference/vulnerability-semantics.md` — semantics of the `vulnerability`
  node that the `prevention.vuln` parameter targets (`vmc.vuln` retired by
  #439 — see "Retired parameters" above).
- `docs/superpowers/specs/2026-06-25-control-weight-robustness-design.md` — full
  design spec with §2 co-variation table, §4 data contract, §7 perf/budget, and
  the full plan-gate decision log.
- `docs/reviews/2026-06-25-faircam-control-roi-identifiability.md` — the
  identifiability audit that motivated this feature and retired absolute ROI
  point claims.
- `src/riskflow/services/weight_robustness.py` — logit-normal sampler, co-variation
  resolver, ensemble runner, and `WEIGHT_ROBUSTNESS_KEYS` (the persisted key
  contract).
