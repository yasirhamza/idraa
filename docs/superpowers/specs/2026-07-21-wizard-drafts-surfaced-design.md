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

- **Never-advanced drafts are EXCLUDED (plan-gate DA-1 BLOCKER /
  DQ-1 — explicit product decision):** a mere GET of `/scenarios/new/wizard`
  ("+ New scenario") mints and commits an empty `current_step=1` row —
  the very generator of the 110 accumulated prod rows. The strip and the
  badge filter `current_step >= 2`: step-1 rows contain nothing the user
  typed and are re-creatable in two clicks. The generator itself
  (mint-on-GET → lazy-create-on-first-POST) is a tracked follow-up issue
  opened at PR time, not in scope here.
- Row: display name (`state_json.name` or "New scenario"), context label
  "Re-estimating" when `target_scenario_id` is set, "Step {current_step}
  of 6" (upper-clamped to 6 only — step values are trusted otherwise,
  DA-7/DQ-1), last-touched via the **`format_datetime` filter** (CLAUDE.md
  timezone convention — no raw strftime), **Resume** link
  (`/scenarios/new/wizard/step/{current_step}?tx={tx_id}`), **Discard**
  (form POST to the cancel endpoint, CSRF field, small ghost/critical
  button). Field sourcing (DQ-8): `name`/`current_step`/
  `target_scenario_id` come from `state_json`; `tx_id`/`updated_at` come
  from the ORM row.
- **Org-scoped (DA-2):** both the strip and badge queries filter
  `organization_id == user.organization_id` in addition to `user_id` —
  `_resolve_tx` applies this deliberately (cross-org resume would attach
  wrong org-scoped override/library pins); the read paths must not hand
  out resume links that bypass that defense.
- Per-user only: analysts see their own drafts, not colleagues' (the
  wizard state is personal working memory; an admin view is a non-goal).
- No dedup: multiple drafts against the same scenario list newest-first
  (owner default, confirmed).

### 2. Re-estimation badge on the scenario page

`scenarios/view.html`: when the current user has ≥1 draft (org-scoped,
`current_step >= 2`) whose `target_scenario_id` is this scenario, render
a compact notice under the Re-estimate button row: "Re-estimation in
progress (step N, <when>) — Resume · Discard" (newest draft only).
**Badge correctness must not inherit the strip's display cap (DA-5):**
the badge queries by target in SQL (`state_json` JSON extract on
`target_scenario_id == scenario.id.hex`), ordered `updated_at desc
limit 1` — never "the 20 newest, filtered".

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
- **Boot sweep is the PRIMARY mechanism (DA-3):** a scale-to-zero machine
  that auto-stops within 300s would never reach the periodic loop's first
  post-sleep iteration. A one-shot draft sweep runs in the lifespan next
  to the existing boot `reap_orphaned_runs` call (exception-guarded); the
  periodic-loop step is the long-uptime backup. The sweep also piggybacks
  on `periodic_reaper_loop` as a second exception-isolated step on its own
  short-lived session. **Coupling documented on BOTH settings fields
  (DA-3/DQ-6):** `RUN_REAPER_INTERVAL_SECONDS=0` disables the periodic
  draft sweep too (the boot sweep still runs).
  `cleanup_expired`'s `max_age_minutes` becomes a REQUIRED kwarg (the
  legacy `_DEFAULT_TTL_MINUTES = 30` constant is deleted; no caller ever
  used the default — kill dead optionality).
- **One-time prune (THE destructive step, called out for plan-gate):** an
  Alembic data migration deletes `wizard_drafts` rows with
  `updated_at < now − 7 days` at upgrade. Rationale: the 110 accumulated
  prod rows are abandoned test walks, most younger than 30 days — without
  the prune the new strip lists a wall of noise on day one. 7 days
  preserves anything plausibly still wanted (incl. the owner's crashed
  draft from today). Mechanics (DA-6/DQ-2): dialect-neutral bound-param
  cutoff computed in Python (the `b7d2e8a1c5f3` timestamp-window
  precedent — NOT SQLite's `datetime('now')`), and the migration logs the
  deleted rowcount (`logger.warning`) per the #346 observability
  precedent. Not reversible (data delete), documented in the docstring.

### 4b. Resume/discard robustness (plan-gate DA-4/DA-8)

- **Dead-tx resume must not mint phantoms:** `get_wizard_step` currently
  `get_or_create`s the parsed tx — resuming a swept/discarded/bookmarked
  tx would commit a fresh empty draft (which the strip then displays).
  When `tx` is EXPLICITLY provided and no `(user_id, tx)` row exists, the
  route 303s to `/scenarios` with a "draft no longer exists" flash
  (mirrors cancel's r3-MAJOR short-circuit). The no-tx entry path is
  untouched.
- **Cancel hardening:** the cancel endpoint calls `clear` directly
  (idempotent delete-by-key; no more mint-then-delete on unknown tx) and
  guards the `uuid.UUID(tx)` parse (malformed tx → 303 to `/scenarios`,
  not 500).
- **No audit rows for draft discard — by design:** drafts are personal
  working memory, not business entities; consistent with today's cancel
  and the TTL sweep (scenario deletes audit via `ScenarioService.delete`
  because scenarios ARE business entities).

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
- +ADDED at plan-gate (2026-07-21, quality+architect, applied as one
  consolidated commit): step-1 drafts filtered from strip+badge + lazy-
  create follow-up issue (DA-1/DQ-1/DA-7); org-scoping on read paths
  (DA-2); boot-time sweep + coupling docs (DA-3/DQ-6); dead-tx resume
  redirect + cancel direct-clear/parse-guard + no-audit statement
  (DA-4/DA-8); badge SQL-by-target limit 1 (DA-5); migration bound-param
  cutoff + rowcount log, b7d2e8a1c5f3 precedent (DA-6/DQ-2); test-plan
  fixture corrections seed_user/seed_organization + pytest-alembic
  pattern per test_audit_f2_vuln_framing.py (DQ-3/DQ-4); count-assert
  dropped per cleanup_expired's documented contract (DQ-5); concrete
  get_session idiom (DQ-7); field-sourcing clarification (DQ-8).
