# Product-Form LEF×LM Tail Approximation

**Issue #412 — known-limitation disclosure (2026-06-23)**

> **THIS IS NOT A BUG, AND NOT A DEVIATION FROM THE FAIR STANDARD.** The native
> engine combines Loss Event Frequency (LEF) and Loss Magnitude (LM) per
> iteration as a **product** (`risk = lef * loss_magnitude`). That is the
> **faithful canonical FAIR / pyfair per-iteration form** — the same form pyfair
> uses for its `Risk` column. The engine is correct against the Standard.
>
> What this document discloses is a **known statistical property** of that
> faithful model: the headline **MEAN is exact**, but **every TAIL statistic is
> an approximation** whose error grows once a scenario's LEF exceeds 1. The gap
> here is **disclosure**, not correctness — we owe analysts an honest account of
> how to read tail numbers for high-frequency scenarios. No engine, math, or UI
> change is made by this document.

---

## The Limitation

For each Monte Carlo iteration the engine computes a single annualized-loss
value as the **product** of that iteration's LEF and that iteration's LM. It
does **not** draw a discrete event count `N` for the year and sum `N`
independent loss draws (a true *compound* sum). The product form is exact in
expectation — so the reported ALE mean is unbiased — but it **mis-shapes the
tail** of the loss distribution for scenarios whose annual loss-event frequency
is materially above one event per year.

Concretely:

- The **mean** (ALE) is exact. The headline number every report leads with is
  not an approximation.
- The **tail** statistics — VaR at the 90/95/99/99.9 percentiles, Expected
  Shortfall (es_95/es_99/es_999), the loss-exceedance curve, and the two-sided
  p2.5/p97.5 percentile band — are **all approximations** read off the
  product-form array. When LEF < 1 the distortion is negligible. The error
  grows once LEF exceeds 1.

---

## Why: the combination is a product, not a sum

The per-iteration combination lives in the native engine.

`fair_cam/risk_engine/fair_core.py`, lines 442–448:

```python
# Calculate Loss Event Frequency (LEF)
lef = samples["tef"] * samples["vulnerability"]

# Calculate Loss Magnitude (LM)
loss_magnitude = samples["primary_loss"] + samples["secondary_loss"]

# Calculate Risk (Annual Loss Expectancy)
risk = lef * loss_magnitude
```

- `lef` (line 442) is itself a product of the Threat Event Frequency and
  Vulnerability samples — the standard Open FAIR `LEF = TEF × Vuln` derivation.
- `loss_magnitude` (line 445) is the sum of Primary and Secondary Loss samples —
  the standard `LM = PL + SL` derivation.
- `risk` (line 448) is the **element-wise product** `LEF × LM`.

This is the **canonical FAIR / pyfair per-iteration form**. pyfair's `Risk`
column is computed the same way, and v3 deliberately mirrors it (the native
engine epic #324 reproduces pyfair's node algebra, not a new one). Nothing here
is a v3 invention or a deviation.

---

## Why: the mean is exact

Under per-iteration independence of LEF and LM:

```
E[risk] = E[LEF × LM] = E[LEF] · E[LM]
```

This is the product form's one exact guarantee. Framed against the compound
process underlying FAIR — a year produces `N` loss events (a frequency process)
each of severity `X` (a magnitude process) — **Wald's identity** gives the
expected total annual loss as:

```
E[total annual loss] = E[N] · E[X]
```

With `E[N] = E[LEF]` (expected events per year) and `E[X] = E[LM]` (expected
loss per event), the product-form mean `E[LEF] · E[LM]` **equals** the true
compound-process mean. So the reported ALE mean is **unbiased** — there is no
approximation in the headline number.

`fair_cam/risk_engine/fair_core.py`, line 469:

```python
"ale_mean": np.mean(risk),
```

`np.mean(risk)` is the sample estimate of `E[LEF × LM]`, i.e. the unbiased ALE.

---

## Why: every TAIL statistic is an approximation

The product form is **not** a draw from the true distribution of annual total
loss. Wald's identity pins the *mean* of a compound sum, but says nothing about
its higher moments or tail shape. A real high-frequency year is the sum of `N`
**independent** loss draws — which, by the central-limit tendency of a sum,
concentrates differently in the tail than a **single** loss draw scaled by a
frequency multiplier. The product form does the latter: it treats each
iteration as "one event's worth of loss, scaled by that iteration's LEF." That
is exact in expectation but the **wrong shape** in the tail once `LEF > 1`,
because:

- A true multi-event year (`N ≥ 2`) sums multiple independent severity draws;
  extreme-high and extreme-low draws partially average out, so the realized
  total has a **tighter, differently-shaped** tail than `(single LM draw) ×
  LEF`.
- The product form instead **stretches a single severity draw** by the
  frequency factor, which can both over- and under-state tail percentiles
  relative to the true compound sum, with the discrepancy widening as LEF rises
  above 1.
- When `LEF < 1` (most loss events are rare, fractional-frequency scenarios)
  the product and the compound sum are nearly indistinguishable — the
  distortion is negligible. The approximation error is therefore **concentrated
  in the high-frequency regime**.

### Which stored statistics are affected, and where they are produced

All of the following are descriptive statistics read off the **same**
product-form array. In production that array is
`enhanced.residual_risk` (a fair_cam `FairResult` whose `simulation_results` IS
the `lef * loss_magnitude` array from `fair_core.py:448`). The v3 view-model
builders live in `src/idraa/services/run_executor.py`:

| Stored statistic | Builder (`run_executor.py`) | Reads |
|---|---|---|
| `var_90`, `var_95`, `var_99`, `var_999` | `_build_tail_metrics` | `enhanced.residual_risk` |
| Expected Shortfall `es_95` / `es_99` / `es_999` | `_build_tail_metrics` | `enhanced.residual_risk` |
| Two-sided central-95% band `p2.5` / `p97.5` | `_build_loss_percentile_band` | `enhanced.residual_risk` |
| Loss-exceedance curve (LEC) | `_build_loss_exceedance_curve` | `enhanced.residual_risk` |

Because every one of these is `np.percentile(...)` / a conditional mean over the
product-form array, they all inherit the tail-shape approximation. The **mean**
is the only stored statistic exempt (it is exact, per the section above).

> **Note on reading the deepest tail.** Independently of this product-form
> caveat, `var_999` / `es_999` are also subject to a **sampling-noise** caveat
> already documented in `_build_tail_metrics` (at the 10k-iteration form default
> the p99.9 tail holds only ~10 samples). The two caveats stack for
> high-frequency scenarios: treat the deepest tail of a high-LEF scenario as
> indicative, not precise.

---

## Real exposure in the seeded library

How much does this matter for the shipped seed library? Originally verified
against `data/seed_library_entries.json` + `data/seed_library_entries_extension.json`
(85 entries total) on 2026-06-23; **counts below are re-derived as-of the
current seed data** (library growth since 2026-06-23 changed the totals, not
the method):

| Quantity | Value (as of current seed) |
|---|---|
| Total seed scenarios | 102 |
| Scenarios with **TEF mode > 1** | 40 (**39.2%**) |
| **Maximum** TEF mode anywhere | **20** — "Telecom SIM-Swap Fraud — Carrier-Liability Account Takeover" |
| "Credential Stuffing — Consumer-Facing Portal Account Takeover" TEF mode | 5 |

> These numbers **correct** the originating review, which overstated the
> exposure as "52% of 44 scenarios" with a "max TEF mode of 50." The
> 2026-06-23 verified figures were **42.4% of 85** and a **max mode of 20**;
> re-derived as-of the current 102-entry library the figure is **39.2% of
> 102**, still a max mode of 20. (TEF mode is the LEF's upper component —
> `LEF = TEF × Vuln` — so realized LEF is typically *below* TEF mode once
> Vulnerability < 1; TEF mode > 1 is an upper-bound proxy for "in the
> high-frequency regime," not a literal LEF.)

Reading: a **material minority** of seed archetypes sit in the regime where the
tail approximation's error is non-trivial, so the disclosure matters. But the
exposure is **bounded** — the highest frequency anywhere in the library is a
mode of 20 events/year, not an extreme value, so no shipped archetype lives deep
in the high-error tail.

---

## Future work (OUT OF SCOPE for this PR)

This document is **disclosure only**. The following are recorded here as
roadmap; neither is implemented by this change:

1. **LEF-p95 caveat flag (UI).** Surface a per-scenario caveat in the UI when a
   scenario's modeled `LEF p95 > 1`, marking that scenario's tail statistics as
   "frequency-regime: tail is an approximation." This would route the disclosure
   to exactly the scenarios where it matters, at run-display time.

2. **Optional compound-sampling mode (engine).** An opt-in simulation mode that
   draws a discrete annual event count `N ~ Poisson(LEF)` per iteration and sums
   `N` independent Loss-Magnitude draws — a **true compound sum** that is
   tail-accurate, at the cost of more samples per iteration. This would be an
   alternative to (not a replacement for) the canonical product form.

Both are **deferred** and intentionally not built in this PR.

---

## See Also

- `docs/reference/vulnerability-semantics.md` — semantics of the `vulnerability`
  field that feeds `LEF = TEF × Vuln` (the LEF factor in the product above).
- `docs/reference/fair-cam-methodology.md` — the canonical FAIR / FAIR-CAM
  methodology reference (see its "Known limitations" pointer).
- `fair_cam/risk_engine/fair_core.py` — the per-iteration LEF (line 442), LM
  (line 445), and `risk = lef * loss_magnitude` product (line 448); `ale_mean`
  (line 469).
- `src/idraa/services/run_executor.py` — the v3 view-model tail builders
  `_build_tail_metrics`, `_build_loss_percentile_band`, and
  `_build_loss_exceedance_curve`, all reading `enhanced.residual_risk`.
- `docs/reference/control-weight-robustness.md` — the weight-uncertainty
  robustness feature; note the mean/median gap (`exp(σ²/2)`) that separates
  the representative-value control ranges from the MC-mean headline.
