# True Mixture Pooling Implementation Plan (#27 via #25)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace parameter-averaging SME pooling with a true linear-opinion-pool mixture, exact end-to-end: mixture quantile collapse for PERT-shaped fieldsets, native `lognormal_mixture` storage + engine sampling for catastrophic multi-SME losses.

**Architecture:** fair_cam grows mixture types + deterministic mixture quantile/mode math (pure functions, no sampling); the engine gains one sampling branch; the storage contract gains one shape confined to catastrophic multi-SME; every consumer surface branches beside its existing lognormal handling. Single-SME is identity everywhere by construction.

**Tech Stack:** fair_cam (scipy.stats.truncnorm CDFs already in-tree), numpy rng, existing validators/templates/exporters.

**Spec:** `docs/superpowers/specs/2026-07-19-mixture-pooling-design.md` — the decision record and §1-6 govern; the worked A/B pair (meanlog 8.06/σ 0.70 vs 15.77/1.19) is the canonical test vector.

## Global Constraints

- fair_cam math change ⇒ methodology reviewer on every task that touches `fair_cam/` or numeric semantics; 4-reviewer plan-gate + final PR-gate (milestone tier).
- Single-SME pooling ≡ EXACT identity on every path (PERT collapse byte-identical; native storage emits plain `lognormal`).
- Native mixture key convention: `{"distribution": "lognormal_mixture", "components": [{"mean", "sigma", "weight"}, ...]}` — `mean` = meanlog (NOT `mu`), matching the native-lognormal precedent.
- Weights normalized, sum 1 ± 1e-9; component count 1..`Settings.max_smes_per_fieldset` (config.py:149, default 20).
- No feature flag. No retroactive re-pooling. `distribution_fit_metadata.schema_version` 2 → 3 with `pooling_method: "linear_opinion_pool_v1"`.
- R-oracle departure is DOCUMENTED, not silent: parity tests rewritten with the Clemen & Winkler 1999 rationale in docstrings.
- All numeric tests print expected-vs-actual side-by-side per the verification-reporting rule.

---

### Task 1: Mixture types + pooling core (fair_cam)

**Files:**
- Modify: `fair_cam/quantile_pooling/_types.py` (types + demote divergence warning)
- Modify: `fair_cam/quantile_pooling/_lognormal.py:127-149` (`combine_lognorm_trunc`)
- Modify: `fair_cam/quantile_pooling/_normal.py:89-109` (`combine_norm`)
- Modify: `fair_cam/quantile_pooling/__init__.py` (exports)
- Test: `fair_cam/tests/quantile_pooling/test_mixture_pooling.py` (new), rewrite `test_combine_lognorm_trunc.py` sentinels, update `test_pooling_divergence_warning.py`

**Interfaces (Produces):**
- `@dataclass(frozen=True) LognormMixture: components: tuple[LogNormalTruncFit, ...]; weights: tuple[float, ...]` — `__post_init__` validates non-empty, len match, weights > 0, sum 1 ± 1e-9 (normalize in the combiner, validate here).
- `NormMixture` — same shape over `NormalTruncFit`.
- `combine_lognorm_trunc(fits, weights=None) -> LognormMixture`; `combine_norm(fits, weights=None) -> NormMixture`. `weights=None` ⇒ equal. Single fit ⇒ single-component mixture. Docstrings carry the Clemen & Winkler 1999 citation (Risk Analysis 19(2):187-203) + the explicit R-oracle-departure statement replacing the MD-1 port caveat.
- `_warn_if_divergent_fits` demoted `logger.warning` → `logger.info`, message updated ("divergence is represented by the mixture; informational").

- [ ] Steps (TDD): failing tests first — combiner returns mixture with normalized equal weights; explicit weights normalized; single-fit identity (`components == (fit,)`, `weights == (1.0,)`); weight validation raises on zeros/negatives/len-mismatch; divergence INFO (caplog level check). Rewrite `test_equal_weight_arithmetic_mean`/`test_nonuniform_weights` to assert the MIXTURE result with a docstring recording the intentional retirement of arithmetic-mean semantics; the R-oracle test (`test_combine_matches_r_oracle`) becomes `test_combine_departs_from_r_oracle_documented`: with the oracle fixture present, assert the mixture's components are the INPUT fits (the pool no longer collapses), and the docstring cites the spec decision record. Implement. Commit — `feat(fair_cam): linear opinion pool mixture types (#27)`

### Task 2: Mixture quantile + PERT collapse math (fair_cam)

**Files:**
- Modify: `fair_cam/quantile_pooling/_lognormal.py` (+`_plnormtrunc` CDF helper, `mixture_quantile_lognorm`, `lognormal_mixture_to_pert_approx`)
- Modify: `fair_cam/quantile_pooling/_normal.py` (normal counterparts)
- Test: `fair_cam/tests/quantile_pooling/test_mixture_collapse.py` (new)

**Interfaces (Produces):**
- `_plnormtrunc(x, meanlog, sdlog, min_support, max_support) -> float` — truncated-lognormal CDF (companion to the existing `_qlnormtrunc`, same truncnorm plumbing).
- `mixture_quantile_lognorm(mix: LognormMixture, p: float) -> float` — solves `Σ wᵢ Fᵢ(x) = p` by bisection on x; bracket = `[min_i Qᵢ(p·0.5), max_i Qᵢ(1-(1-p)·0.5)]` widened geometrically until it brackets; tolerance 1e-10 relative, deterministic (no sampling). Single component ⇒ delegates to `_qlnormtrunc` exactly.
- `lognormal_mixture_to_pert_approx(mix, q_low=0.05, q_high=0.95) -> tuple[PertTriple, ModeClampReason | None]`:
  - `low = mixture_quantile_lognorm(mix, q_low)`, `high = ...(mix, q_high)`.
  - `raw_mode` = UNCONSTRAINED argmax of the mixture density `Σ wᵢ fᵢ(x)` — candidates: each component's closed-form mode `exp(μᵢ - σᵢ²)` (including out-of-range ones) plus a 256-point log-spaced grid over the widened bracket (see binding amendment), then golden-section refine (±1e-9 relative), THEN the clamp machinery. Single component ⇒ reduces to the existing closed-form `exp(meanlog - sdlog²)` EXACTLY (assert byte-equality in tests).
  - Clamp precedence machinery identical to `lognormal_to_pert_approx:165-196` (support bounds = min/max over component supports).
- Normal counterparts: `_pnormtrunc`? (exists as scipy call — add if absent), `mixture_quantile_norm`, `normal_mixture_to_pert_approx` (mode grid linear-spaced; component modes = means).

- [ ] Steps (TDD), key vectors:
  - **Worked A/B pair pin** (spec §6): equal-weight mixture of (8.06, 0.70) and (15.77, 1.19) on [0, inf) — assert `Q_mix(0.05) < 1.1e3·1.05` (covers SME A's low anchor) and `Q_mix(0.95) > 16e6·0.95` (covers B's high anchor), each printed expected-vs-actual; assert the OLD averaged fit's [31k, 710k] range is NOT what we produce.
  - Quantile inversion vs brute force: 1e6-sample empirical quantiles at fixed seed within 1e-2 relative (per the binding amendment + spec §6 — 1e-3 is MC-flaky on tails).
  - Monotonicity: Q_mix strictly increasing over p grid.
  - Single-component byte-identity vs `_qlnormtrunc`/closed-form mode.
  - Bimodal mode: mixture with well-separated components picks the heavier component's peak.
  - Commit — `feat(fair_cam): mixture quantiles + PERT collapse (#27)`

### Task 3: Engine sampling (fair_cam)

**Files:**
- Modify: `fair_cam/risk_engine/fair_core.py:21-31` (DistributionType), `:34-133` (FAIRDistribution.sample), `~:295-326` (`_scale_distribution`)
- Test: `fair_cam/tests/risk_engine/test_mixture_sampling.py` (new)

**Interfaces (Produces):**
- `DistributionType.LOGNORMAL_MIXTURE = "lognormal_mixture"`.
- `FAIRDistribution.parameters` typing widens `dict[str, float]` → `dict[str, Any]` (the mixture stores `parameters={"components": [{"mean","sigma","weight"}, ...]}`; all existing kinds unchanged).
- Sampling branch: `idx = rng.choice(len(w), size=size, p=w)` then vectorized `rng.lognormal(mean_arr[idx], sigma_arr[idx])` (one draw pass, no python loop).
- `_scale_distribution` branch: currency scaling shifts EVERY component `mean += ln(mult)`, sigmas/weights unchanged (mirrors the plain-lognormal log-space shift).

- [ ] Steps (TDD): sampled mean vs analytic `Σ wᵢ·exp(μᵢ+σᵢ²/2)` and variance vs law-of-total-variance at 4e5 draws, fixed seed, 1% tolerance, printed side-by-side; component-selection frequencies vs weights (χ²-loose bound); single-component mixture sample-stream identical to plain lognormal at same seed (assert allclose); scale branch shifts analytic mean by exactly mult. Commit — `feat(fair_cam): lognormal mixture engine sampling (#27)`

### Task 4: Validators + structural shape

**Files:**
- Modify: `src/idraa/services/fair_cam_validation.py:70-105` (`_validate_finite` mixture branch)
- Modify: `fair_cam/validation/input_validator.py:629-649` (distribution_type allowlist)
- Modify: `src/idraa/services/scenario_import.py:70-99` (`_structural_dist_problem` mixture shape, `allow_lognormal` gate reused)
- Test: extend `tests/unit/test_fair_cam_validation.py` (or the file holding `_validate_finite` tests — locate by grep), `tests/unit/test_scenario_import*.py` structural cases

**Interfaces (Produces):** validation matrix — reject: missing/empty components, component missing a key, extra keys (exact-set check `{"mean","sigma","weight"}` per component + `{"distribution","components"}` top level), non-finite mean, `sigma <= 0 or > 10` (`_SIGMA_MAX` per component), weight `<= 0` or non-finite, `|Σw - 1| > 1e-9`, `len(components) > Settings.max_smes_per_fieldset` (import the settings accessor the file already uses; if none, read via `get_settings()`).

- [ ] Steps (TDD): the full rejection matrix + a passing well-formed 2-component case; structural import check accepts the exact shape and rejects blob-smuggling (extra key, nested extra). Commit — `feat(validation): lognormal_mixture shape + finiteness gates (#27)`

### Task 5: wizard_finalize integration + metadata

**Files:**
- Modify: `src/idraa/services/wizard_finalize.py` (`_FieldsetPipeline` collapsers L148-183, `PerFieldsetResult.pooled` type L258-282, `process_sme_estimates` L414-422, `build_scenario_payload` L435-554)
- Test: extend `tests/services/test_wizard_finalize*.py` (locate the existing finalize tests by grep)

**Interfaces:**
- Consumes T1/T2: combiners now return `LognormMixture`/`NormMixture`; collapsers = `lognormal_mixture_to_pert_approx` / `normal_mixture_to_pert_approx`.
- `PerFieldsetResult.pooled: LognormMixture | NormMixture`.
- `build_scenario_payload` branches:
  - collapsed (PERT) paths: unchanged shape, values from the mixture collapse.
  - catastrophic + `len(mix.components) == 1`: plain `{"distribution": "lognormal", "mean": c.meanlog, "sigma": c.sdlog, ...}` — byte-identical to today (identity pin).
  - catastrophic + `> 1`: `{"distribution": "lognormal_mixture", "components": [{"mean": c.meanlog, "sigma": c.sdlog, "weight": w}, ...], "distribution_fit_metadata": {...}}`.
  - `common_meta`: `schema_version: 3`, `pooling_method: "linear_opinion_pool_v1"`, `weights` = the REAL normalized weights (replacing the hardcoded `[1.0]*n` at L489), per-component `pooled_meanlog`/`pooled_sdlog` become lists `component_meanlogs`/`component_sdlogs` (single-element for n=1; keep the scalar keys TOO for n=1 back-compat with any reader — grep readers first; if none read them, drop scalars and say so in the commit).
- [ ] Steps (TDD): multi-SME catastrophic stores the mixture shape (assert exact dict); single-SME catastrophic byte-identical to a pre-change golden captured FIRST (capture before editing); multi-SME capped/tef/vuln produce PERT from mixture quantiles (assert vs direct T2 calls); metadata pins (schema_version 3, pooling_method, real weights). Commit — `feat(wizard): mixture pooling through finalize + metadata v3 (#27)`

### Task 6: Executor mapping + display + PDF

**Files:**
- Modify: `src/idraa/services/run_executor.py:118-150` (`_dict_to_fair_distribution` mixture branch)
- Modify: `src/idraa/app.py:537-571` (+`lognormal_mixture_display_rows` Jinja global: rows = p5/median/mean/p95 of the MIXTURE via `mixture_quantile_lognorm` on an untruncated-support mixture rebuilt from the stored dict + analytic mean, plus a per-component sub-list "n expert opinions, weights ...")
- Modify: `src/idraa/templates/macros/chart.html:16-91` (`pert_distribution_chart` gains the `lognormal_mixture` branch beside the `lognormal` one)
- Modify: `src/idraa/services/pdf_report.py:2163+` (`_draw_distribution_table` `LOGNORMAL_MIXTURE` branch → mixture percentile table via the same math; reuse `_Z_P*` NOT — mixture percentiles are numeric, not z-based; call the fair_cam mixture quantile with untruncated supports)
- Test: extend the view/PDF tests (grep `lognormal_display_rows` + `_draw_distribution_table` test files)

- [ ] Steps (TDD): executor maps the stored mixture dict to a sampling-correct `FAIRDistribution` (analytic-mean check at fixed seed); view.html renders component count + p5/p95 for a seeded mixture scenario (integration render assert); PDF branch smoke (existing PDF test harness pattern); plain-lognormal rendering byte-unchanged (regression pin). Commit — `feat(ui): mixture rendering across view, PDF, executor (#27)`

### Task 7: Import/export

**Files:**
- Modify: `src/idraa/services/scenario_export.py:61-77` (`_dist_cells`: mixture flattens to p5/p95 via mixture quantiles, mode blank — mirroring the lognormal flatten), `:109-166` (JSON `_normalize_dist` passes the mixture through verbatim — verify + test, code change only if it mangles)
- Modify: `src/idraa/services/scenario_import.py` (JSON import accepts the shape — T4 did the structural check; wire the `allow_lognormal` gate to also govern mixtures)
- Docs note in both modules: CSV IMPORT cannot express mixtures (JSON-only) — a CSV row remains a single lognormal; state it in the CSV template docstring.
- Test: round-trip test — export JSON of a mixture scenario → re-import → identical stored dict; CSV export flatten pins.

- [ ] Steps (TDD). Commit — `feat(io): mixture JSON round-trip + CSV flatten (#27)`

### Task 8: Workbook parity — two-uniform mixture or asserted gap

**Files:**
- Modify: `src/idraa/services/verification_workbook_let.py:55-181` (`_invcdf`, `scaled_params`)
- Test: extend `tests/services/test_verification_workbook_let.py`

**Decision rule (BINDING):** a mixture must NOT reuse one uniform for both component selection and inversion (comonotonic coupling ≠ mixture). Inspect the LET generator: if a SECOND independent uniform stream per mixture node is emittable within the existing column budget (read how `u` columns are allocated), implement `_invcdf` mixture as nested cumulative-weight IFs on `u_sel` choosing the component whose `EXP(NORM.INV(u, mean_i, sigma_i))` formula applies. If the plumbing cannot cleanly supply `u_sel`, implement the ASSERTED GAP instead: `_invcdf` raises `NotImplementedError("lognormal_mixture: workbook parity tracked in <new issue>")`, the workbook builder SKIPS mixture scenarios with a visible sheet note, the parity test asserts the raise (explicit expected-gap, never silent), and file the follow-up issue in the same commit (`gh issue create`). Either way `scaled_params` handles mixtures (shift every component mean by `ln(mult)`).

- [ ] Steps (TDD per chosen path; report which path + why). Commit — `feat(workbook): mixture parity or asserted gap (#27)`

### Task 9: Gate + docs + goldens sanity

- [ ] Confirm no golden re-baseline needed: grep ensemble/weight-robustness golden tests for stored distribution shapes (mixtures only arise from NEW multi-SME finalizations; existing goldens carry none — assert by inspection, state in commit).
- [ ] Spec drift-log entry (T5 metadata decisions, T8 path taken); `docs/reference/` methodology note if the repo keeps one for pooling (grep; skip if none).
- [ ] FULL gate FOREGROUND (`uv run python scripts/run_local_gate.py`) — all steps green.
- [ ] Commit — `docs(design): mixture pooling execution drift-log (#27)`

---

## Final

Branch `feat/27-mixture-pooling` off current main, worktree per the concurrent-sessions convention. 4-reviewer final PR-gate (methodology Opus+max mandatory) before merge; PR body carries the worked-pair before/after table. Closes #27 AND #25.

## Plan-gate round-1 amendments (BINDING — override conflicting text above)

**Task 1**
- ALSO rewrite `fair_cam/tests/quantile_pooling/test_combine_norm.py` with the same intentional-re-pin treatment: `test_combine_matches_r_oracle` (asserts `.mean`/`.sd` L62-63), `test_equal_weight_arithmetic_mean` (L90-91), `test_nonuniform_weights` (L103-105), `test_pooled_mean_outside_support_acceptable` (L108) all read the retired scalar shape (Spec-I2).

**Task 2 (Meth-B1/I2/I5, Sec-I3 — the math contract)**
- Worked-pair pin CORRECTED: assert `Q_mix(0.05) == pytest.approx(_qlnormtrunc(0.10, 8.06, 0.70, 0, inf), rel=1e-9)` — the exact identity (equal pool reaches CDF 0.05 where the lower component reaches 0.10; ≈ $1,291) — plus `Q_mix(0.95) > 15.2e6` (loose bound; actual ≈ $32.4M = B's upper decile). Print expected-vs-actual. The earlier `< $1,155` bound was arithmetically WRONG (MC-verified at gate) — do not "fix" the math to satisfy it.
- MC brute-force comparison tolerance: 1e-2 relative at 1e6 draws (1e-3 is flaky on tail quantiles — gate-measured 62-72% seed-failure rate). Fixed seed, printed side-by-side.
- Mode algorithm, precisely: `len(components) == 1` takes a DEDICATED branch computing `raw_mode = exp(meanlog - sdlog**2)` (closed form) and running the EXISTING clamp-precedence machinery — guaranteeing byte-identity of mode AND `mode_clamp_reason` (σ ≥ 1.645 single-SME cases clamp to low with `MODE_BELOW_PERT_LOW` exactly as today). Multi-component: `raw_mode` = UNCONSTRAINED global mixture-density argmax — candidates are every component's closed-form mode (including out-of-[low,high] ones) plus a 256-point log-grid over the bracket `[min_i mode_i / 4, max_i mode_i * 4] ∪ [low, high]`, golden-section refined — THEN the existing clamp machinery applies (so clamp reasons keep their current semantics for mixtures too).
- Bracket widening in `mixture_quantile_lognorm`: HARD CAP 200 doublings, raise `ArithmeticError` with the component params on non-convergence (fail-loud; a finite-but-huge meanlog must not spin the render path).
- ADD `test_divergent_pert_collapse_documented_limitation`: for the worked pair, pin the collapse's inter-expert valley mass (P[$20k,$500k] ≈ 0.129 vs mixture 0.009) and the median ratio — collapse-PERT median ÷ true-mixture median ≈ 66× (PERT $3.63M ÷ mixture $55.0k; gate-derived, NOT ~27× which only appears against the retired averaged fit) — as KNOWN limitation values with the Meth-I1 rationale in the docstring — a documentation pin, not a regression bar.

**Task 3 (Meth-I3/I4 + Arch-NTH)**
- Single-component mixtures take a DEDICATED sampling branch that bypasses `rng.choice` and calls `rng.lognormal(mean, sigma, size)` directly — this makes the sample-stream-identity test valid AND keeps the engine stream byte-identical for any 1-component mixture. State in code comment why (rng.choice advances the generator).
- Variance test: verify the law-of-total-variance FORMULA analytically (exact arithmetic assert), and bound the EMPIRICAL variance at 7% relative at 4e5 draws with a pinned seed verified green (1% is unrealistic — 4th-moment domination, gate-measured). Mean stays at 1% with the pinned seed. Component-selection frequencies within ±3σ binomial bounds.
- Also widen the LOCAL annotation `new_params: dict[str, float]` in `_scale_distribution` (fair_core.py:285) alongside the field typing.

**Task 4 (Sec-B1 — BLOCKER — + Sec-N2, Spec-N)**
- Validation order per component is FINITENESS FIRST: `math.isfinite(mean)`, `math.isfinite(sigma)`, `math.isfinite(weight)` BEFORE any range comparison (`NaN <= 0` and `NaN > 10` are both False — a NaN sigma passes range checks and corrupts Monte Carlo; the #306 class). Mirror the scalar branch's finite-first ordering exactly.
- Rejection-matrix tests place the malformed component at the LAST index of a 3-component mixture (proves per-component iteration, not components[0]-only).
- `input_validator.py:639` allowlist edit is defense-in-depth ONLY — the enforcing gate is v3-side (`_validate_finite` + `_structural_dist_problem`); state so in the code comment.

**Task 5 (Arch-I2/I3, Meth-I6, Spec-I3)**
- `common_meta` support fields: derive `pooled_min_support`/`pooled_max_support` from `r.pooled.components[0].min_support/.max_support` (support is per-fieldset-uniform by construction; assert all components share it).
- Identity-pin SCOPE: single-SME byte-identity applies to the DISTRIBUTION dict (mean/sigma or low/mode/high) — the `distribution_fit_metadata` sidecar intentionally changes for everyone (schema_version 3, pooling_method, normalized weights [1.0] not [1.0]*n... for n=1 both are [1.0]; for n≥2 REAL normalized weights e.g. [0.5, 0.5]). Capture the pre-change golden of the distribution dict only.
- ADD to the Files list and rewrite deliberately (not blind): `tests/services/test_wizard_finalize.py:101-107` (`.pooled.sdlog` → `.pooled.components[0].sdlog`), `tests/contracts/test_scenario_distribution_fit_metadata_roundtrip.py:96-160` (construct `PerFieldsetResult(pooled=LognormMixture(...))`; the vuln case at L159 uses `NormMixture(...)`), `tests/contracts/test_sme_estimate_persist_iteration.py` (constructs `pooled=LogNormalTruncFit(...)` at L88 — wrap in a single-component `LognormMixture`; construction change, not just typing).
- ADD `src/idraa/routes/scenarios.py:2311-2314` to the Files list: the finalize route's `per_fieldset_pooling_summary` uses `getattr(r.pooled, "meanlog", None)` — silently None for mixtures, degrading the audit trail. Replace with component-aware summary (`component_meanlogs`/`component_sdlogs` lists + weights); assert in a test that a multi-SME finalize audit row carries non-null pooling summary.

**Task 7 (Sec-I1 / Spec-I1 — round-trip contradiction)**
- The importable mixture shape is EXACTLY `{"distribution", "components"}` (components exactly `{"mean","sigma","weight"}`) — the anti-blob gate is NEVER loosened to admit `distribution_fit_metadata`.
- The round-trip test uses a MINIMAL metadata-free mixture (hand-authored import → export → re-import → identical). The metadata-carrying JSON export does not re-import — a PRE-EXISTING scalar-lognormal asymmetry (verified at gate), noted in the module docstring and NOT propagated as a new guarantee; do not change scalar behavior in this PR.

**Task 8 (Arch answer + Sec-I2/Arch-I4)**
- The uniform-stream question is ANSWERED: the LET binds `RANDARRAY({n},1)` columns without a budget (verification_workbook_let.py:373-376) — implement the TWO-UNIFORM path (`u_sel` per mixture node; nested cumulative-weight IFs selecting which `EXP(NORM.INV(u, mean_i, sigma_i))` applies; CRN parity holds by reusing both streams between base and residual). The asserted-gap fallback applies ONLY if implementation surfaces a concrete blocker — report it, don't silently choose it.
- `_assert_numeric_dist` (verification_workbook_let.py:200-217) currently raises TypeError on the `components` list BEFORE `_invcdf` is reached. Extend it to RECURSE into `components` asserting each `mean`/`sigma`/`weight` is int|float (non-bool) — do NOT merely exempt the container key (that would disable the Sec-N1 formula-injection guard for nested params).
- `scaled_params` emits the `lognormal_mixture` shape with every component mean shifted by `ln(mult)`.

**NTH dispositions:** component-cap coupling to `max_smes_per_fieldset` accepted + noted in the validator comment (Sec-N1); numeric-only display pin added to T6 tests (Sec-N3); legacy-divergent-scenario banner deferred — file a follow-up issue at T9 (Meth-N3).

## Scope budget

- target_task_count: 9 (single PR)
- review budget: 4-reviewer plan-gate (iterate-to-zero) + per-task methodology+spec reviews (methodology on every fair_cam-touching task) + 4-reviewer final PR-gate
- timeline budget: 1-2 working sessions

## Scope drift log

- 2026-07-19: plan drafted from the approved spec; workbook two-uniform-vs-asserted-gap left as a plan-time BINDING decision rule for the implementer (spec §4 sanctioned).
- 2026-07-19: `FAIRDistribution.parameters` typing widens to `dict[str, Any]` for the mixture's nested components (survey: currently `dict[str, float]`) — engine-internal, no wire change.
- 2026-07-19: metadata scalar keys (`pooled_meanlog`/`pooled_sdlog`) → component lists; scalar back-compat decided by reader grep at T5 (no known readers).
