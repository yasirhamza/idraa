# Wizard Drafts Surfaced Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface persisted wizard drafts (resume/discard on /scenarios + scenario-page badge + honest exit affordance) and wire the draft-TTL lifecycle.

**Architecture:** Read-side additions to two routes/templates; a link in the wizard shell; the TTL sweep piggybacked on the existing reaper loop; one destructive one-time data migration. No new tables, no FAIR math.

**Tech Stack:** FastAPI + SQLAlchemy async, Jinja2, Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-07-21-wizard-drafts-surfaced-design.md` (decisions settled).

**Worktree:** `wt-drafts`, branch `feat/wizard-drafts-surfaced`.

## Global Constraints

- Timestamps render via the `format_datetime` Jinja filter (`<time data-localize>`), NEVER raw strftime (CLAUDE.md).
- Discard reuses `POST /scenarios/new/wizard/cancel?tx=` — no new delete surface. Every form carries `{{ csrf_field() }}`.
- Resume links are `/scenarios/new/wizard/step/{current_step}?tx={tx_id}`; `current_step` upper-clamped to 6 only (step 1 is a VALID route — DQ-1/DA-7); never-advanced drafts (`current_step < 2`) are FILTERED out of strip and badge entirely (DA-1, spec §1).
- pytest FOREGROUND only, `SESSION_SECRET=drafts-implement-01 uv run pytest ... -q --no-cov`.
- Rebuild the sheet (`uv run python -m idraa.tasks.build_css`) in any commit that changes template class inventory; commit `tailwind.css` with it.
- Pre-commit auto-fix → `git add -A`, retry once.

---

### Task 1: Settings + lifecycle sweep wiring

**Files:**
- Modify: `src/idraa/config.py` (add field near `run_reaper_interval_seconds`)
- Modify: `src/idraa/services/wizard_state.py` (remove `_DEFAULT_TTL_MINUTES`, make `max_age_minutes` a required kw-arg)
- Modify: `src/idraa/services/run_reaper.py` (`periodic_reaper_loop` gains the draft sweep step)
- Test: `tests/unit/test_wizard_draft_sweep.py` (new)

**Interfaces:**
- Produces: `Settings.wizard_draft_ttl_days: int = 30` (env `WIZARD_DRAFT_TTL_DAYS`, ge=0; 0 = off). Consumed by Task 6's verification and the loop here.

- [ ] **Step 1: Failing tests** — create `tests/unit/test_wizard_draft_sweep.py`:

```python
"""Wizard-draft TTL sweep (drafts-surfaced spec §4)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models.wizard_draft import WizardDraft
from idraa.services.wizard_state import WizardStateService
from idraa.models._types import now_utc  # DA-10: the import run_reaper.py:66 uses

pytestmark = pytest.mark.asyncio


async def _mk_draft(db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, age_days: int) -> uuid.UUID:  # noqa: E501
    tx = uuid.uuid4()
    draft = WizardDraft(
        user_id=user_id,
        tx_id=tx,
        organization_id=org_id,
        state_json={"tx_id": str(tx), "current_step": 3},
    )
    db.add(draft)
    await db.flush()
    # backdate via direct UPDATE (onupdate would restamp)
    from sqlalchemy import update

    await db.execute(
        update(WizardDraft)
        .where(WizardDraft.tx_id == tx)
        .values(updated_at=now_utc() - datetime.timedelta(days=age_days))
    )
    await db.commit()
    return tx


async def test_ttl_setting_default() -> None:
    assert get_settings().wizard_draft_ttl_days == 30


async def test_cleanup_deletes_old_keeps_recent(
    seed_user, seed_organization, db_session: AsyncSession
) -> None:
    user = seed_user
    old_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=40)
    new_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=1)
    svc = WizardStateService(db_session)
    await svc.cleanup_expired(max_age_minutes=30 * 24 * 60)
    await db_session.commit()
    # NO count assertion (DQ-5): cleanup_expired's docstring warns SQLite may
    # report -1 — correctness is the row set, not the count.
    from sqlalchemy import select

    remaining = (
        (await db_session.execute(select(WizardDraft.tx_id))).scalars().all()
    )
    assert new_tx in remaining and old_tx not in remaining
```

(Fixtures pinned per DQ-3: `seed_user` is an ANALYST `User` at
tests/conftest.py:386, `seed_organization` its org — the exact pair the
adjacent `test_cleanup_expired_removes_idle_drafts` already uses at
tests/unit/test_wizard_state.py:150-154. `authed_analyst` yields
`(client, org_id)` — NOT a User — do not use it here.)

- [ ] **Step 2: Run — expect FAIL** (`wizard_draft_ttl_days` missing).

- [ ] **Step 3: Implement.**
  - `config.py`, adjacent to `run_reaper_interval_seconds`:
    ```python
    wizard_draft_ttl_days: int = Field(
        default=30,
        ge=0,
        description=(
            "Delete wizard drafts idle longer than this many days "
            "(drafts-surfaced spec §4). 0 disables the sweep."
        ),
    )
    ```
    (Match the file's existing Field style; env name derives from the
    field per the existing settings config.)
  - `wizard_state.py`: delete `_DEFAULT_TTL_MINUTES`; signature becomes
    `async def cleanup_expired(self, *, max_age_minutes: int) -> int:`
    (no default — the only caller passes the settings value; update the
    module docstring's stale "30min" reference).
  - **Boot sweep (DA-3, PRIMARY on scale-to-zero):** in `app.py`'s lifespan,
    AFTER the existing reaper/retention `try/except` block CLOSES (~line
    790 — NOT inside it: the sweep helper opens its own session and must
    not nest inside the boot block's `async with get_session()` nor
    double-wrap its exception handling — DQ-11), add a sibling one-shot:
    ```python
    try:
        await sweep_wizard_drafts(_settings)
    except Exception:
        logging.getLogger(__name__).exception(
            "Boot wizard-draft sweep failed; continuing startup"
        )
    ```
    (app.py has NO module logger — the lifespan logs via
    `logging.getLogger(__name__)` inline, see app.py:787/805 — DQ-9;
    import `sweep_wizard_drafts` alongside the reaper imports at
    app.py:764; a sweep failure must never block startup).
  - Both `config.py` field descriptions document the coupling (DQ-6): the
    new field notes "periodic sweep rides the run-reaper loop —
    RUN_REAPER_INTERVAL_SECONDS=0 disables it (boot sweep still runs)";
    `run_reaper_interval_seconds`'s description gains "also the cadence of
    the wizard-draft TTL sweep".
  - `run_reaper.py` — inside `periodic_reaper_loop`'s `while True:` body,
    after the `reap_once` try/except, a SECOND isolated step:
    ```python
        try:
            await sweep_wizard_drafts(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Wizard-draft TTL sweep failed; will retry next interval")
    ```
    with (module-level, near `reap_once`; mirror its session pattern —
    READ how `reap_once` opens its session and copy that idiom):
    ```python
    async def sweep_wizard_drafts(settings: Settings) -> None:
        """Drafts-surfaced spec §4: TTL-sweep idle wizard drafts on the
        reaper cadence (public name — consumed by both the boot one-shot
        and the loop, DQ-13). 0 days = disabled."""
        ttl_days = settings.wizard_draft_ttl_days
        if ttl_days <= 0:
            return
        from idraa.db import get_session
        from idraa.services.wizard_state import WizardStateService

        async with get_session() as session:  # the exact idiom reap_once uses (run_reaper.py:196-199)
            deleted = await WizardStateService(session).cleanup_expired(
                max_age_minutes=ttl_days * 24 * 60
            )
            await session.commit()
        if deleted:
            logger.info("Wizard-draft TTL sweep deleted %d idle draft(s)", deleted)
    ```
- [ ] **Step 4: Run the new test file + `tests/unit/test_run_reaper*.py` (reaper loop untouched behaviorally, but prove no import breakage) — PASS.**
- [ ] **Step 5: Commit** — `feat(wizard): draft TTL lifecycle — settings + reaper-loop sweep (drafts-surfaced T1)`

---

### Task 2: One-time prune migration (destructive, spec §4)

**Files:**
- Create: `alembic/versions/<rev>_prune_stale_wizard_drafts.py` (generate with `uv run alembic revision -m "prune stale wizard drafts"`; READ the newest existing revision first and chain `down_revision` correctly)
- Test: `tests/migrations/` — use the EXISTING pytest-alembic harness (`tests/migrations/conftest.py`), following `test_audit_f2_vuln_framing.py:59-74` exactly (authoritative instructions in Step 2; never call the migration's `upgrade()` directly)

- [ ] **Step 1:** Migration body (data-only, no schema):

```python
"""prune stale wizard drafts (one-time, drafts-surfaced spec §4)

DESTRUCTIVE data migration: deletes wizard_drafts idle > 7 days at upgrade
time. Rationale: 110 invisible drafts accumulated on prod before the
resume UI existed (the TTL sweeper had no caller); without this prune the
new drafts strip debuts as a wall of abandoned test walks. 7 days keeps
anything plausibly wanted. Downgrade is a no-op (rows are gone).
"""

import datetime
import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    # Dialect-neutral bound-param cutoff (DQ-2/DA-6): mirrors the
    # b7d2e8a1c5f3 timestamp-window precedent — NOT SQLite's datetime().
    # UtcDateTime stores "YYYY-MM-DD HH:MM:SS.ffffff" UTC wall-clock
    # (verified at plan-gate), so a same-format string compares correctly.
    cutoff = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
    ).strftime("%Y-%m-%d %H:%M:%S.%f")
    result = op.get_bind().execute(
        sa.text("DELETE FROM wizard_drafts WHERE updated_at < :cutoff"),
        {"cutoff": cutoff},
    )
    logger.warning("pruned %d stale wizard draft(s) (>7 days idle)", result.rowcount)


def downgrade() -> None:
    pass
```

- [ ] **Step 2:** Test via the EXISTING pytest-alembic harness (DQ-4 —
tests/migrations/conftest.py provides `alembic_config`/`alembic_engine`;
follow the exact time-cutoff analog `tests/migrations/test_audit_f2_vuln_framing.py:59-74`):
`command.upgrade(cfg, <prior rev>)` → raw-INSERT two drafts with
`updated_at` at now−10 days / now−1 day (format them with the same
`%Y-%m-%d %H:%M:%S.%f` strftime) → `command.upgrade(cfg, <new rev>)` →
assert exactly the 1-day row survives. Do NOT call the migration's
`upgrade()` function directly. `uv run alembic heads` shows exactly ONE
head (current head to chain from: `26444158e537`).
- [ ] **Step 3: Commit** — `chore(wizard): one-time prune of pre-UI stale drafts (drafts-surfaced T2)`

---

### Task 3: Drafts strip on /scenarios

**Files:**
- Modify: `src/idraa/routes/scenarios.py` `list_scenarios` (~line 172) — load current user's drafts
- Modify: `src/idraa/templates/scenarios/list.html` — strip section between page_header and the status filter chips
- Test: `tests/integration/test_wizard_drafts_strip.py` (new)

**Interfaces:**
- Produces: template context `wizard_drafts: list[dict]` — each `{"tx_id": str, "name": str, "step": int, "reestimating": bool, "updated_at": datetime}`. Task 4 mirrors the same dict shape.

- [ ] **Step 1: Failing tests** — own-drafts-only isolation (create drafts for two users, assert only the session user's render — the session user is resolved by email `analyst@test.local` via a select on User, since `authed_analyst` yields only (client, org_id) — DQ-3), org isolation (a same-user draft in ANOTHER org does not render — DA-2), step-1 drafts EXCLUDED (DA-1), cap 20 (create 22 qualifying, assert 20 rendered + newest first), name fallback, `data-drafts-strip` pin, absent when zero qualifying drafts.
- [ ] **Step 2:** Route: query `WizardDraft` where `user_id == user.id` AND `organization_id == user.organization_id` (DA-2) order `updated_at desc` with NO SQL limit (DA-9: a limit-then-filter window lets a burst of step-1 ghosts — one minted per "+ New scenario" GET — EVICT a real draft from view, recreating the invisible-draft failure this feature exists to end; per-user org-scoped rows are TTL-bounded, so the unbounded fetch is production-scale safe). Then map rows, SKIPPING drafts whose `state_json.get("current_step", 1) < 2` (DA-1), and cap the MAPPED list at 20 for display. Context dict per row: name = `state_json.get("name") or "New scenario"`; `reestimating` = bool(`state_json.get("target_scenario_id")`); step = `min(int(state_json.get("current_step", 2)), 6)` (upper clamp only — DQ-1); `tx_id`/`updated_at` from the ORM row (DQ-8). NO N+1: no per-draft scenario lookups.
- [ ] **Step 3:** Template:
```html
{% if wizard_drafts %}
<section class="mb-4" data-drafts-strip>
  <h2 class="text-meta text-ink-3 uppercase mb-2">In progress ({{ wizard_drafts|length }})</h2>
  <ul class="space-y-1">
    {% for d in wizard_drafts %}
    <li class="flex items-center gap-3 text-sm">
      <a class="link" href="/scenarios/new/wizard/step/{{ d.step }}?tx={{ d.tx_id }}">
        {{ d.name }}{% if d.reestimating %} <span class="text-ink-3">(re-estimating)</span>{% endif %}
      </a>
      <span class="text-ink-3">Step {{ d.step }} of 6 · {{ d.updated_at | format_datetime }}</span>
      <form method="post" action="/scenarios/new/wizard/cancel?tx={{ d.tx_id }}">
        {{ csrf_field() }}
        <button type="submit" class="btn btn-ghost btn-xs text-status-critical">Discard</button>
      </form>
    </li>
    {% endfor %}
  </ul>
</section>
{% endif %}
```
- [ ] **Step 4: Rebuild sheet + run the new test file + existing scenarios list tests — PASS.**
- [ ] **Step 5: Commit** — `feat(wizard): drafts strip on /scenarios — resume + discard (drafts-surfaced T3)`

---

### Task 4: Re-estimation badge on the scenario page

**Files:**
- Modify: `src/idraa/routes/scenarios.py` scenario-view handler — load the newest current-user draft targeting this scenario
- Modify: `src/idraa/templates/scenarios/view.html` — notice under the Re-estimate row (`data-reestimate-draft-badge`)
- Test: append to `tests/integration/test_wizard_drafts_strip.py`

- [ ] Steps: failing test (badge for targeting draft w/ Resume+Discard; absent without; newest-of-two wins; a step-1 targeting draft does NOT badge) → route: SQL by target, NOT a capped fetch (DA-5): `select(WizardDraft).where(WizardDraft.user_id == user.id, WizardDraft.organization_id == user.organization_id, WizardDraft.state_json["target_scenario_id"].as_string() == scenario.id.hex).order_by(WizardDraft.updated_at.desc()).limit(1)` (SQLAlchemy JSON accessor works on SQLite JSON1; verify `.as_string()` against the stored plain-string value, else use `func.json_extract`), then apply the same `current_step >= 2` filter → template notice → run → commit `feat(wizard): re-estimation-in-progress badge on scenario page (drafts-surfaced T4)`.

---

### Task 4b: Resume/discard robustness (spec §4b — DA-4/DA-8)

**Files:**
- Modify: `src/idraa/routes/scenarios.py` — `get_wizard_step` (~1611) + `cancel_wizard` (~2559)
- Test: append to `tests/integration/test_wizard_drafts_strip.py`

- [ ] **Step 1: Failing tests** — (a) GET a wizard step with an explicit
random tx → 303 to `/scenarios` AND no new wizard_drafts row exists
(count unchanged); (b) POST cancel with an unknown tx → 303, count
unchanged (no mint-then-delete write pair — assert via row count before/
after); (c) POST cancel with `tx=not-a-uuid` → 303 (not 500); (d) the
no-tx GET entry path still mints + renders step 1 (unchanged behavior);
(e) GET a wizard step with `tx=not-a-uuid` → 303 (not 500) — DQ-10: the
resume path's `_resolve_tx` does a bare `uuid.UUID(tx_str)` today, so
malformed resume links 500 symmetrically to cancel's case.
- [ ] **Step 2:** `get_wizard_step`: the tx-provided branch is guarded
BEFORE `_resolve_tx`/`get_or_create` runs (DQ-10 — `_resolve_tx` at
routes/scenarios.py:1334 does a bare `uuid.UUID(tx_str)` and then
`get_or_create`; it has THREE call sites (1579/1608/1828), so do NOT
change `_resolve_tx` itself — scope the guard to `get_wizard_step`):
`try: parsed = uuid.UUID(tx) except ValueError: → 303 /scenarios`; then
the existence check via a new read-only
`WizardStateService.get(user_id, tx_id) -> WizardState | None` (add it
beside `get_or_create`, reusing the found-branch's whitelist-filter +
version_token hydration at wizard_state.py:164-173; ideally
`get_or_create` delegates its found-branch to it); on miss:
`build_flash("That draft no longer exists — it may have been discarded
or expired.", "warning")` → 303 `/scenarios` (mirror the delete-run
query-param + flash mechanics in routes/runs.py:819-848, adding the
analogous param handling to `list_scenarios`). Only on existence does
the normal `_resolve_tx` path proceed. No-tx path untouched. `cancel_wizard`: drop the `get_or_create` +
short-circuit dance; guard `uuid.UUID(tx)` in try/except ValueError → 303
`/scenarios`; call `wiz.clear(user_id=user.id, tx_id=parsed)` directly
(verify `clear` is a WHERE-delete safe on missing rows — READ it; if it
isn't idempotent, make it so).
- [ ] **Step 3: Run + commit** — `fix(wizard): dead-tx resume redirects instead of minting phantoms; idempotent cancel (drafts-surfaced T4b)`

---

### Task 5: Wizard shell exit affordance

**Files:**
- Modify: `src/idraa/templates/scenarios/wizard/_shell.html` footer (next to Cancel, ~line 102)
- Test: append a pin to `tests/integration/test_wizard_drafts_strip.py` (GET any wizard step renders the link)

- [ ] Implement:
```html
<a href="/scenarios" class="btn btn-ghost btn-sm"
   title="Progress through your last completed step is saved. Edits on this page since then are not.">
  Exit — draft saved
</a>
```
Placed BEFORE Cancel (exit = safe action, cancel = destructive). Commit `feat(wizard): honest exit-draft-saved affordance (drafts-surfaced T5)`.

---

### Task 6: Verification sweep (main loop)

- [ ] Full affected suites + `tests/unit tests/integration -k "wizard or draft or scenario"` green; `uv run ruff check .`, `format --check`, `mypy src`.
- [ ] Open the follow-up GH issue for the DA-1 root cause: "wizard mints a draft on GET — move to lazy-create on first POST" (cite this spec).
- [ ] `uv run alembic heads` → single head; upgrade runs clean on a copy of a seeded dev DB.
- [ ] Playwright smoke on the UAT snapshot: strip renders with the snapshot's real drafts, Resume lands on the right step, Discard removes the row, badge on a targeted scenario, exit link present. Screenshots for the owner.
- [ ] Full local gate green → final bundled review → PR.
