# Wizard Re-elicitation for Existing Scenarios — Design (idraa#56)

Owner-approved 2026-07-19 (design presented and approved in-session; eligibility
and provenance forks decided by owner: **all scenarios** eligible, provenance
**flips to expert judgment**).

## Problem

The estimation wizard is create-only. Once a scenario is finalized, the only
way to change its estimates is the flat edit form — which, since #27, warns
that saving a pooled multi-SME scenario replaces the mixture with a single
lognormal. There is no path to *re-elicit*: to run the SME quantile process
again over an existing scenario and replace its estimates with a properly
pooled result. Register-imported scenarios (epic #34) have the same gap from
the other direction: their band-derived estimates deserve an upgrade path to
real expert elicitation.

Key enabler discovered during design: the raw per-SME answers **survive
finalize**. `scenario_sme_estimates` (one row per fieldset × SME:
`scenario_id, fieldset, sme_id XOR sme_name, low, high, recorded_at,
recorded_by`) is written by `persist_estimates` but has **no reader** today.
A re-estimation wizard can fully rehydrate its SME rows for wizard-born
scenarios.

## Decisions (owner-approved)

1. **Eligibility: all scenarios**, any source, any status (DRAFT or ACTIVE).
   Wizard-born scenarios rehydrate their saved SME rows; imported/form-built
   scenarios start with empty SME rows but keep name, taxonomy, and controls
   pre-filled. The register-import upgrade is a first-class use case.
2. **Provenance: flip to expert judgment.** On finalize, `source` becomes
   `EXPERT_JUDGMENT` and `library_pin` is cleared — the estimates now come
   from SMEs, not a library entry, so re-curation sweeps must not claim or
   overwrite them. ATT&CK mappings are retained (they describe the attack
   method, not the estimates).

## Architecture

The wizard's step flow, templates, pooling pipeline (`process_sme_estimates`
→ `build_scenario_payload` → `persist_estimates`), and draft-state machinery
are reused unchanged. The feature adds three seams:

1. **Entry + seeding** — `POST /scenarios/{scenario_id}/re-estimate`
   (analyst/admin, CSRF) seeds a `wizard_drafts` row from the scenario and
   303-redirects to `GET /scenarios/new/wizard/step/2?tx={tx}`. Step 1
   (library pick) is skipped: provenance flips to expert judgment regardless.
2. **State targeting** — `WizardState` gains two optional fields (defaults
   `None`, so in-flight legacy drafts deserialize unchanged):
   - `target_scenario_id: str | None` — hex UUID of the scenario being
     re-estimated;
   - `target_expected_row_version: int | None` — the scenario's
     `row_version` captured at seed time (the same optimistic-lock primitive
     the edit form uses, carried through the wizard's lifetime).
3. **Finalize branch** — when `target_scenario_id` is set, `finalize_wizard`
   calls a new `ScenarioService.update_from_wizard(...)` instead of
   `create_from_wizard(...)`. Everything upstream of that call (pooling,
   payload build, draft locking) is shared.

### Seeding (`seed_wizard_state_from_scenario`)

New pure function in `services/wizard_state.py` (or sibling module) building a
`WizardState` from a loaded `Scenario` + its SME rows:

- `current_step = 2`; fresh `tx_id`; `target_scenario_id = scenario.id.hex`;
  `target_expected_row_version = scenario.row_version`.
- Descriptive: `name, description, threat_category, threat_actor_type,
  asset_class, attack_vector` copied from the scenario. (No `tags` —
  `Scenario` has no tags column; `WizardState.tags` is a dead field.)
- `mitigating_control_ids` from current links.
- `loss_shape`: `"catastrophic"` if the stored primary-loss node's
  `distribution` is `lognormal` or `lognormal_mixture`, else `"capped"`.
  (A storage invariant for wizard-born scenarios since #326/#27:
  catastrophic ⇔ native lognormal family on pl. For form/import-built
  scenarios it is a reasonable default, not an invariant — an imported
  native-lognormal pl seeds "catastrophic" — and it is operator-editable
  at step 4 either way.)
- `sme_estimates`: rehydrated from `scenario_sme_estimates` rows for this
  scenario — `{fieldset: [{sme_id | sme_name, low, high}]}` preserving the
  XOR identity shape the wizard steps already consume. Empty dict when the
  scenario has no rows (imports, form-built).
  **Legacy-residual exception (plan-gate Meth-B1):** when
  `scenario.vuln_framing == "legacy_residual"`, the `vuln` fieldset is NOT
  rehydrated (tef/pl/sl only). Those vuln rows were elicited under the
  pre-#339 residual wording and embed a control discount; rehydrating them
  and stamping `"inherent"` at finalize would clear the #339 double-count
  safeguard while the values still carry the discount — silently
  understating risk. Excluding them forces fresh vuln elicitation under the
  inherent copy, making the finalize stamp truthful by construction;
  `process_sme_estimates` fails closed if vuln is left empty.
- Library fields (`library_entry_id/version`, `override_id/version`) stay
  `None` — no library pin is involved in a re-estimation.
- The elicited distributions themselves are NOT copied into
  `threat_event_frequency`/etc. of the state; those state fields remain the
  wizard's working values and are produced by the pooling pipeline at
  finalize exactly as in the create flow.

The route handler wraps this in the existing draft-persistence machinery
(`wizard_state.py` save path) with org stamping from the current user.

### Finalize (`ScenarioService.update_from_wizard`)

Mirrors `create_from_wizard`'s validation posture but updates in place, and
mirrors `ScenarioService.update`'s concurrency/audit conventions:

- SELECT ... FOR UPDATE the scenario row; 404-with-message if deleted
  mid-flight; organization match enforced (same posture as the finalize
  route's draft org check).
- Optimistic lock: `scenario.row_version != state.target_expected_row_version`
  → `ScenarioVersionConflictError` → the finalize route re-renders step 6
  via the existing `_render_review_with_flash` helper (HTTP 422, the
  established finalize-error idiom — amended from the draft's 409 at
  plan-gate Spec-I2 for consistency with every other finalize error), with
  the message "this scenario was edited while you were estimating — start
  a fresh re-estimate from the scenario page". Draft preserved (NOT
  deleted) so the user can review their entries, then cancel. A
  `NotFoundError` from the locked re-resolve (scenario deleted in the
  race window) is caught by the same dispatch and routed to the same
  422 flash (plan-gate Arch-N2).
- Applies: the four distribution nodes + `distribution_fit_metadata` from
  `build_scenario_payload`; descriptive fields from state; `loss_shape`
  consequences are already encoded in the payload (capped→PERT,
  catastrophic→native, per #27 single-vs-mixture rules).
- Provenance: `source = EXPERT_JUDGMENT`; `library_pin = None`;
  `conversion_metadata = None` (the register-origin sidecar is the same
  provenance class as the pin — estimates no longer derive from the
  conversion; the historical record stays in the audit log); ATT&CK
  mappings untouched.
- `vuln_framing`: set to `"inherent"` (the model default/standard). The
  stamp is truthful by construction BECAUSE seeding never rehydrates vuln
  rows from a `legacy_residual` scenario (see the seeding exception above):
  every vuln value that reaches this finalize was elicited under the
  inherent wording — either freshly, or rehydrated from an
  already-inherent scenario.
- `entry_currency = "USD"` stamp, matching the wizard's create-path
  convention (the wizard elicits USD quantiles; non-USD elicitation is out
  of scope).
- `status`: untouched (status is immutable outside `promote` — existing
  convention).
- `row_version += 1`; ONE `scenario.update` audit row with per-field
  `[before, after]` diff (same builder as `ScenarioService.update`) plus the
  component-aware `per_fieldset_pooling_summary` sidecar (#27 shape).
- Mitigating controls: replaced with the wizard's step-4 selection,
  scoped by the #217 `eligible_control_ids` pattern (plan-gate Arch-I1):
  removals apply only to controls the step-4 picker could actually render
  (ACTIVE), so links to DRAFT/DEPRECATED controls survive re-estimation
  instead of being silently wiped.
- SME rows: existing `scenario_sme_estimates` rows for the scenario are
  deleted and the new set inserted via `persist_estimates` in the same
  transaction (replace semantics; the audit diff is the history).
- Existing runs: untouched and NOT stale-marked — consistent with the
  edit-form convention (scenario edits never touch runs). Documented
  behavior, not an oversight.
- Draft row deleted in-transaction before commit (same ordering as the
  create path).
- Redirect: 303 → `/scenarios/{scenario_id}`.

### UI

- Scenario detail action bar (`templates/scenarios/view.html` analyst/admin
  actions): "Re-estimate" button — a small POST form (CSRF) styled like the
  sibling actions, next to Edit. No status/source gating (matches Edit/Run).
- Wizard shell (`_shell.html`): when `state.target_scenario_id` is set, the
  header/title reads "Re-estimating: {name}"; Cancel keeps its existing
  semantics (deletes the draft; the scenario is untouched).
- Step 6 review: when targeting, the finalize copy states the update-in-place
  semantics ("Finalize replaces the estimates of scenario ‹name› — its run
  history and status are unchanged") and notes that pooling math may have
  been updated since the original estimate, so stored distributions can
  shift even for unchanged inputs (plan-gate Meth-N3).
- Edit form's #27 mixture warning: append a pointer — "To re-elicit from
  experts instead, use Re-estimate on the scenario page."

## Error handling

- Re-estimate POST on a nonexistent / other-org scenario → 404.
- RBAC: analyst/admin only (same guard as the wizard and edit).
- Concurrent wizard drafts: `wizard_drafts` is keyed `(user_id, tx_id)` — a
  user may hold a create-draft and a re-estimate-draft simultaneously;
  no special handling needed (tx isolation already provides it).
- Two concurrent re-estimates of the same scenario: both seed; the second to
  finalize hits the row-version conflict. Correct and intended.
- Scenario deleted while wizard in flight: finalize re-renders the review
  step with a flash message (422, same idiom as other finalize errors); the
  draft is preserved so the user can see their entered data (and cancel).
- Legacy drafts (no `target_*` keys in `state_json`): deserialize with
  `None` defaults → create path, byte-identical behavior.

## Testing

- **Unit (seeding):** rehydration mapping — sme_id XOR sme_name preserved per
  row, N≥3 rows per fieldset survive (adapter-iteration contract),
  loss_shape derivation for PERT / native lognormal / lognormal_mixture
  primary loss; empty-SME-rows scenarios seed with empty dict;
  legacy-state deserialization (missing target keys → None).
- **Service (update_from_wizard):** row-version conflict raises; audit diff
  correctness (before/after on changed fields, source flip, pin clear);
  SME-row replacement preserves all N new rows and removes all old rows;
  vuln_framing set; status/ATT&CK/runs untouched; draft deleted on success,
  preserved on conflict.
- **Integration (routes):** button rendered for analyst, absent for
  reviewer/viewer; full happy path wizard-born scenario (seed → steps →
  finalize → updated in place, redirect, row_version bumped); full happy
  path register-imported scenario (empty SME seed → estimates entered →
  legacy_residual cleared); cancel-is-a-no-op on a TARGETED draft
  (scenario row_version unchanged, draft deleted); 422 conflict path
  renders the flash and preserves the draft; legacy_residual seeding
  excludes the vuln fieldset while rehydrating tef/pl/sl.
- **Methodology surface:** no new FAIR math (pooling pipeline reused
  verbatim). Reviewer attention: vuln-framing flip semantics, provenance
  flip, and that `build_scenario_payload` output is applied without
  app-layer re-derivation.

## Out of scope

- Per-fieldset partial re-estimation (all-or-nothing by design).
- Non-USD elicitation (tracked with the broader #384 currency work).
- SME notifications / async collection.
- Stale-marking existing runs on estimate change (would be a new convention;
  if wanted later, it belongs to a dedicated staleness epic).
- Retaining superseded SME-row generations (audit diff is the history).

## Scope budget

- target_task_count: 6 (seeding fn + state fields; SME-row read path; entry
  route + button; finalize branch/update_from_wizard; wizard copy (shell +
  step 6 + edit-form pointer); integration tests + gate).
- review budget: 4-reviewer plan-gate iterate-to-0; per-task 2-reviewer
  (methodology only on the finalize/provenance task); final 4-reviewer
  PR-gate. Single PR.
- timeline: single-session execution.

## Scope drift log

- (seed) Scope as approved in-session 2026-07-19: entry + seeding + finalize
  update-in-place + UI copy; all scenarios eligible; provenance flips.
- 2026-07-19 plan-gate R1 (1 BLOCKER, 5 IMPORTANT, ~12 NTH → all applied):
  Meth-B1 legacy_residual vuln rows never rehydrated (double-count
  safeguard); Arch-I1 #217 eligible_control_ids scoping on the re-estimate
  finalize; Arch-I2/Spec-N1 `tags` dropped (no Scenario column; fixture
  must not invent it); Spec-I2 conflict path amended 409 → 422 review
  flash (finalize-error idiom); Spec-I3 targeted-cancel no-op test added;
  conversion_metadata cleared on provenance flip; loss_shape wording
  narrowed to wizard-born invariant; step-6 legacy pooling-upgrade note.
  Refuted at gate: the mitigating_controls lazy-load worry (relationship
  is lazy="selectin" — eager).

## Review tier

Feature epic → plan-gate 4-reviewer pass (methodology / spec / architect /
security) iterate-to-zero, per-task reviews during execution (methodology
required on the finalize/provenance task), final 4-reviewer PR-gate.
(Advisory only; the CLAUDE.md milestone floor governs.)
