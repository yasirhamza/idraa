# Wizard drafts, surfaced — resume/discard UI + save-exit + lifecycle

Owner request (2026-07-21, after the finalize-transient investigation
surfaced that their crashed draft had silently survived): "drafts are
persisted? how come that feature is not surfaced in UI? also it would be
cool to have a save draft button" → approved for the full cycle.

## What already exists (verified against the tree)

- `WizardDraft` (models/wizard_draft.py): PK `(user_id, tx_id)`,
  `organization_id` FK, `state_json`, `version_token`, `updated_at`
  (UtcDateTime, drives TTL). Every wizard step POST persists the full
  state via `advance_step` — crash/restart/deploy lose nothing.
- Resume works TODAY by URL: `/scenarios/new/wizard/step/{n}?tx={tx_id}`
  (user-scoped `get_or_create`).
- Discard exists: `POST /scenarios/new/wizard/cancel?tx=` deletes the
  draft and 303s to `/scenarios`.
- `WizardStateService.cleanup_expired(max_age_minutes=30)` exists with
  **zero callers** (the "Phase 1.5b scheduler" note never landed) →
  drafts are immortal and invisible: **110 rows accumulated on prod**.
- Periodic-loop precedent: `run_reaper.periodic_reaper_loop` spawned from
  the FastAPI lifespan at `Settings.run_reaper_interval_seconds`, each
  iteration exception-isolated.

## Scope

### 1. Drafts strip on `/scenarios` (per-user)

`list_scenarios` additionally loads the CURRENT USER's drafts (org-scoped
by construction of the PK+FK), newest-first, **capped at 20**. Rendered as
a quiet section between the page header and the status filter chips, only
when count > 0:

- Row: display name (`state_json.name` or "New scenario"), context label
  "Re-estimating" when `target_scenario_id` is set, "Step {current_step}
  of 6", last-touched via the **`format_datetime` filter** (CLAUDE.md
  timezone convention — no raw strftime), **Resume** link
  (`/scenarios/new/wizard/step/{current_step}?tx={tx_id}`), **Discard**
  (form POST to the existing cancel endpoint, CSRF field, small
  ghost/critical button).
- Per-user only: analysts see their own drafts, not colleagues' (the
  wizard state is personal working memory; an admin view is a non-goal).
- No dedup: multiple drafts against the same scenario list newest-first
  (owner default, confirmed).

### 2. Re-estimation badge on the scenario page

`scenarios/view.html`: when the current user has ≥1 draft whose
`target_scenario_id` is this scenario, render a compact notice under the
Re-estimate button row: "Re-estimation in progress (step N, <when>) —
Resume · Discard" (newest draft only). Same Resume/Discard mechanics.

### 3. "Exit — draft saved" affordance in the wizard shell

The shell footer (next to Cancel) gains a plain link to `/scenarios`
labeled **"Exit — draft saved"** with a title attribute: "Progress through
your last completed step is saved. Edits on this page since then are
not." HONESTY over machinery: state is already persisted per step; the
button only makes that visible. No new POST surface. (The existing
Cancel keeps its destructive meaning: discard the draft.)

### 4. Lifecycle, finally wired

- New `Settings.wizard_draft_ttl_days: int = 30` (env
  `WIZARD_DRAFT_TTL_DAYS`, 0 = sweep off) — 30 *days*, not the legacy
  30-minute default that predates a resume UI.
- The sweep piggybacks on the EXISTING reaper loop: `reap_once`'s caller
  (`periodic_reaper_loop`) gains a second exception-isolated step calling
  `WizardStateService.cleanup_expired(max_age_minutes=ttl_days*24*60)`
  on its own short-lived session. One loop, two sweeps, either failing
  logs and continues. `cleanup_expired`'s signature is unchanged; its
  legacy `_DEFAULT_TTL_MINUTES = 30` constant is removed in favor of the
  caller passing the settings-derived value (kill dead optionality — no
  caller ever used the default).
- **One-time prune (THE destructive step, called out for plan-gate):** an
  Alembic data migration deletes `wizard_drafts` rows with
  `updated_at < now − 7 days` at upgrade. Rationale: the 110 accumulated
  prod rows are abandoned test walks, most younger than 30 days — without
  the prune the new strip lists a wall of noise on day one. 7 days
  preserves anything plausibly still wanted (incl. the owner's crashed
  draft from today). Follows the #346 data-migration precedent; not
  reversible (data delete), documented in the migration docstring.

### 5. Testing

- Route: strip renders own drafts only (two-user isolation test), cap-20,
  name fallback "New scenario", re-estimating context label.
- Badge: shown for targeting drafts, absent otherwise, newest-of-several.
- Discard from the strip round-trips (draft gone, redirect to list).
- Sweep: TTL respected (older deleted, younger kept), `0` disables; loop
  isolation (a sweep exception does not kill the reaper loop) — unit
  level with a stubbed session.
- Migration: upgrade prunes an old row, keeps a recent one (SQLite
  fixture DB).
- Template pins: `data-drafts-strip`, `data-reestimate-draft-badge`,
  the shell exit link.

## Out of scope (non-goals)

- Admin/cross-user draft visibility or management.
- Draft naming/renaming; draft-level notes.
- Auto-dedup of multiple drafts per scenario.
- A "save current page without advancing" POST (the per-step save
  already covers the wizard's real granularity; revisit only if owners
  ask after living with the strip).
- Expert-mode (non-wizard) form drafts.

## Review tier

Routes + templates + one service touch + one data migration; no FAIR
math. Plan-gate and final gate: quality + architect (owner-trimmed UI
tier; the architect specifically reviews the destructive migration and
the lifecycle wiring). Implementation by subagent, bundled final review.

## Scope budget

- target_task_count: 6 (5 implementation + 1 verification), single PR.
- Review budget: 2-reviewer plan-gate + bundled final reviewer.
- Timeline budget: same session (2026-07-21), deploy after merge.

## Scope drift log

- Originating prompt named: surfacing drafts in UI + a save-draft button.
- +ADDED: lifecycle wiring (TTL sweep) + one-time prune — direct
  consequence of surfacing (110 accumulated invisible rows become 110
  visible ones without it); owner approved "lifecycle, finally wired" in
  the proposal message.
- +ADDED: re-estimation badge on the scenario page (proposal item,
  approved with the rest).
- REFRAMED: "save draft button" → "Exit — draft saved" link. Every step
  already saves; a save button that duplicates Next would be dead
  machinery. The link surfaces the existing guarantee honestly.
- −CUT: per-page save-without-advancing POST (non-goal until real demand).
