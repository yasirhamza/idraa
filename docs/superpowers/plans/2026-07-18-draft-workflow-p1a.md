# Scenario DRAFT Workflow (epic #34 P1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First real use of `EntityStatus.DRAFT` on Scenario: draft scenarios are visible/editable but excluded from run creation, dashboard metrics, and coverage until an audit-logged promote flips them ACTIVE.

**Architecture:** No schema change (enum member + `status` column already exist; no migration). Enforcement is server-side in `RunService.create_and_dispatch` (the form picker filter is convenience only), with defense-in-depth in the run executor. Promote mirrors the shipped `confirm_vuln_framing` service/route/banner pattern. A query-site allowlist test makes the exclusion sweep provably total.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Jinja2/HTMX, pytest + httpx.

**Spec:** `docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md` §4 (P1a slice).

## Global Constraints

- Scenario list/view/export SHOW drafts (visibility is the point of review); only computation/metrics exclude them.
- Promote is idempotent, audit-logged (`AuditWriter`), row-version-bumping — exactly the `confirm_vuln_framing` shape (`services/scenarios.py:509-556`).
- Promote REFUSES while `vuln_framing == "legacy_residual"` (stricter P1a subset of spec's "confirm OR acknowledge"; the acknowledge path arrives with P1c's dialog).
- No demote (deprecation exists). No new Settings keys. No `.strftime` in templates (use existing filters). All new POSTs rely on the global CSRF middleware + `csrf_field()`.
- Copy discipline: drafts are "pending review", never "results" (spec §7.1).

---

### Task 1: Server-side DRAFT gate at run creation + executor defense-in-depth

**Files:**
- Modify: `src/idraa/services/runs.py` (in `create_and_dispatch`, after the scenario fetch at ~L103-115)
- Modify: `src/idraa/services/run_executor.py` (~L1852/L1863, after each re-fetch)
- Test: `tests/integration/test_draft_workflow.py` (new)

**Interfaces:**
- Consumes: `ScenarioRepo.get_for_org_or_raise` / `fetch_by_ids_for_org` (existing, unchanged).
- Produces: `ValidationError("scenario '<name>' is a draft — promote it before running an analysis")` raised from `create_and_dispatch` for ANY non-ACTIVE scenario in `scenario_ids`. Task 2's picker and Task 4's contract test rely on this being the authoritative gate.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/integration/test_draft_workflow.py — epic #34 P1a.

DRAFT scenarios are review-pending priors: visible and editable, but
excluded from run creation (server-side gate — the form filter is
convenience), dashboard metrics, and library coverage until promoted.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus
from tests.conftest import csrf_post
# _seed_scenario lives in tests/integration/test_scenario_routes.py; move it
# to tests/factories.py if importing across test modules is awkward — it is
# already parameterized by status.
from tests.integration.test_scenario_routes import _seed_scenario


@pytest.mark.asyncio
async def test_run_create_rejects_draft_scenario(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    draft = await _seed_scenario(db_session, org_id=org_id, name="Draft S", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, "/analyses", {"scenario_ids": [str(draft.id)], "iterations": "1000"}, follow_redirects=False)
    assert r.status_code == 422
    assert "draft" in r.text.lower()


@pytest.mark.asyncio
async def test_run_create_rejects_mixed_active_and_draft(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    active = await _seed_scenario(db_session, org_id=org_id, name="Active S", status=EntityStatus.ACTIVE)
    draft = await _seed_scenario(db_session, org_id=org_id, name="Draft T", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, "/analyses", {"scenario_ids": [str(active.id), str(draft.id)], "iterations": "1000"}, follow_redirects=False)
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify both FAIL** — `uv run pytest tests/integration/test_draft_workflow.py -q` (expect 200/303-shaped success today, i.e. assertion failures).

- [ ] **Step 3: Implement the gate.** In `services/runs.py::create_and_dispatch`, immediately after `scenarios` is populated (both branches):

```python
        # Epic #34 P1a: DRAFT scenarios are review-pending priors — never
        # runnable. Server-side gate; the /analyses/new picker filter is
        # convenience only and trivially bypassed.
        non_active = [s for s in scenarios if s.status != EntityStatus.ACTIVE]
        if non_active:
            names = ", ".join(sorted(s.name for s in non_active))
            raise ValidationError(
                f"scenario '{names}' is a draft — promote it before running an analysis"
            )
```

(Reuse the module's existing `ValidationError`→422 handling in `routes/runs.py::post_create_analysis`; verify by reading how existing validation failures surface there and match it.)

In `services/run_executor.py`, after each committed-run re-fetch (~L1852 SINGLE, ~L1863 AGGREGATE), add the defense-in-depth guard (same comment, raising the executor's existing failure-path exception type so the run lands `failed`, not crashed):

```python
        if any(s.status != EntityStatus.ACTIVE for s in scenarios):
            raise RunExecutionError("run references a non-ACTIVE scenario (draft?) — refusing to execute")
```

(Use the module's actual failure exception — read the surrounding except-clauses and match; if none fits, `ValueError` caught by the generic failure path is acceptable.)

- [ ] **Step 4: Run tests to verify both PASS**, plus `uv run pytest tests/integration/test_run_routes.py tests/services/test_runs*.py -q` for regressions.

- [ ] **Step 5: Commit** — `git commit -m "feat(runs): server-side DRAFT scenario gate at run creation (epic #34 P1a)"`

### Task 2: Picker, dashboard, and coverage exclusions

**Files:**
- Modify: `src/idraa/routes/runs.py:904-909` (`get_new_analysis_form` scenario query)
- Modify: `src/idraa/repositories/scenario_repo.py:99-120` (`list_pinned_library_entry_ids_for_org`)
- Modify: `src/idraa/services/dashboard.py:259` (`scenario_count`)
- Test: append to `tests/integration/test_draft_workflow.py`

**Interfaces:**
- Consumes: `EntityStatus`, existing repo methods.
- Produces: `list_pinned_library_entry_ids_for_org(organization_id, *, statuses: tuple[EntityStatus, ...] = (EntityStatus.ACTIVE,))` — dashboard/coverage callers use the default; pass explicitly if other statuses are ever wanted.

- [ ] **Step 1: Failing tests** (append):

```python
@pytest.mark.asyncio
async def test_new_analysis_picker_omits_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    await _seed_scenario(db_session, org_id=org_id, name="Visible Active", status=EntityStatus.ACTIVE)
    await _seed_scenario(db_session, org_id=org_id, name="Hidden Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await client.get("/analyses/new")
    assert "Visible Active" in r.text and "Hidden Draft" not in r.text


@pytest.mark.asyncio
async def test_dashboard_counts_exclude_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    await _seed_scenario(db_session, org_id=org_id, name="Counted", status=EntityStatus.ACTIVE)
    await _seed_scenario(db_session, org_id=org_id, name="Not Counted", status=EntityStatus.DRAFT)
    await db_session.commit()
    from idraa.repositories.scenario_repo import ScenarioRepo
    repo = ScenarioRepo(db_session)
    # dashboard calls count_for_org(status=ACTIVE) after this task
    assert await repo.count_for_org(organization_id=org_id, status=EntityStatus.ACTIVE) == 1
    pinned = await repo.list_pinned_library_entry_ids_for_org(org_id)
    # neither seed pins a library entry; assertion is that the signature accepts the default
    assert pinned == []
```

Plus a template-level dashboard assertion if `build_dashboard` is cheaply invokable in tests — read `tests/` for an existing dashboard test to extend (`grep -rl build_dashboard tests/`) and add the ACTIVE-only count assertion there.

- [ ] **Step 2: Verify failure** (picker test fails; count test fails only if dashboard still counts all — the repo-level call with `status=` already works, so the load-bearing edits are the call sites).

- [ ] **Step 3: Implement** — picker: add `.where(Scenario.status == EntityStatus.ACTIVE)` to the `_scenario_stmt` in `get_new_analysis_form`; dashboard: `count_for_org(organization_id=org.id, status=EntityStatus.ACTIVE)`; repo: add `statuses` keyword (default `(EntityStatus.ACTIVE,)`) to `list_pinned_library_entry_ids_for_org` with `.where(Scenario.status.in_(statuses))`. Grep `list_pinned_library_entry_ids_for_org` callers and leave non-dashboard callers (if any) on explicit previous behavior only if a test proves they need drafts — default them to ACTIVE otherwise.

- [ ] **Step 4: Run** new tests + `uv run pytest tests/integration/test_dashboard* tests/services/test_dashboard* -q`.

- [ ] **Step 5: Commit** — `feat(dashboard): exclude DRAFT scenarios from picker, counts, coverage (epic #34 P1a)`

### Task 3: Promote flow + UI

**Files:**
- Modify: `src/idraa/services/scenarios.py` (new method after `confirm_vuln_framing`, ~L556)
- Modify: `src/idraa/routes/scenarios.py` (new route after `confirm_vuln_framing` route, ~L1094)
- Modify: `src/idraa/templates/scenarios/view.html` (banner after the legacy_residual block ~L62; fix `status_pill(..., kind="control")` → `kind="entity"` at L69)
- Modify: `src/idraa/templates/scenarios/form.html:69` (hidden status input → visible select with `active`/`draft`)
- Test: append to `tests/integration/test_draft_workflow.py`

**Interfaces:**
- Produces: `ScenarioService.promote(*, organization_id: uuid.UUID, scenario_id: uuid.UUID, current_user: User, ip_address: str | None = None) -> Scenario`; route `POST /scenarios/{scenario_id}/promote` (ANALYST/ADMIN, 303 → `/scenarios/{id}`). Audit action string: `"scenario.promote"`.

- [ ] **Step 1: Failing tests** (append):

```python
@pytest.mark.asyncio
async def test_promote_flips_draft_to_active_with_audit(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    draft = await _seed_scenario(db_session, org_id=org_id, name="Promotable", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{draft.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 303
    await db_session.refresh(draft)
    assert draft.status == EntityStatus.ACTIVE
    from sqlalchemy import select
    from idraa.models.audit import AuditLog
    row = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "scenario.promote"))).scalars().first()
    assert row is not None and row.entity_id == draft.id


@pytest.mark.asyncio
async def test_promote_idempotent_on_active(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    s = await _seed_scenario(db_session, org_id=org_id, name="Already Active", status=EntityStatus.ACTIVE)
    await db_session.commit()
    prev = s.row_version
    r = await csrf_post(client, f"/scenarios/{s.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.row_version == prev  # no bump, no audit on no-op


@pytest.mark.asyncio
async def test_promote_refuses_unconfirmed_legacy_residual(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    d = await _seed_scenario(db_session, org_id=org_id, name="Residual Draft", status=EntityStatus.DRAFT)
    d.vuln_framing = "legacy_residual"
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{d.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 422
    await db_session.refresh(d)
    assert d.status == EntityStatus.DRAFT


@pytest.mark.asyncio
async def test_promote_forbidden_for_reviewer(authed_reviewer, db_session: AsyncSession):
    client, org_id = authed_reviewer
    d = await _seed_scenario(db_session, org_id=org_id, name="RBAC Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{d.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 403
```

- [ ] **Step 2: Verify all FAIL** (404 on the missing route).

- [ ] **Step 3: Implement.** Service (mirror `confirm_vuln_framing` exactly — lock=True read, NotFoundError, idempotent return, row_version bump, flush, audit):

```python
    async def promote(
        self, *, organization_id: uuid.UUID, scenario_id: uuid.UUID,
        current_user: User, ip_address: str | None = None,
    ) -> Scenario:
        """DRAFT → ACTIVE after review (epic #34 P1a). Idempotent on ACTIVE.

        Refuses while vuln_framing == "legacy_residual": the reviewer must
        confirm inherent framing first (spec §4 — P1a implements the strict
        subset; the acknowledge-in-dialog path arrives with P1c).
        """
        repo = ScenarioRepo(self._db)
        scenario = await repo.get_for_org(
            organization_id=organization_id, scenario_id=scenario_id, lock=True)
        if scenario is None:
            raise NotFoundError(f"scenario {scenario_id} not found")
        if scenario.status == EntityStatus.ACTIVE:
            return scenario
        if scenario.status != EntityStatus.DRAFT:
            raise ValidationError(f"only draft scenarios can be promoted (status={scenario.status.value})")
        if scenario.vuln_framing == "legacy_residual":
            raise ValidationError("confirm vulnerability framing before promoting — see the banner on this scenario")
        prev_row_version = scenario.row_version
        scenario.status = EntityStatus.ACTIVE
        scenario.row_version = prev_row_version + 1
        await self._db.flush()
        await AuditWriter(self._db).log(
            organization_id=organization_id, entity_type="scenario",
            entity_id=scenario.id, action="scenario.promote",
            changes={"status": ["draft", "active"], "row_version": [prev_row_version, scenario.row_version]},
            user_id=current_user.id, ip_address=ip_address)
        return scenario
```

Route: copy the `confirm_vuln_framing` route shape verbatim (`routes/scenarios.py:1070-1094`) at path `/scenarios/{scenario_id}/promote`, adding `except ValidationError as exc: raise HTTPException(status_code=422, detail=str(exc))`. Banner in `view.html` directly after the legacy_residual block, mirroring its structure:

```html
{% if scenario.status.value == 'draft' %}
  <div class="alert alert-info mb-4" role="alert">
    <div>
      <p class="font-semibold">Draft — pending review.</p>
      <p class="text-sm mt-1">This scenario's parameters are starting priors, not calibrated results. It is excluded from analyses and dashboard metrics until promoted.</p>
      {% if current_user and current_user.role.value in ("analyst", "admin") %}
        <form method="post" action="/scenarios/{{ scenario.id }}/promote" class="mt-2">
          {{ csrf_field() }}
          <button type="submit" class="btn btn-sm btn-outline">Promote to active</button>
        </form>
      {% endif %}
    </div>
  </div>
{% endif %}
```

Form: replace the hidden input at `form.html:69` with a labeled select (`active` / `draft`, current value selected), matching the form's existing field markup conventions (read neighboring fields and copy their label/classes). Fix `view.html:69` pill kind to `"entity"`.

- [ ] **Step 4: Run** all of `tests/integration/test_draft_workflow.py` + `tests/integration/test_scenario_routes.py -q`.

- [ ] **Step 5: Commit** — `feat(scenarios): DRAFT promote flow with audit + banner (epic #34 P1a)`

### Task 4: Exclusion-totality contract test + gate

**Files:**
- Create: `tests/arch/test_draft_exclusion_sweep.py`
- Test: itself.

**Interfaces:** consumes nothing new; freezes Tasks 1-2.

- [ ] **Step 1: Write the sweep test** — every `select(Scenario` / `ScenarioRepo` query site in `src/idraa/` must be in the audited allowlist, so any FUTURE query surface fails CI until someone decides draft-inclusion explicitly:

```python
"""Draft-exclusion totality sweep (epic #34 P1a, spec §4).

Any new code that queries Scenario rows must be added to AUDITED with an
explicit draft-handling decision, or this test fails. This is what makes
"drafts are excluded from computation" provably total instead of a hand-list.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "idraa"

# path -> decision ("excludes-drafts" | "shows-all-by-design" | "run-committed-upstream-gated")
AUDITED = {
    "routes/runs.py": "excludes-drafts",            # picker filters ACTIVE (P1a T2)
    "services/runs.py": "excludes-drafts",          # server-side gate (P1a T1)
    "services/run_executor.py": "run-committed-upstream-gated",  # defense-in-depth guard (P1a T1)
    "repositories/scenario_repo.py": "shows-all-by-design",      # primitives; callers decide
    "routes/scenarios.py": "shows-all-by-design",   # list/view/export show drafts (spec §4)
    "services/scenarios.py": "shows-all-by-design", # CRUD on explicit ids
    "services/dashboard.py": "excludes-drafts",     # ACTIVE-only counts (P1a T2)
    "services/attack_coverage.py": "excludes-drafts",  # pre-existing ACTIVE filter
    "services/scenario_import.py": "shows-all-by-design",  # dedup vs ACTIVE names (P1b revisits)
    "services/reports.py": "run-committed-upstream-gated",
}

QUERY_RE = re.compile(r"select\(\s*Scenario\b|ScenarioRepo\(")


def test_every_scenario_query_site_is_audited() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if QUERY_RE.search(path.read_text(encoding="utf-8")) and rel not in AUDITED:
            offenders.append(rel)
    assert not offenders, (
        f"unaudited Scenario query sites {offenders}: add each to AUDITED in "
        "tests/arch/test_draft_exclusion_sweep.py with an explicit draft-handling decision"
    )


def test_audited_files_still_query_scenarios() -> None:
    stale = [rel for rel in AUDITED
             if not (SRC / rel).exists() or not QUERY_RE.search((SRC / rel).read_text(encoding="utf-8"))]
    assert not stale, f"stale AUDITED entries (file gone or no longer queries Scenario): {stale}"
```

- [ ] **Step 2: Run it** — expect failure listing every current query site NOT yet in AUDITED; reconcile the real list against the map above (the implementer MUST resolve discrepancies by reading each file and classifying it, not by blind-adding).

- [ ] **Step 3: Make it pass**, then run the FULL gate foreground: `uv run python scripts/run_local_gate.py` — all steps green.

- [ ] **Step 4: Commit** — `test(arch): draft-exclusion totality sweep (epic #34 P1a)`

---

## Final

PR per project flow (branch `feat/34-p1a-draft-workflow`, push `--no-verify` only if the gate is already green on that HEAD, `Fixes` nothing — epic stays open; body links spec §4). 4-reviewer final PR-gate per epic-milestone ceremony BEFORE merge.
