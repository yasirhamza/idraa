# Scenario DRAFT Workflow (epic #34 P1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First real use of `EntityStatus.DRAFT` on Scenario: draft scenarios are visible/editable but excluded from run creation, dashboard metrics, and coverage until an audit-logged promote flips them ACTIVE.

**Architecture:** No schema change (enum member + `status` column already exist; no migration). Enforcement is server-side in `RunService.create_and_dispatch` (the form picker filter is convenience only), with defense-in-depth in the run executor. Promote mirrors the shipped `confirm_vuln_framing` service/route/banner pattern, and is the ONLY status-transition path — `update()` rejects status changes (plan-gate B-1). A query-site allowlist tripwire makes the exclusion sweep enumerable-and-audited.

**Acknowledged deviation from spec §4 (plan-gate Spec-I4):** the spec prescribes one central `include_drafts=False` chokepoint. No such chokepoint exists — there is no service-layer list wrapper; consumers query `ScenarioRepo` primitives directly. Adding one would mean refactoring every consumer in the same PR. Instead, exclusion lands at each consumer (Tasks 1-2) and Task 4's tripwire supplies the totality property the spec's chokepoint was after: any NEW query surface fails CI until it carries an explicit draft decision. Equivalent guarantee, smaller diff.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Jinja2/HTMX, pytest + httpx.

**Spec:** `docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md` §4 (P1a slice).

## Global Constraints

- `EntityStatus` is NOT currently imported in `services/runs.py`, `services/run_executor.py`, `services/dashboard.py`, or `services/scenarios.py` — each task adding a snippet that references it must add `from idraa.models.enums import EntityStatus` (`scenario_repo.py` already imports it).

- Scenario list/view/export SHOW drafts (visibility is the point of review); only computation/metrics exclude them.
- Promote is idempotent, audit-logged (`AuditWriter`), row-version-bumping — exactly the `confirm_vuln_framing` shape (`services/scenarios.py:509-556`).
- Promote REFUSES while `vuln_framing == "legacy_residual"` (stricter P1a subset of spec's "confirm OR acknowledge"; the acknowledge path arrives with P1c's dialog).
- No demote (deprecation exists). No new Settings keys. No `.strftime` in templates (use existing filters). All new POSTs rely on the global CSRF middleware + `csrf_field()`.
- Copy discipline: drafts are "pending review", never "results" (spec §7.1).

---

### Task 1: Server-side DRAFT gate at run creation + executor defense-in-depth

**Files:**
- Modify: `src/idraa/services/runs.py` (in `create_and_dispatch`, after the scenario fetch at ~L103-115)
- Modify: `src/idraa/services/run_executor.py` (ONE guard after both branches converge, ~L1887)
- Test: `tests/integration/test_draft_workflow.py` (new)

**Interfaces:**
- Consumes: `ScenarioRepo.get_for_org_or_raise` / `fetch_by_ids_for_org` (existing, unchanged).
- Produces: `RunValidationError("scenario '<name>' is a draft — promote it before running an analysis")` raised from `create_and_dispatch` for ANY non-ACTIVE scenario in `scenario_ids`. Task 2's picker and Task 4's contract test rely on this being the authoritative gate.

- [x] **Step 1: Write the failing tests**

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
    draft = _seed_scenario(db_session, org_id=org_id, name="Draft S", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, "/analyses", {"scenario_ids": [str(draft.id)], "mc_iterations": "1000"}, follow_redirects=False)
    assert r.status_code == 422
    assert "draft" in r.text.lower()


@pytest.mark.asyncio
async def test_run_create_rejects_mixed_active_and_draft(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    active = _seed_scenario(db_session, org_id=org_id, name="Active S", status=EntityStatus.ACTIVE)
    draft = _seed_scenario(db_session, org_id=org_id, name="Draft T", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, "/analyses", {"scenario_ids": [str(active.id), str(draft.id)], "mc_iterations": "1000"}, follow_redirects=False)
    assert r.status_code == 422
```

- [x] **Step 2: Run to verify both FAIL** — `uv run pytest tests/integration/test_draft_workflow.py -q` (expect 200/303-shaped success today, i.e. assertion failures).

- [x] **Step 3: Implement the gate.** In `services/runs.py::create_and_dispatch`, immediately after `scenarios` is populated (both branches):

```python
        # Epic #34 P1a: DRAFT scenarios are review-pending priors — never
        # runnable. Server-side gate; the /analyses/new picker filter is
        # convenience only and trivially bypassed.
        non_active = [s for s in scenarios if s.status != EntityStatus.ACTIVE]
        if non_active:
            names = ", ".join(sorted(s.name for s in non_active))
            raise RunValidationError(
                f"scenario '{names}' is a draft — promote it before running an analysis"
            )
```

(`RunValidationError` is the module's existing validation type — `services/runs.py:98,125,137` — and the ONLY type the route maps to 422 at `routes/runs.py:1001-1002`. Do not invent a new exception.)

In `services/run_executor.py`, add ONE defense-in-depth guard AFTER BOTH branches have populated `scenarios` — i.e. after `scenarios = [scenario]` (~L1857) and the AGGREGATE `fetch_by_ids_for_org` (~L1863) converge; placing it at L1852 would NameError on the SINGLE branch where only `scenario` is bound:

```python
        # Epic #34 P1a defense-in-depth: the create-time gate is authoritative;
        # this guard only fires if a future path enqueues a run for a draft.
        if any(s.status != EntityStatus.ACTIVE for s in scenarios):
            raise ValueError("run references a non-ACTIVE scenario (draft?) — refusing to execute")
```

(`ValueError` is deliberate: the guard sits inside the executor try at ~L1818 whose `except Exception` at ~L2512 terminalizes the run to FAILED with `error_message` + audit — verified failure path, no stuck RUNNING rows.)

- [x] **Step 4: Run tests to verify both PASS**, plus `uv run pytest tests/integration/test_run_routes.py tests/services/test_runs*.py -q` for regressions.

- [x] **Step 5: Commit** — `git commit -m "feat(runs): server-side DRAFT scenario gate at run creation (epic #34 P1a)"`

### Task 2: Picker, dashboard, and coverage exclusions

**Files:**
- Modify: `src/idraa/routes/runs.py:904-909` (`get_new_analysis_form` scenario query)
- Modify: `src/idraa/repositories/scenario_repo.py:99-120` (`list_pinned_library_entry_ids_for_org`)
- Modify: `src/idraa/services/dashboard.py:259` (`scenario_count`)
- Test: append to `tests/integration/test_draft_workflow.py`

**Interfaces:**
- Consumes: `EntityStatus`, existing repo methods.
- Produces: `list_pinned_library_entry_ids_for_org(organization_id, *, statuses: tuple[EntityStatus, ...] = (EntityStatus.ACTIVE,))` — dashboard/coverage callers use the default; pass explicitly if other statuses are ever wanted.

- [x] **Step 1: Failing tests** (append):

```python
@pytest.mark.asyncio
async def test_new_analysis_picker_omits_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Visible Active", status=EntityStatus.ACTIVE)
    _seed_scenario(db_session, org_id=org_id, name="Hidden Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await client.get("/analyses/new")
    assert "Visible Active" in r.text and "Hidden Draft" not in r.text


@pytest.mark.asyncio
async def test_dashboard_counts_exclude_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Counted", status=EntityStatus.ACTIVE)
    _seed_scenario(db_session, org_id=org_id, name="Not Counted", status=EntityStatus.DRAFT)
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

- [x] **Step 2: Verify failure** (picker test fails; count test fails only if dashboard still counts all — the repo-level call with `status=` already works, so the load-bearing edits are the call sites).

- [x] **Step 3: Implement** — picker: add `.where(Scenario.status == EntityStatus.ACTIVE)` to the `_scenario_stmt` in `get_new_analysis_form`; dashboard: `count_for_org(organization_id=org.id, status=EntityStatus.ACTIVE)`; repo: add `statuses` keyword (default `(EntityStatus.ACTIVE,)`) to `list_pinned_library_entry_ids_for_org` with `.where(Scenario.status.in_(statuses))`. Grep `list_pinned_library_entry_ids_for_org` callers and leave non-dashboard callers (if any) on explicit previous behavior only if a test proves they need drafts — default them to ACTIVE otherwise.

- [x] **Step 4: Run** new tests + `uv run pytest tests/integration/test_dashboard* tests/services/test_dashboard* -q`.

- [x] **Step 5: Commit** — `feat(dashboard): exclude DRAFT scenarios from picker, counts, coverage (epic #34 P1a)`

### Task 3: Promote flow + UI

**Files:**
- Modify: `src/idraa/services/scenarios.py` (new method after `confirm_vuln_framing`, ~L556)
- Modify: `src/idraa/routes/scenarios.py` (new route after `confirm_vuln_framing` route, ~L1094)
- Modify: `src/idraa/templates/scenarios/view.html` (banner after the legacy_residual block ~L62; fix `status_pill(..., kind="control")` → `kind="entity"` at L69)
- Modify: `src/idraa/services/scenarios.py` `update()` guard at top ~L405 (reject status transitions — plan-gate B-1/SEC-R2-1) + `_stamp_new_scenario` ~L162 (create status domain — SEC-R2-2/SEC-R3-NTH)
- Modify: `src/idraa/templates/scenarios/form.html:69` (CREATE mode only: labeled select `active`/`draft`; EDIT mode: keep the hidden preserve-mirror EXACTLY as-is)
- Modify: `src/idraa/templates/scenarios/list.html` (status filter chips — plan-gate Spec-I1)
- Test: append to `tests/integration/test_draft_workflow.py`

**Interfaces:**
- Produces: `ScenarioService.promote(*, organization_id: uuid.UUID, scenario_id: uuid.UUID, current_user: User, ip_address: str | None = None) -> Scenario`; route `POST /scenarios/{scenario_id}/promote` (ANALYST/ADMIN, 303 → `/scenarios/{id}`). Audit action string: `"scenario.promote"`.

- [x] **Step 1: Failing tests** (append):

```python
@pytest.mark.asyncio
async def test_promote_flips_draft_to_active_with_audit(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    draft = _seed_scenario(db_session, org_id=org_id, name="Promotable", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{draft.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 303
    await db_session.refresh(draft)
    assert draft.status == EntityStatus.ACTIVE
    from sqlalchemy import select
    from idraa.models.audit_log import AuditLog
    row = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "scenario.promote"))).scalars().first()
    assert row is not None and row.entity_id == draft.id


@pytest.mark.asyncio
async def test_promote_idempotent_on_active(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    s = _seed_scenario(db_session, org_id=org_id, name="Already Active", status=EntityStatus.ACTIVE)
    await db_session.commit()
    prev = s.row_version
    r = await csrf_post(client, f"/scenarios/{s.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 303
    await db_session.refresh(s)
    assert s.row_version == prev  # no bump, no audit on no-op


@pytest.mark.asyncio
async def test_promote_refuses_unconfirmed_legacy_residual(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    d = _seed_scenario(db_session, org_id=org_id, name="Residual Draft", status=EntityStatus.DRAFT)
    d.vuln_framing = "legacy_residual"
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{d.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 422
    await db_session.refresh(d)
    assert d.status == EntityStatus.DRAFT


@pytest.mark.asyncio
async def test_promote_forbidden_for_reviewer(authed_reviewer, db_session: AsyncSession):
    client, org_id = authed_reviewer
    d = _seed_scenario(db_session, org_id=org_id, name="RBAC Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(client, f"/scenarios/{d.id}/promote", {}, follow_redirects=False)
    assert r.status_code == 403
```

- [x] **Step 2: Verify all FAIL** (404 on the missing route).

- [x] **Step 3: Implement.** Service (mirror `confirm_vuln_framing` exactly — lock=True read, NotFoundError, idempotent return, row_version bump, flush, audit):

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

(P1a-subset note, plan-gate Spec-N2: the banner above is the P1a slice of spec §4's conversion-provenance banner; band-binding provenance content arrives with P1b/P1c when `conversion_metadata` exists.)

**Status field (plan-gate B-1 — triple-converged BLOCKER; read carefully).** `ScenarioService.update()` currently writes `scenario.status = form.status` unconditionally (`services/scenarios.py:467`), so a visible select on the EDIT form would be an unguarded second promote/demote path bypassing the legacy_residual refusal and the `scenario.promote` audit action. Therefore:

1. **Harden `update()` — guard at the TOP (plan-gate SEC-R2-1):** the check goes as the FIRST statement after the scenario is loaded — BEFORE `validate_fair_distributions`, BEFORE the vuln_framing auto-flip, BEFORE any field assignment. Rationale: the route catches `ValidationError` and RETURNS a 422 re-render (`routes/scenarios.py:937-948`) — a successful handler exit — and `get_db` auto-commits pending dirty state (the in-code "Sec2-I2" hazard). A later guard would turn "status rejected" into silently-committed, unaudited, non-row-version-bumped edits of every other field. Add:

```python
        if form.status != scenario.status:
            # Epic #34 P1a (plan-gate B-1): status transitions go ONLY through
            # the audited promote flow — the edit path must not be a second,
            # unguarded promote/demote surface.
            raise ValidationError("status cannot be changed here — use Promote on the scenario page")
```

(Match the module's existing validation-error type for update-path 422s — read how `update()` surfaces validation errors today and use that exact type + route mapping.)

1b. **Create-path status domain (plan-gate SEC-R2-2, placement per SEC-R3-NTH):** in `_stamp_new_scenario` (services/scenarios.py ~L162, NEXT TO the existing `validate_fair_distributions` call — the shared chokepoint whose docstring says validation happens exactly once regardless of entry path, so `create_from_wizard` is covered too), add the service-side counterpart of the create-select's template constraint:

```python
        if form.status not in (EntityStatus.ACTIVE, EntityStatus.DRAFT):
            raise ValidationError("new scenarios may only be created as active or draft")
```

with test (add in Step 1):

```python
@pytest.mark.asyncio
async def test_create_rejects_non_lifecycle_status(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    # copy the create payload dict verbatim from
    # test_create_scenario_persists_and_redirects (test_scenario_routes.py:310-340)
    # into `payload`, then override status="deprecated"
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 422
    from sqlalchemy import select as _sel
    from idraa.models.scenario import Scenario
    rows = (await db_session.execute(_sel(Scenario).where(
        Scenario.name == payload["name"]))).scalars().all()
    assert rows == []                               # no row created
```

2. **Template:** in `form.html`, render the status field as a labeled select (`active` / `draft`) ONLY in create mode; in edit mode keep the existing hidden preserve-mirror at L69 untouched. Determine create-vs-edit the way the template already does (inspect how it branches for the form action/heading; reuse that condition).

3. **Filter chips (Spec-I1):** `GET /scenarios` already accepts `?status=` (`routes/scenarios.py:167-213`). Add a chip row above the list table in `list.html`: `All` (no param), `Active` (`?status=active`), `Draft` (`?status=draft`), current one visually active — match the template's existing link/badge classes (read neighboring markup for precedent).

Fix `view.html:69` pill kind to `"entity"`.

Extra failing tests to add in Step 1 (plan-gate B-1/Spec-I1):

```python
@pytest.mark.asyncio
async def test_edit_form_cannot_change_status(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    d = _seed_scenario(db_session, org_id=org_id, name="Sticky Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    # build the full valid edit payload the way the existing update-route test
    # in tests/integration/test_scenario_routes.py does; then set status=active
    # co-mutate another field: SEC-R2-1 asserts rejection leaves the session
    # CLEAN — no silent unaudited commit of the other edits
    payload = _valid_update_payload_for(d) | {"status": "active", "description": "sneaky edit"}
    r = await csrf_post(client, f"/scenarios/{d.id}", payload, follow_redirects=False)
    assert r.status_code == 422
    await db_session.refresh(d)
    assert d.status == EntityStatus.DRAFT
    assert d.description != "sneaky edit"          # nothing committed
    from sqlalchemy import select as _sel
    from idraa.models.audit_log import AuditLog
    upd = (await db_session.execute(_sel(AuditLog).where(
        AuditLog.action == "scenario.update", AuditLog.entity_id == d.id))).scalars().all()
    assert upd == []                                # no audit row for the rejected edit


@pytest.mark.asyncio
async def test_create_as_draft_works(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    # copy the create payload dict verbatim from
    # test_create_scenario_persists_and_redirects (test_scenario_routes.py:310-340),
    # override status="draft"; POST /scenarios; assert 303 and the row is DRAFT


@pytest.mark.asyncio
async def test_scenario_list_has_draft_filter_chip(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Chip Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await client.get("/scenarios")
    assert "?status=draft" in r.text          # chip present
    r2 = await client.get("/scenarios?status=draft")
    assert "Chip Draft" in r2.text
```

(`_valid_update_payload_for` is a tiny local helper the implementer builds from the existing test's payload dict; the create-as-draft body is payload reuse from the named test, not a design placeholder.)

- [x] **Step 4: Run** all of `tests/integration/test_draft_workflow.py` + `tests/integration/test_scenario_routes.py -q`.

- [x] **Step 5: Commit** — `feat(scenarios): DRAFT promote flow with audit + banner (epic #34 P1a)`

### Task 4: Exclusion-totality contract test + gate

**Files:**
- Create: `tests/arch/test_draft_exclusion_sweep.py`
- Test: itself.

**Interfaces:** consumes nothing new; freezes Tasks 1-2.

- [x] **Step 1: Write the sweep test** — every `select(Scenario` / `ScenarioRepo` query site in `src/idraa/` must be in the audited allowlist, so any FUTURE query surface fails CI until someone decides draft-inclusion explicitly:

```python
"""Draft-exclusion totality tripwire (epic #34 P1a, spec §4).

Any new code that queries Scenario rows must be added to AUDITED with an
explicit draft-handling decision, or this test fails. Enumeration is a
source-pattern sweep over every KNOWN query idiom (select / db.get / join /
selectinload / aliased / repo construction) — a tripwire, not a proof.
Accepted blind spots (plan-gate Arch-I2/Spec-I5): raw SQL and relationship
loads reached from OTHER entities' queries; anyone adding those for Scenario
must extend QUERY_RE alongside.
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

QUERY_RE = re.compile(
    r"select\([^)]*\bScenario\b"      # select(Scenario…), select(func.x(Scenario.…
    r"|\.get\(\s*Scenario\b"          # db.get(Scenario, id)
    r"|\bjoin\(\s*Scenario\b"         # .join(Scenario, …)
    r"|selectinload\(\s*Scenario\."   # selectinload(Scenario.rel)
    r"|aliased\(\s*Scenario\b"         # aliased(Scenario)
    r"|ScenarioRepo\(",
    re.S,
)


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

- [x] **Step 2: Run it** — as of plan-gate round 2 the broadened regex matches EXACTLY the 10 files in the seed map (verified against the tree), so expect green-or-near-green; if the tree drifted, reconcile any surfaced file by READING and classifying it with one of the three decision values, not by blind-adding.

- [x] **Step 3: Make it pass**, then run the FULL gate foreground: `uv run python scripts/run_local_gate.py` — all steps green.

- [x] **Step 4: Commit** — `test(arch): draft-exclusion totality sweep (epic #34 P1a)`

---

## Final

PR per project flow (branch `feat/34-p1a-draft-workflow`, push `--no-verify` only if the gate is already green on that HEAD, `Fixes` nothing — epic stays open; body links spec §4). 4-reviewer final PR-gate per epic-milestone ceremony BEFORE merge.
