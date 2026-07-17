# Loss representation: capped PERT default + curated catastrophic lognormal

Library `primary_loss` / `secondary_loss` are stored per the entry's
**`loss_shape`** class (Milestone B, spec
`docs/superpowers/specs/2026-07-09-loss-pert-overhaul-design.md`):

- **`capped` (83 entries, the default):** bounded **PERT**
  `{distribution: "PERT", low, mode, high}` — the `high` IS the economic
  ceiling. Bounding happens through the distribution choice, never a runtime
  clip (a clipped lognormal dumps tail mass into an artifact spike at the cap).
- **`catastrophic` (10 entries, owner-curated shortlist):** uncapped native
  lognormal `{distribution: "lognormal", mean, sigma}` — intentionally
  unbounded (criteria: plausible physical-safety/loss-of-life outcome, or
  nation-state systemic compromise no sector p95 credibly bounds; magnitude
  alone never qualifies). Shortlist + criteria live in spec §3.

`loss_shape` is INDEPENDENT of `loss_tier` (citation quality) — see the
superseding note in `loss-magnitude-tiering.md`.

## Conversion rule (capped entries)

Each capped entry's Epic-D lognormal `{mean: μ, sigma: σ}` converted
mechanically (builder `scripts/build_loss_pert_conversion.py`, content
migration `d9e5a3c7f2b4`):

```
low  = exp(μ − Z·σ)      Z = 1.6448536269514722   # p5
high = exp(μ + Z·σ)                               # p95 — the cap
mode = exp(μ − σ²) clamped up to low               # analytic lognormal mode
```

**`mode == low` for ALL 83 capped entries** — the clamp fires whenever
σ > 1.645, and the library's σ range is 1.838–3.472. The engine
(`fair_core`, pyfair-matched Vose moment form, γ=4) realizes this as
**Beta(2/3, 10/3)**: α < 1, so the density *rises toward the low bound*;
mean = `(5·low + high)/6`.

**Expected-loss impact (documented calibration-philosophy change):** the p95
cap removes the tail that carried the lognormal mean, so capped entries no
longer reproduce the IRIS envelope mean:

| sector (σ) | LN mean | PERT mean | ratio |
|---|---|---|---|
| energy_utilities (1.838) | $790k | $506k | 1.6× |
| healthcare (1.960) | $3.80M | $2.35M | 1.6× |
| manufacturing (2.272) | $13.2M | $7.0M | 1.9× |
| financial_services (3.203) | $169M | $32.3M | 5.2× |
| technology_saas / telecom (3.472) | $298M | $36.2M | 8.2× |

Rationale (owner ruling 2026-07-08): the tail mass above p95 was economically
impossible for a single org, so the envelope mean was inflated by impossible
values. The envelope citations remain provenance for the (p5, p95) pair and
σ; `loss_form_profile` shares remain provenance of how the envelope was
split. The guard
`tests/integration/test_library_loss_differentiation.py::test_loss_params_reconstruct_from_envelope_and_shares`
pins that every capped PERT reconstructs from the SAME envelope×share
lognormal via these formulas — the citation chain survives the conversion.

## Wizard authoring

- **`WizardState.loss_shape`** (`"capped"` default) is seeded from the library
  entry at selection and overridable via the step-4 toggle ("Catastrophic —
  uncapped loss magnitude"), applying to BOTH PL and SL.
- **Capped (default):** pl/sl use the same lognormal→PERT collapse TEF uses
  (`_fit_lognorm_native` → `combine_lognorm_trunc` → `lognormal_to_pert_approx`,
  the `_LOGNORMAL_TO_PERT_PIPELINE`), stored as PERT with the 15-key hybrid
  sidecar (lognormal-fit provenance + mode-clamp fields). The wizard mode is
  the analytic lognormal mode `exp(meanlog − sdlog²)`, clamping to `low` only
  for very wide anchors (`high/low > e^(2Z²) ≈ 224`).
- **Catastrophic:** pl/sl keep the Epic-B native-lognormal pipeline
  (13-key sidecar), uncapped by intent.
- Storage dispatches on **`PerFieldsetResult.collapsed`** (set from the
  chosen pipeline), NOT the static fieldset registry — shape is per-scenario.
- **%-of-revenue hint:** display-only text next to the pl/sl `high` inputs
  when capped and `org.annual_revenue` is set. No validation, no scaling
  (org-revenue loss SCALING was removed by #517 and is not reintroduced).

## Legacy scenarios

Scenarios snapshot their distributions at finalize, so pre-Milestone-B
scenarios keep their stored lognormal PL/SL until re-authored (snapshot
pattern, same as Milestone A's TEF). Import stays permissive (PERT and
lognormal loss nodes both accepted). TEF/vuln are governed by
`tef-representation.md` / the vuln PERT convention and are unchanged here.
