# True Mixture Pooling for SME Elicitation — Design (idraa#27 via idraa#25)

**Status:** approved-pending-owner-review · **Date:** 2026-07-19 · **Fixes:** #27 (pooling is parameter-averaging) via #25 (true mixture)

## The defect (issue #27, verified in code)

`fair_cam.quantile_pooling.combine_lognorm_trunc` / `combine_norm` pool multi-SME
fits by weighted **arithmetic averaging of distribution parameters** (a faithful
port of evaluator/collector `R/fit_distributions.R:67-79`, self-documented as an
"engineering approximation"). Parameter averaging discards between-expert
variance: for the issue's worked pair —

- SME A: $1k–$10k (meanlog 8.06, sdlog 0.70)
- SME B: $1M–$50M (meanlog 15.77, sdlog 1.19)

the pool is (meanlog 11.92, sdlog 0.94) → 90% range ≈ **$31k–$710k**, covering
*neither* expert and concentrating mass where both said the value isn't
(risk-understating). Since Epic B the pooled fit is stored natively and drives
Monte Carlo directly — the approximation is load-bearing.

## Decision record (owner, 2026-07-19)

**True mixture** (option 3 of 3). The single-lognormal alternatives were
rejected with hand-verified numbers, both recorded here as the why:

- *Log-space moment matching* (match the mixture of logs): σlog ≈ 3.98 → 90%
  range $215–$104M — covers both experts but inflates the implied mean loss
  ~55× above the true mixture mean (risk-OVERstating ALE under divergence).
- *Natural-space moment matching* (match E[X], Var[X] exactly): ALE exact, but
  90% range ≈ $210k–$27M — SME A's stated range vanishes (lognormal means are
  tail-dominated; the larger expert swamps the fit).

A finite mixture — each SME's fit a weighted component, the engine picking a
component per draw — is exact on ranges, moments, and multimodality
simultaneously. The cost is confined to the data contract (below).

**No feature flag** — hard replacement per the kill-dead-optionality doctrine;
the parameter-averaging path survives only in git history. Weights: equal by
default (`weights=None`); the existing `weights` parameter is preserved
(calibration-weighted pooling remains #18's future scope).

## 1. Pooling core (fair_cam)

`combine_lognorm_trunc` / `combine_norm` return mixture fits: new frozen types

- `LognormMixture(components: tuple[LogNormalTruncFit, ...], weights: tuple[float, ...])`
- `NormMixture(components: tuple[NormalTruncFit, ...], weights: tuple[float, ...])`

Weights normalized to sum 1 (±1e-9 validated). Single-SME pooling returns a
single-component mixture ≡ **exact identity** — no behavior change on the
dominant production path (one IRIS-baseline row per fieldset).

Methodology grounding: the **linear opinion pool** is the standard combination
rule for expert distributions (Clemen & Winkler 1999, "Combining Probability
Distributions From Experts in Risk Analysis", Risk Analysis 19(2), pp. 187-203;
lineage to Stone 1961). This is a **documented, justified departure from the
evaluator/collector R-oracle** — the port caveat in MD-1 closes with its own
citation chain. `_warn_if_divergent_fits` demotes WARNING → INFO: divergence is
now represented, not distorted.

## 2. PERT-collapse paths (most fieldsets — zero contract change)

Post-PERT-reversal, capped losses, TEF, and vulnerability are PERT-collapsed at
storage time. `lognormal_to_pert_approx` / `normal_to_pert_approx` gain
mixture-aware variants:

- `low = Q_mix(0.05)`, `high = Q_mix(0.95)` — numerical inversion of the
  mixture CDF `F(x) = Σ wᵢ Fᵢ(x)` by bisection over the component supports
  (deterministic, no sampling; component CDFs are the existing truncated
  closed forms).
- `mode`: single component takes a dedicated branch — closed-form
  `exp(meanlog − sdlog²)` then the EXISTING clamp machinery (byte-identical
  incl. `mode_clamp_reason`). Multi-component `raw_mode` = the UNCONSTRAINED
  global mixture-density argmax (candidates: every component's closed-form
  mode, including out-of-range ones, plus a log-grid over a widened bracket),
  THEN the existing `ModeClampReason` precedence applies — preserving the
  current clamp-reason semantics (plan-gate R2 reconciliation). A multimodal mixture's global mode is a documented representation
  choice — the PERT triple is already a summary shape, and low/high carry the
  union coverage that #27 demands (for the worked pair: low lands near SME A's
  low decile, high near SME B's high decile).

Stored JSON stays today's PERT triple byte-shape. **Scope of the fix on PERT
paths (Meth-I1):** this restores RANGE coverage (low/high span the experts'
union) — the headline #27 defect — but a unimodal PERT cannot represent a
bimodal mixture: for the worked pair the collapse still places ~13% of mass
in the inter-expert valley and does not preserve the mixture mean/median.
That residual is a documented, tested limitation of the summary shape;
EXACTNESS (ranges + moments + multimodality) is delivered only on the native
mixture path (§3). A divergent-experts collapse test pins the residual as a
known limitation, not a regression.

## 3. Native path (catastrophic losses only — the one contract change)

When `loss_shape = catastrophic` AND components > 1, store:

```json
{"distribution": "lognormal_mixture",
 "components": [{"mean": <meanlog>, "sigma": <sdlog>, "weight": <w>}, ...]}
```

(`mean`/`sigma` follow the existing native-lognormal key convention — the key
is `mean`, not `mu`.) Single-component mixtures store as today's plain
`lognormal` — wire format unchanged for the common case.

- **Engine**: `FAIRDistribution` gains mixture sampling — component index per
  draw via `rng.choice(len(w), p=w)`, then that component's lognormal. Exact
  mixture Monte Carlo; no approximation anywhere.
- **Validators** (`validate_fair_distributions` + fair_cam): per component,
  FINITENESS FIRST (`math.isfinite` on mean, sigma, weight — NaN passes range
  comparisons, the #306 corruption class), THEN `0 < sigma ≤ 10` and
  `weight > 0`; weights sum to 1 (±1e-9); 1 ≤ components ≤
  `MAX_SMES_PER_FIELDSET`. Rejection tests place the malformed component at a
  non-first index (iteration discipline).
- This is a **material adapter-surface change** → data-contract paranoid tier:
  4-reviewer plan-gate before code, per policy.

## 4. Consumer surfaces

- **Display** (scenario view, wizard preview): mixture rendered as a component
  list ("2 expert opinions, equal weight: …") — mixture-aware branch beside
  the existing lognormal rendering.
- **PDF**: same component-list rendering (pure-renderer conventions).
- **Import/export (#306 lineage)**: the IMPORTABLE mixture shape is exactly
  `{"distribution", "components"}` with each component exactly
  `{"mean", "sigma", "weight"}` — the anti-blob exact-key gate is never
  loosened. JSON export emits the stored dict verbatim (including
  `distribution_fit_metadata`, matching the scalar-lognormal precedent, which
  likewise does not JSON-re-import today — a pre-existing asymmetry, noted
  not propagated); round-trip guarantees are scoped to minimal metadata-free
  shapes. CSV flattens mixtures to derived p5/p95 like scalar lognormals;
  CSV import cannot express mixtures (JSON-only, documented).
- **Verification workbook**: mixture parity via a SECOND independent uniform
  stream (`u_sel`) doing cumulative-weight component selection, with the
  node's own `u` inverting the selected component — never one uniform for
  both roles (comonotonic coupling ≠ mixture; empirically +76% mean error on
  the worked pair). RANDARRAY bindings in the LET are unbounded, so the
  stream is a trivial addition (plan-gate verified). If plan-time spike shows the LET formula budget can't absorb it,
  workbook mixture-parity splits to a fast-follow with the gap **asserted in
  the parity test as an explicit expected-gap**, never silent.
- **Untouched**: qualitative converter (bands → PERT only), library entries
  (never mixtures), runs/reports (consume samples, not shapes — the executor
  maps distributions through `_dict_to_fair_distribution`, which gains the one
  new kind).

## 5. Legacy + provenance

- **No retroactive re-pooling**: already-finalized scenarios keep their stored
  parameters (their per-SME inputs are not durably retained post-finalize).
- `distribution_fit_metadata` gains `pooling_method: "linear_opinion_pool_v1"`;
  absent ⇒ implicitly `parameter_average_v0`. Schema-version bump per the
  existing metadata versioning convention.
- The divergence INFO log keeps firing (observability without alarm).

## 6. Testing spine

- **Worked-example pin** (the #27 A/B pair, anchors MC-verified at plan-gate):
  `Q_mix(0.05) == Q_A(0.10)` exactly (≈ $1,291 — in an equal pool the mixture
  reaches CDF 0.05 where the lower component alone reaches 0.10; B contributes
  ~2e-13 there) and `Q_mix(0.95) ≈ $32.4M` (= B's upper decile; loose bound
  > $15.2M). Native mixture mean equals the analytic `Σ wᵢ·exp(μᵢ + σᵢ²/2)`
  (**native path only** — the PERT collapse does not preserve it, see §2).
  All pinned side-by-side expected-vs-actual per the verification rule.
- **Identity pins**: single-SME mixture ≡ the old single-fit behavior exactly
  (PERT collapse byte-identical; native storage byte-identical plain
  lognormal).
- **Engine**: mixture sampling moments vs analytic (mean, variance via law of
  total variance) at fixed seed; component-selection frequencies vs weights.
- **Quantile inversion**: `Q_mix` vs brute-force empirical quantiles at 1e6
  samples, tolerance 1e-2 relative at a pinned seed (1e-3 is MC-flaky on tail
  quantiles — gate-measured; the deterministic bisection side converges to
  1e-10); bisection determinism + 200-doubling bracket cap.
- **R-oracle departure**: the existing oracle-parity test for `combine_*`
  updates to assert the DOCUMENTED departure (old expected values retired with
  the Clemen & Winkler rationale in the test docstring — an intentional
  re-pin, not a blind one).
- **Contract**: adapter iteration (N≥3 components preserved), ORM/DTO snapshot
  updates, validator rejection matrix (bad weights, sigma cap, component cap).

## Scope budget

- target_task_count: 9 (single PR: fair_cam mixture types + collapse math /
  engine sampling / validators / wizard_finalize integration / storage +
  metadata / display + PDF / import-export / workbook parity-or-asserted-gap /
  gate+docs)
- review budget: 4-reviewer plan-gate (iterate-to-zero; methodology persona
  Opus+max per the standing pin) + per-task methodology+spec reviews + full
  4-reviewer final PR-gate (fair_cam math change = automatic milestone tier).
- timeline budget: 1-2 working sessions.

## Scope drift log

- 2026-07-19 (design): single-lognormal options rejected WITH numbers (both
  distortions recorded above); mixture chosen. No-flag hard cut. Native
  mixture confined to catastrophic multi-SME; PERT paths solved by mixture
  quantiles with zero contract change. Workbook parity may split to an
  asserted-gap fast-follow (plan-time decision).
