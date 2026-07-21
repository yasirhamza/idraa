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
- Resume links are `/scenarios/new/wizard/step/{current_step}?tx={tx_id}` with `current_step` clamped to 2..6 (defensive: a corrupt draft with step 1/7 must not 500 the list — clamp, don't crash).
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
from idraa.utils.timeutils import now_utc

pytestmark = pytest.mark.asyncio


async def _mk_draft(db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, age_days: int) -> uuid.UUID:
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
    analyst_user, db_session: AsyncSession
) -> None:
    user = analyst_user
    old_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=40)
    new_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=1)
    svc = WizardStateService(db_session)
    deleted = await svc.cleanup_expired(max_age_minutes=30 * 24 * 60)
    await db_session.commit()
    assert deleted == 1
    from sqlalchemy import select

    remaining = (
        (await db_session.execute(select(WizardDraft.tx_id))).scalars().all()
    )
    assert new_tx in remaining and old_tx not in remaining
```

(READ tests/conftest.py first for the real analyst-user fixture name — use
whatever yields a persisted `User`; if only `authed_analyst` exists, derive
the user via the existing `_user_id_from_org`-style helper pattern.)

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
  - `run_reaper.py` — inside `periodic_reaper_loop`'s `while True:` body,
    after the `reap_once` try/except, a SECOND isolated step:
    ```python
        try:
            await _sweep_wizard_drafts(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Wizard-draft TTL sweep failed; will retry next interval")
    ```
    with (module-level, near `reap_once`; mirror its session pattern —
    READ how `reap_once` opens its session and copy that idiom):
    ```python
    async def _sweep_wizard_drafts(settings: Settings) -> None:
        """Drafts-surfaced spec §4: TTL-sweep idle wizard drafts on the
        reaper cadence. 0 days = disabled."""
        ttl_days = settings.wizard_draft_ttl_days
        if ttl_days <= 0:
            return
        from idraa.services.wizard_state import WizardStateService

        async with <the same session-factory idiom reap_once uses>() as session:
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
- Test: `tests/migrations/` — follow the existing data-migration test pattern if one exists (grep `tests` for the #346 migration's test; if none exists, add a focused test that runs the upgrade body function against a seeded SQLite connection)

- [ ] **Step 1:** Migration body (data-only, no schema):

```python
"""prune stale wizard drafts (one-time, drafts-surfaced spec §4)

DESTRUCTIVE data migration: deletes wizard_drafts idle > 7 days at upgrade
time. Rationale: 110 invisible drafts accumulated on prod before the
resume UI existed (the TTL sweeper had no caller); without this prune the
new drafts strip debuts as a wall of abandoned test walks. 7 days keeps
anything plausibly wanted. Downgrade is a no-op (rows are gone).
"""

def upgrade() -> None:
    op.execute(
        "DELETE FROM wizard_drafts "
        "WHERE updated_at < datetime('now', '-7 days')"
    )


def downgrade() -> None:
    pass
```

NOTE: `datetime('now', ...)` is SQLite dialect. The repo is
SQLite-dev/Postgres-later — follow the repo's existing data-migration
dialect handling (READ the #346-era migration `d4918202a23a`-adjacent
files for the established pattern; if they branch on dialect, mirror it;
`updated_at` is stored UTC via UtcDateTime).

- [ ] **Step 2:** Test: seed two drafts (10 days / 1 day old) into a migrated fixture DB, run the upgrade SQL, assert 1 survivor. `uv run alembic heads` shows exactly ONE head.
- [ ] **Step 3: Commit** — `chore(wizard): one-time prune of pre-UI stale drafts (drafts-surfaced T2)`

---

### Task 3: Drafts strip on /scenarios

**Files:**
- Modify: `src/idraa/routes/scenarios.py` `list_scenarios` (~line 172) — load current user's drafts
- Modify: `src/idraa/templates/scenarios/list.html` — strip section between page_header and the status filter chips
- Test: `tests/integration/test_wizard_drafts_strip.py` (new)

**Interfaces:**
- Produces: template context `wizard_drafts: list[dict]` — each `{"tx_id": str, "name": str, "step": int, "reestimating": bool, "updated_at": datetime}`. Task 4 mirrors the same dict shape.

- [ ] **Step 1: Failing tests** — own-drafts-only isolation (create drafts for two users, assert only the session user's render), cap 20 (create 22, assert 20 rendered + newest first), name fallback, `data-drafts-strip` pin, absent when zero drafts.
- [ ] **Step 2:** Route: query `WizardDraft` where `user_id == user.id` order `updated_at desc` limit 20; map `state_json` → the context dict (name = `state_json.get("name") or "New scenario"`; `reestimating` = bool(`target_scenario_id`); step = clamp(`current_step`, 2, 6)). NO N+1: no per-draft scenario lookups (the strip shows the draft's own name copy, which for re-estimates IS the target's name at seed time).
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

- [ ] Steps: failing test (badge for targeting draft w/ Resume+Discard; absent without; newest-of-two) → route (query by `user_id` + JSON `target_scenario_id == scenario.id.hex` — SQLite JSON extract or filter in Python over the ≤20 newest; keep it simple: fetch user's drafts once, filter in Python) → template notice → run → commit `feat(wizard): re-estimation-in-progress badge on scenario page (drafts-surfaced T4)`.

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
- [ ] `uv run alembic heads` → single head; upgrade runs clean on a copy of a seeded dev DB.
- [ ] Playwright smoke on the UAT snapshot: strip renders with the snapshot's real drafts, Resume lands on the right step, Discard removes the row, badge on a targeted scenario, exit link present. Screenshots for the owner.
- [ ] Full local gate green → final bundled review → PR.
