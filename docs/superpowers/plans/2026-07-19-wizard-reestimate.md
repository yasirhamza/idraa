# Wizard Re-elicitation Implementation Plan (idraa#56)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the SME estimation wizard against an existing scenario and replace its estimates in place (spec: `docs/superpowers/specs/2026-07-19-wizard-reestimate-design.md`).

**Architecture:** Three seams on the existing wizard: (1) an entry route that seeds a `wizard_drafts` row from a scenario, (2) two new optional `WizardState` targeting fields, (3) a finalize branch calling a new `ScenarioService.update_from_wizard` that shares `update()`'s primitives via a small extraction. Pooling pipeline, step flow, and templates are reused.

**Tech Stack:** FastAPI + SQLAlchemy async + Jinja2/HTMX; pytest + httpx.

## Global Constraints

- Worktree `wt-reestimate`, branch `feat/wizard-reestimate`; NEVER background the test/gate commands — run them foreground and wait.
- Eligibility: ALL scenarios, any source/status. Provenance on finalize: `source = ScenarioSource.EXPERT_JUDGMENT`, `library_pin = None`, `vuln_framing = "inherent"`, `status` untouched, `scenario.effect`/`scenario_type`/`version` (descriptive) preserved.
- Optimistic lock: `target_expected_row_version` captured at seed; conflict → 422 review-flash re-render (amendment 5), draft PRESERVED.
- Runs never stale-marked. ATT&CK mappings untouched on the update path.
- SME rows: replace-all in the finalize transaction (delete + `persist_estimates`).
- Legacy drafts (no target keys in `state_json`) must deserialize to `None` defaults (the whitelist loader in `wizard_state.py:get_or_create` already tolerates missing keys — dataclass defaults apply).
- Commit messages: `feat(...): ... (#56)` with the session trailers used on this branch.
- All tests run as `uv run pytest <path> -q --no-cov` from the worktree root.

## Plan-gate round-1 amendments (BINDING — override base task text on conflict)

1. **Meth-B1 (was BLOCKER):** `seed_wizard_state_from_scenario` must EXCLUDE
   the `vuln` fieldset from rehydration when
   `scenario.vuln_framing == "legacy_residual"` (tef/pl/sl still rehydrate).
   The seed fixture carries `vuln_framing` and Task 1 gains two tests:
   legacy_residual → no "vuln" key in `sme_estimates` (others present);
   "inherent" → all fieldsets rehydrated. Simplest implementation: the seed
   function filters the passed dict:
   `if getattr(scenario, "vuln_framing", None) == "legacy_residual": sme_estimates = {k: v for k, v in sme_estimates.items() if k != "vuln"}`.
2. **Arch-I1:** the re-estimate finalize branch passes
   `eligible_control_ids` to `set_mitigating_controls`, mirroring the #217
   edit-path scoping (routes/scenarios.py:~955-968): compute the ACTIVE
   control-id set the step-4 picker rendered (`ControlRepo.list_for_org`)
   and scope removals to it, so links to DRAFT/DEPRECATED controls survive.
   Task 4 gains a test: a scenario linked to a DEPRECATED control keeps
   that link through re-estimation.
3. **Arch-I2 / Spec-N1:** drop `tags` everywhere — no `tags=` line in the
   seed function, no `tags` in the SimpleNamespace fixture, no tags
   assertion. Fixtures must not invent columns the ORM lacks.
4. **Spec-I1:** Task 4 requires three import additions in
   `routes/scenarios.py`: `delete` (extend the existing
   `from sqlalchemy import select` line), `ScenarioSMEEstimate`
   (`from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate`),
   and `ScenarioVersionConflictError`
   (`from idraa.services.scenarios import ScenarioVersionConflictError` —
   or extend the module's existing services.scenarios import).
5. **Spec-I2 + Arch-N2:** the conflict path uses
   `_render_review_with_flash` (HTTP **422**, the finalize-error idiom) —
   NOT 409. The dispatch's except clause catches
   `(ScenarioVersionConflictError, NotFoundError)` and routes both to the
   flash (rollback first; draft survives). For the `NotFoundError` branch
   use the fixed friendly message ("This scenario no longer exists — it was
   deleted while you were estimating. Cancel to discard this draft."), not
   `str(exc)` (Sec-R2-N1). Task 4 test renamed to
   `test_finalize_conflict_renders_review_flash_and_preserves_draft`,
   asserting status 422 + message + draft row still present + scenario
   unchanged. Import `NotFoundError` from `idraa.errors`.
6. **Spec-I3:** Task 4 gains `test_cancel_targeted_draft_is_noop`: POST the
   wizard cancel on a targeted draft → scenario row_version unchanged,
   draft row deleted.
7. **Meth-N2:** `update_from_wizard` also clears
   `scenario.conversion_metadata` (set to `None`) and includes it in the
   audit extras when it changed.
8. **Arch-N1 (prescribed, not optional):** implement the extras variant
   that keeps `_audit_diff` generic: capture the standard `before` dict
   only; hold `source`/`library_pin`/`vuln_framing`/`conversion_metadata`
   before-values in separate locals; append their `[before, after]` pairs
   in the explicit loop. Do NOT extend the `before` dict.
9. **Arch-N4 / Sec-N2:** no `or 0` coalesce — the route dispatch guards:
   `if state.target_expected_row_version is None: raise HTTPException(500, "re-estimate draft missing its row-version capture")`
   (impossible state, fail loud).
10. **Sec-N1:** the Task 3 button uses the literal `{{ csrf_field() }}`
    global (the Promote form idiom) — `csrf_input` does not exist.
11. **Arch-N3:** Task 3 gains a direct `load_sme_rows` contract test
    (db-backed): 2 fieldsets, ≥3 rows each, mixed `sme_id`/`sme_name`
    identities — asserts grouping, ordering, and full row survival.
12. **Refuted at gate (do not "fix"):** `Scenario.mitigating_controls` is
    `lazy="selectin"` — the entry route's attribute access is eager-loaded
    and safe; ignore the plan's "may need eager-load variant" caution.
13. **Meth-N3:** Task 5's step-6 targeted copy adds: "Estimation math may
    have been updated since this scenario was last estimated — pooled
    distributions can shift even if you keep every value."
14. **Arch-R2-I1:** the `conversion_metadata = None` clear (amendment 7) is
    PINNED BY TESTS: Task 4 test 4 asserts `scenario.conversion_metadata is
    None` after the register-import upgrade; Task 2 test 5 asserts the
    audit extras carry `changes["conversion_metadata"] == [<before>, None]`
    when the scenario had one.
15. **Arch-R2-N2:** the USD stamp moves INSIDE `update_from_wizard`
    (`scenario.entry_currency = "USD"`, `scenario.entry_rate = None`) so a
    non-USD scenario's currency flip lands in the audit extras
    (`entry_currency` before/after when changed). The route-level stamp
    remains ONLY on the create path. Task 2 gains a test: an EUR-entry
    scenario re-estimated → `entry_currency == "USD"` and
    `changes["entry_currency"] == ["EUR", "USD"]`.

---

### Task 1: WizardState targeting fields + seed function + SME read path

**Files:**
- Modify: `src/idraa/services/wizard_state.py` (dataclass fields + new seed function + SME-row loader)
- Test: `tests/unit/test_wizard_reestimate_seed.py` (new)

**Interfaces:**
- Produces: `WizardState.target_scenario_id: str | None` / `WizardState.target_expected_row_version: int | None` (defaults `None`); `async def load_sme_rows(db, scenario_id, organization_id) -> dict[str, list[dict[str, Any]]]`; `def seed_wizard_state_from_scenario(scenario, *, sme_estimates, mitigating_control_ids, tx_id) -> WizardState`.
- Consumes: `Scenario` ORM, `ScenarioSMEEstimate` ORM.

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_wizard_reestimate_seed.py
"""Unit tests for wizard re-elicitation seeding (#56).

seed_wizard_state_from_scenario is pure: it maps a loaded Scenario (+
pre-fetched SME rows and control ids) into a WizardState targeting that
scenario. The SME-row loader is tested at the service/integration level
(Task 4); here we pin the pure mapping, including the adapter-iteration
contract (N>=3 rows survive per fieldset) and loss_shape derivation.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from idraa.services.wizard_state import WizardState, seed_wizard_state_from_scenario


def _scenario(**over):
    base = dict(
        id=uuid.uuid4(),
        row_version=3,
        name="Ransomware on historian",
        description="desc",
        threat_category=SimpleNamespace(value="ransomware"),
        threat_actor_type=SimpleNamespace(value="organized_crime"),
        asset_class=SimpleNamespace(value="ot_systems"),
        attack_vector="phishing",
        vuln_framing="inherent",
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_seed_targets_scenario_and_captures_row_version():
    s = _scenario()
    st = seed_wizard_state_from_scenario(
        s, sme_estimates={}, mitigating_control_ids=[], tx_id="deadbeef"
    )
    assert st.target_scenario_id == s.id.hex
    assert st.target_expected_row_version == 3
    assert st.current_step == 2
    assert st.tx_id == "deadbeef"
    # Library fields stay None: no pin is involved in a re-estimation.
    assert st.library_entry_id is None and st.override_id is None


def test_seed_copies_descriptive_fields_and_controls():
    cid = uuid.uuid4()
    st = seed_wizard_state_from_scenario(
        _scenario(),
        sme_estimates={},
        mitigating_control_ids=[str(cid)],
        tx_id="t",
    )
    assert st.name == "Ransomware on historian"
    assert st.threat_category == "ransomware"
    assert st.threat_actor_type == "organized_crime"
    assert st.asset_class == "ot_systems"
    assert st.attack_vector == "phishing"
    assert st.mitigating_control_ids == [str(cid)]


def test_seed_loss_shape_derivation():
    pert = _scenario()
    logn = _scenario(primary_loss={"distribution": "lognormal", "mean": 10.0, "sigma": 1.0})
    mix = _scenario(
        primary_loss={
            "distribution": "lognormal_mixture",
            "components": [{"mean": 10.0, "sigma": 1.0, "weight": 1.0}],
        }
    )
    assert seed_wizard_state_from_scenario(
        pert, sme_estimates={}, mitigating_control_ids=[], tx_id="t"
    ).loss_shape == "capped"
    for s in (logn, mix):
        assert seed_wizard_state_from_scenario(
            s, sme_estimates={}, mitigating_control_ids=[], tx_id="t"
        ).loss_shape == "catastrophic"


def test_seed_preserves_all_sme_rows_per_fieldset():
    # Adapter-iteration contract (CLAUDE.md): N>=3 rows survive intact,
    # including the sme_id XOR sme_name identity shape.
    rows = {
        "tef": [
            {"sme_id": str(uuid.uuid4()), "low": 0.1, "high": 2.0},
            {"sme_name": "Alice", "low": 0.2, "high": 3.0},
            {"sme_name": "Bob", "low": 0.3, "high": 4.0},
        ]
    }
    st = seed_wizard_state_from_scenario(
        _scenario(), sme_estimates=rows, mitigating_control_ids=[], tx_id="t"
    )
    assert st.sme_estimates == rows


def test_legacy_residual_scenario_never_rehydrates_vuln_rows():
    # Meth-B1: pre-#339 vuln rows embed a control discount; rehydrating them
    # would make the finalize "inherent" stamp a lie. tef/pl/sl unaffected.
    rows = {
        "tef": [{"sme_name": "A", "low": 0.1, "high": 2.0}],
        "vuln": [{"sme_name": "A", "low": 0.05, "high": 0.4}],
        "pl": [{"sme_name": "A", "low": 1e4, "high": 1e6}],
    }
    st = seed_wizard_state_from_scenario(
        _scenario(vuln_framing="legacy_residual"),
        sme_estimates=rows, mitigating_control_ids=[], tx_id="t",
    )
    assert "vuln" not in st.sme_estimates
    assert set(st.sme_estimates) == {"tef", "pl"}


def test_inherent_scenario_rehydrates_all_fieldsets():
    rows = {"vuln": [{"sme_name": "A", "low": 0.05, "high": 0.4}]}
    st = seed_wizard_state_from_scenario(
        _scenario(), sme_estimates=rows, mitigating_control_ids=[], tx_id="t"
    )
    assert st.sme_estimates == rows


def test_legacy_state_json_without_target_keys_deserializes_to_none():
    # The whitelist loader drops unknown keys and dataclass defaults fill
    # missing ones — a pre-#56 draft must load with target fields None.
    st = WizardState(tx_id="t")
    assert st.target_scenario_id is None
    assert st.target_expected_row_version is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_wizard_reestimate_seed.py -q --no-cov`
Expected: FAIL / ImportError (`seed_wizard_state_from_scenario` not defined).

- [ ] **Step 3: Implement**

In `src/idraa/services/wizard_state.py`, add to the `WizardState` dataclass, after `sme_estimates` and BEFORE `version_token` (keep `version_token` last with its comment intact):

```python
    # #56 wizard re-elicitation: when set, finalize UPDATES this existing
    # scenario in place instead of creating a new one. target_scenario_id is
    # the hex UUID; target_expected_row_version is the scenario's row_version
    # captured at seed time (the edit form's optimistic-lock primitive,
    # carried through the wizard's lifetime — finalize raises
    # ScenarioVersionConflictError on mismatch). Legacy drafts lack both keys
    # in state_json and fall to None on load (create path, unchanged).
    target_scenario_id: str | None = None
    target_expected_row_version: int | None = None
```

Add at module bottom (imports at top of file: `from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate` — plus `TYPE_CHECKING` import of `Scenario` if needed for the annotation):

```python
async def load_sme_rows(
    db: AsyncSession,
    scenario_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> dict[str, list[dict[str, Any]]]:
    """#56: rehydrate persisted per-SME elicitation rows for re-estimation.

    First-ever read path for scenario_sme_estimates (written by
    wizard_finalize.persist_estimates, previously write-only). Returns the
    exact shape WizardState.sme_estimates carries and
    process_sme_estimates consumes: {fieldset: [{sme_id|sme_name, low,
    high}]}. Row order follows recorded_at then id for determinism.
    """
    rows = (
        (
            await db.execute(
                select(ScenarioSMEEstimate)
                .where(
                    ScenarioSMEEstimate.scenario_id == scenario_id,
                    ScenarioSMEEstimate.organization_id == organization_id,
                )
                .order_by(ScenarioSMEEstimate.recorded_at, ScenarioSMEEstimate.id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        entry: dict[str, Any] = {"low": r.low, "high": r.high}
        if r.sme_id is not None:
            entry["sme_id"] = str(r.sme_id)
        else:
            entry["sme_name"] = r.sme_name
        out.setdefault(r.fieldset.value, []).append(entry)
    return out


def seed_wizard_state_from_scenario(
    scenario: Any,
    *,
    sme_estimates: dict[str, list[dict[str, Any]]],
    mitigating_control_ids: list[str],
    tx_id: str,
) -> WizardState:
    """#56: build a re-estimation WizardState from a loaded Scenario.

    Pure mapping — callers load the scenario (IDOR-safe, org-scoped), its
    SME rows (load_sme_rows) and control ids, then persist the returned
    state via WizardStateService. current_step=2 skips the library pick:
    provenance flips to EXPERT_JUDGMENT on finalize regardless, so a pin
    would be dead weight. loss_shape derives from the stored primary-loss
    node (storage invariant since #326/#27: catastrophic <=> native
    lognormal family on pl).
    """
    pl = scenario.primary_loss or {}
    kind = str(pl.get("distribution", "")).lower()
    loss_shape = "catastrophic" if kind in ("lognormal", "lognormal_mixture") else "capped"
    if getattr(scenario, "vuln_framing", None) == "legacy_residual":
        # Meth-B1: pre-#339 vuln rows were elicited under the residual
        # wording (control discount baked in). Never rehydrate them — the
        # operator re-enters vuln under the inherent copy, which is what
        # makes finalize's vuln_framing="inherent" stamp truthful.
        sme_estimates = {k: v for k, v in sme_estimates.items() if k != "vuln"}

    def _enum_val(v: Any) -> str | None:
        return getattr(v, "value", v) if v is not None else None

    return WizardState(
        tx_id=tx_id,
        current_step=2,
        target_scenario_id=scenario.id.hex,
        target_expected_row_version=scenario.row_version,
        name=scenario.name,
        description=scenario.description,
        threat_category=_enum_val(scenario.threat_category),
        threat_actor_type=_enum_val(scenario.threat_actor_type),
        asset_class=_enum_val(scenario.asset_class),
        attack_vector=scenario.attack_vector,
        mitigating_control_ids=list(mitigating_control_ids),
        loss_shape=loss_shape,
        sme_estimates=sme_estimates,
    )
```

(Plan-gate resolved: `Scenario` has NO tags column — the seed function and tests above already omit it.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_wizard_reestimate_seed.py tests/unit/test_wizard_state*.py -q --no-cov` — all PASS (existing wizard-state tests must stay green: the new fields must not break `_state_json_excluding_version_token` or the whitelist loader).

- [ ] **Step 5: Commit** — `feat(wizard): targeting fields + re-estimation seeding (#56)`

---

### Task 2: `ScenarioService.update_from_wizard` (+ shared-primitive extraction)

**Files:**
- Modify: `src/idraa/services/scenarios.py`
- Test: `tests/services/test_update_from_wizard.py` (new)

**Interfaces:**
- Consumes: existing `ScenarioRepo.get_for_org(lock=True)`, `ScenarioVersionConflictError`, `validate_fair_distributions`, `AuditWriter`.
- Produces: `async def update_from_wizard(self, *, organization_id, scenario_id, form: ScenarioForm, expected_row_version: int, actor: User, ip_address: str | None = None, per_fieldset_pooling_summary: dict[str, Any] | None = None) -> Scenario`.

**Approach:** extract `update()`'s before-capture / field-apply / diff into private helpers `_capture_audit_before(scenario)`, `_apply_form_fields(scenario, form)`, `_audit_diff(before, scenario)` — a mechanical refactor with NO semantic change to `update()` (its tests must pass untouched). `update_from_wizard` reuses them and adds the wizard-specific semantics.

- [ ] **Step 1: Write failing tests**

```python
# tests/services/test_update_from_wizard.py
"""ScenarioService.update_from_wizard (#56).

Uses the same fixtures/builders as tests/services/test_scenario_service.py
(reuse its _seed helpers / conftest fixtures — read that file first and
mirror its construction idiom exactly rather than inventing new builders).
Assertions below are the contract; adapt setup plumbing to the local idiom.
"""
```

Contract to pin (one test each; write real bodies against the local fixture idiom):

1. `test_updates_distributions_in_place_and_bumps_row_version` — seed a scenario (row_version 1), call `update_from_wizard` with a form carrying new PERT tef/vuln/pl and `expected_row_version=1`; assert same `scenario.id`, distributions replaced, `row_version == 2`.
2. `test_row_version_conflict_raises` — `expected_row_version=99` → `pytest.raises(ScenarioVersionConflictError)`; scenario unchanged.
3. `test_provenance_flip` — seed with `source=ScenarioSource.LIBRARY_DERIVED` and a non-None `library_pin`; after the call `scenario.source is ScenarioSource.EXPERT_JUDGMENT`, `scenario.library_pin is None`, and `vuln_framing == "inherent"` even when the vuln numeric triple is UNCHANGED (the by-construction flip, stronger than `update()`'s changed-only flip).
4. `test_status_and_effect_and_descriptive_version_preserved` — seed ACTIVE with `effect=...`; after call: status/effect/scenario_type/`version` (str label) all unchanged.
5. `test_audit_row_carries_diff_and_pooling_summary` — seed with a populated `conversion_metadata` and `entry_currency="EUR"`; pass `per_fieldset_pooling_summary={"tef": {"n_smes": 2}}`; fetch the latest `audit_logs` row: `action == "scenario.update"`, `changes["source"] == ["library_derived", "expert_judgment"]`, `changes["conversion_metadata"][1] is None` with a non-None before (amendment 14), `changes["entry_currency"] == ["EUR", "USD"]` and `scenario.entry_currency == "USD"` (amendment 15), `changes["per_fieldset_pooling_summary"]["tef"]["n_smes"] == 2`, `changes["row_version"] == [1, 2]`.
6. `test_update_regression_suite_green` — not a new test: existing `tests/services/test_scenario_service.py` (or wherever `update()` is covered — locate via `grep -rn "def test.*update" tests/services/`) passes unchanged after the refactor.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/services/test_update_from_wizard.py -q --no-cov`
Expected: FAIL (`update_from_wizard` attribute missing).

- [ ] **Step 3: Implement**

Refactor inside `src/idraa/services/scenarios.py` — move the existing `before: dict = {...}` literal, the field-assignment block, and the `_val`/`after`/`changes` computation out of `update()` verbatim into:

```python
    @staticmethod
    def _capture_audit_before(scenario: Scenario) -> dict[str, Any]:
        # (verbatim body of update()'s `before = {...}` literal)

    @staticmethod
    def _apply_form_fields(scenario: Scenario, form: ScenarioForm) -> None:
        # (verbatim field-assignment block from update(), name..version)

    @staticmethod
    def _audit_diff(before: dict[str, Any], scenario: Scenario) -> dict[str, list[Any]]:
        # (verbatim _val/after/changes computation from update())
```

`update()` then calls the three helpers where the inline code was — behavior identical (including the vuln-triple conditional flip, which STAYS inline in `update()` between capture and apply).

New method (after `update()`):

```python
    async def update_from_wizard(
        self,
        *,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        form: ScenarioForm,
        expected_row_version: int,
        actor: User,
        ip_address: str | None = None,
        per_fieldset_pooling_summary: dict[str, Any] | None = None,
    ) -> Scenario:
        """#56 wizard re-elicitation finalize delegate.

        Shares update()'s primitives (lock, conflict, FAIRCAM validation,
        before/after audit diff) and layers the wizard semantics on top:
        source -> EXPERT_JUDGMENT, library_pin cleared, vuln_framing forced
        "inherent" BY CONSTRUCTION (the wizard elicits vulnerability under
        the inherent wording — unlike update()'s changed-triple-only flip),
        pooling summary embedded in the audit changes. status / effect /
        scenario_type / descriptive version are the CALLER's responsibility
        to preserve on the form (the route builds the form from the loaded
        scenario); the status-immutability guard still enforces it.
        Never a silent no-op: fitted_at in the metadata sidecar changes on
        every re-elicitation, so the diff is always non-empty — but the
        row_version bump is unconditional anyway for belt-and-braces.
        """
        repo = ScenarioRepo(self._db)
        scenario = await repo.get_for_org(
            organization_id=organization_id, scenario_id=scenario_id, lock=True
        )
        if scenario is None:
            raise NotFoundError(f"scenario_id={scenario_id} not found")
        if scenario.row_version != expected_row_version:
            raise ScenarioVersionConflictError(
                f"scenario row_version conflict: expected_row_version="
                f"{expected_row_version} but actual row_version={scenario.row_version}; "
                f"this scenario was edited while you were estimating — "
                f"start a fresh re-estimate from the scenario page"
            )
        if form.status != scenario.status:
            raise ValidationError(
                "status cannot be changed here — use Promote on the scenario page"
            )
        before = self._capture_audit_before(scenario)  # standard keys ONLY (amendment 8)
        extras_before = {
            "source": scenario.source.value,
            "library_pin": scenario.library_pin,
            "vuln_framing": scenario.vuln_framing,
            "conversion_metadata": scenario.conversion_metadata,
            "entry_currency": scenario.entry_currency,
        }
        validate_fair_distributions(
            threat_event_frequency=form.threat_event_frequency,
            vulnerability=form.vulnerability,
            primary_loss=form.primary_loss,
            secondary_loss=form.secondary_loss,
        )
        self._apply_form_fields(scenario, form)
        scenario.source = ScenarioSource.EXPERT_JUDGMENT
        scenario.library_pin = None
        scenario.vuln_framing = "inherent"
        scenario.conversion_metadata = None  # amendment 7: provenance sidecar
        scenario.entry_currency = "USD"  # amendment 15: wizard elicits USD
        scenario.entry_rate = None
        changes = self._audit_diff(before, scenario)
        for extra, prev in extras_before.items():
            cur = getattr(scenario, extra)
            cur = getattr(cur, "value", cur)
            if prev != cur:
                changes[extra] = [prev, cur]
        prev_row_version = scenario.row_version
        scenario.row_version = prev_row_version + 1
        changes["row_version"] = [prev_row_version, scenario.row_version]
        if per_fieldset_pooling_summary is not None:
            changes["per_fieldset_pooling_summary"] = per_fieldset_pooling_summary
        await self._db.flush()
        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="scenario",
            entity_id=scenario.id,
            action="scenario.update",
            changes=changes,
            user_id=actor.id,
            ip_address=ip_address,
        )
        return scenario
```

NOTE for implementer: amendment 8 is PRESCRIPTIVE — `before` holds the standard keys only; the five extras (`source`, `library_pin`, `vuln_framing`, `conversion_metadata`, `entry_currency`) live in `extras_before` and are diffed solely by the explicit loop, exactly as the code block above shows. Do not extend `before`.

- [ ] **Step 4: Run tests**

`uv run pytest tests/services/test_update_from_wizard.py tests/services/ -q --no-cov -k "scenario"` — new tests PASS, existing scenario-service tests PASS unchanged.

- [ ] **Step 5: Commit** — `feat(scenarios): update_from_wizard service path (#56)`

---

### Task 3: Entry route + Re-estimate button

**Files:**
- Modify: `src/idraa/routes/scenarios.py` (new handler; place near the wizard block)
- Modify: `src/idraa/templates/scenarios/view.html` (action bar)
- Test: `tests/integration/test_wizard_reestimate_routes.py` (new)

**Interfaces:**
- Consumes: Task 1's `load_sme_rows` + `seed_wizard_state_from_scenario`; existing `WizardStateService`, `ScenarioRepo`, `require_role`, CSRF idiom of sibling POST routes (read a neighboring POST handler, e.g. promote, and mirror its dependency/CSRF signature exactly).
- Produces: `POST /scenarios/{scenario_id}/re-estimate` → 303 to `/scenarios/new/wizard/step/2?tx={tx}`.

- [ ] **Step 1: Write failing tests**

```python
# tests/integration/test_wizard_reestimate_routes.py
"""Entry route + button gating for wizard re-elicitation (#56).

Reuse the authed_analyst / authed_reviewer fixtures and _seed_scenario
builder from tests/integration/test_scenario_routes.py (import or mirror
per local conftest idiom).
"""
```

Pin (real bodies per local idiom):

1. `test_reestimate_button_rendered_for_analyst` — GET `/scenarios/{id}`; assert `f'action="/scenarios/{s.id}/re-estimate"'` in body.
2. `test_reestimate_button_absent_for_reviewer` — same page as reviewer: action URL absent.
3. `test_post_seeds_draft_and_redirects_to_step_2` — POST as analyst (CSRF per sibling-route idiom); assert 303 and `Location` matches `^/scenarios/new/wizard/step/2\?tx=[0-9a-f-]+$`; fetch the `wizard_drafts` row for that tx: `state_json["target_scenario_id"] == s.id.hex`, `state_json["target_expected_row_version"] == s.row_version`, `state_json["current_step"] == 2`, `state_json["name"] == s.name`.
4. `test_post_rehydrates_sme_rows` — seed 3 `ScenarioSMEEstimate` rows (2 free-text names + 1 FK id; fieldset "tef") for the scenario; after POST the draft's `state_json["sme_estimates"]["tef"]` has all 3 rows with identity keys intact (N>=3 iteration contract).
5. `test_post_404_on_other_org_scenario` — scenario seeded under a different org → 404.
6. `test_wizard_step_renders_seeded_rows` — after POST, GET `/scenarios/new/wizard/step/3?tx={tx}` (follow the redirect chain the wizard requires); assert the seeded SME names appear in the step-3 HTML. If step gating forbids jumping to 3 before submitting 2, submit step 2 with the seeded values first (mirror the existing wizard-flow integration test's step-walk idiom — find it via `grep -rn "wizard/step" tests/integration/ | head`).

- [ ] **Step 2: Run to verify failure** — route 404/405, template assertion failures.

- [ ] **Step 3: Implement route**

```python
@router.post("/scenarios/{scenario_id}/re-estimate")
async def start_reestimate_wizard(
    scenario_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> RedirectResponse:
    """#56: seed a re-estimation wizard draft from an existing scenario.

    Eligibility is universal (any source/status — owner decision): imports
    seed with empty SME rows; wizard-born scenarios rehydrate theirs from
    scenario_sme_estimates. The scenario itself is untouched until
    finalize; Cancel abandons the draft with no effect.
    """
    scenario = await ScenarioRepo(db).get_for_org(
        organization_id=user.organization_id, scenario_id=scenario_id
    )
    if scenario is None:
        raise HTTPException(404, "scenario not found")
    sme_rows = await load_sme_rows(db, scenario.id, user.organization_id)
    control_ids = [str(c.id) for c in (scenario.mitigating_controls or [])]
    wizard_svc = WizardStateService(db)
    state = await wizard_svc.get_or_create(
        user_id=user.id, organization_id=user.organization_id
    )
    seeded = seed_wizard_state_from_scenario(
        scenario,
        sme_estimates=sme_rows,
        mitigating_control_ids=control_ids,
        tx_id=state.tx_id,
    )
    seeded.version_token = state.version_token
    await wizard_svc.advance_step(
        user_id=user.id, organization_id=user.organization_id, state=seeded
    )
    await db.commit()
    return RedirectResponse(
        url=f"/scenarios/new/wizard/step/2?tx={seeded.tx_id}", status_code=303
    )
```

Mirror the module's actual CSRF handling for POST routes (if sibling POSTs take a `_csrf` form dependency, add the same). Check how `scenario.mitigating_controls` is loaded (may need the repo's eager-load variant — mirror the edit GET handler's loading).

- [ ] **Step 4: Add the button** in `view.html`'s analyst/admin actions (next to Edit), matching the sibling action markup/classes; it is a small POST form with the CSRF hidden input used by the Promote form:

```html
<form method="post" action="/scenarios/{{ scenario.id }}/re-estimate" class="inline">
  {{ csrf_field() }}
  <button type="submit" class="btn btn-sm">Re-estimate</button>
</form>
```

- [ ] **Step 5: Run tests** — `uv run pytest tests/integration/test_wizard_reestimate_routes.py -q --no-cov` PASS; also `tests/integration/test_scenario_routes.py` stays green.

- [ ] **Step 6: Commit** — `feat(routes): re-estimate entry seeds a targeted wizard draft (#56)`

---

### Task 4: Finalize branch (update-in-place)

**Files:**
- Modify: `src/idraa/routes/scenarios.py:finalize_wizard` (the block from `library_pin` construction through the redirect — see current lines ~2328-2400)
- Test: `tests/integration/test_wizard_reestimate_finalize.py` (new)

**Interfaces:**
- Consumes: Task 2's `update_from_wizard`; existing `build_scenario_payload`, `persist_estimates`, `_PAYLOAD_TO_FORM`, `_render_review_with_flash`, `ScenarioVersionConflictError`.

- [ ] **Step 1: Write failing tests** (walk the wizard steps with the established step-walk idiom; keep SME entry minimal — one SME per fieldset):

1. `test_finalize_updates_target_in_place` — seed wizard-born-style scenario (row_version 1, source `library_derived`, non-None library_pin, 1 SME row per fieldset), POST re-estimate, walk steps 2–6 keeping seeded values, finalize. Assert: redirect to `/scenarios/{s.id}`; SAME scenario id; `row_version == 2`; `source == expert_judgment`; `library_pin is None`; `vuln_framing == "inherent"`; distributions replaced (fit metadata `fitted_at` newer); status unchanged; NO new Scenario row created (count unchanged).
2. `test_finalize_replaces_sme_rows` — target starts with 2 old rows on "tef"; finalize a re-estimation entering 3 different rows → exactly the 3 new rows remain for the scenario (old gone), N=3 intact.
3. `test_finalize_conflict_renders_review_flash_and_preserves_draft` (asserts 422) — after seeding, bump the scenario via the edit form (row_version 2); finalize → response contains the conflict message, NOT a redirect; the `wizard_drafts` row still exists; the scenario is unchanged.
4. `test_finalize_register_import_upgrade` — scenario with `source=qualitative_register_import`, `vuln_framing="legacy_residual"`, a populated `conversion_metadata`, no SME rows: POST re-estimate (empty rehydration), enter estimates, finalize → source flips, `vuln_framing == "inherent"`, `conversion_metadata is None` (amendment 14), estimates replaced.
5. `test_create_path_unchanged` — the plain `POST /scenarios/new/wizard/...` flow (no target) still creates a new scenario (guard against regression: run one existing wizard-create integration test module and reference it here rather than duplicating).
6. `test_deprecated_control_link_survives_reestimate` (amendment 2) — scenario linked to a control that is then DEPRECATED; re-estimate keeping the ACTIVE selection; finalize → the DEPRECATED link still exists, the ACTIVE selection applied.
7. `test_cancel_targeted_draft_is_noop` (amendment 6) — POST the wizard cancel on a targeted draft → scenario row_version unchanged, draft row deleted.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** In `finalize_wizard`, replace the `create_from_wizard` try-block region with a dispatch. Shape (adapt to surrounding code verbatim-style):

```python
        is_reestimate = state.target_scenario_id is not None
        if is_reestimate:
            # #56: preserve status / effect / scenario_type / descriptive
            # version from the live row — the wizard doesn't collect them.
            target = await ScenarioRepo(db).get_for_org(
                organization_id=user.organization_id,
                scenario_id=uuid.UUID(state.target_scenario_id),
            )
            if target is None:
                # Deleted while the wizard was in flight: keep the draft so
                # the operator can see their entered data, surface a flash.
                return await _render_review_with_flash(
                    request, db, user, tx,
                    message="This scenario no longer exists — it was deleted "
                    "while you were estimating. Cancel to discard this draft.",
                )
            form = ScenarioForm(
                **form_kwargs,
                **state.basic_fields(),
                status=target.status,
                version=target.version,
                effect=getattr(target.effect, "value", target.effect),
                scenario_type=getattr(target.scenario_type, "value", target.scenario_type),
            )
        else:
            form = ScenarioForm(**form_kwargs, **state.basic_fields())
```

(The existing `form = ScenarioForm(...)` line becomes the else-branch; `library_pin` construction stays but is only used on the create path.) Then the create/update dispatch:

```python
        try:
            if is_reestimate:
                if state.target_expected_row_version is None:  # amendment 9
                    raise HTTPException(
                        500, "re-estimate draft missing its row-version capture"
                    )
                scenario = await ScenarioService(db).update_from_wizard(
                    organization_id=user.organization_id,
                    scenario_id=uuid.UUID(state.target_scenario_id),
                    form=form,
                    expected_row_version=state.target_expected_row_version,
                    actor=user,
                    ip_address=client_ip(request),
                    per_fieldset_pooling_summary=summary,
                )
            else:
                scenario = await ScenarioService(db).create_from_wizard(
                    ... existing kwargs unchanged ...
                )
        except (ScenarioVersionConflictError, NotFoundError) as exc:
            await db.rollback()  # unwind advance_step's token bump; draft survives
            return await _render_review_with_flash(
                request, db, user, tx, message=str(exc)
            )
        except ValidationError as exc:
            ... existing handler unchanged ...
```

Post-dispatch block adjustments:
- `scenario.entry_currency = "USD"` / `entry_rate = None` — the existing route-level stamp stays on the CREATE path only; the update path stamps inside `update_from_wizard` so the flip is audited (amendment 15).
- Mitigating controls: on the re-estimate path call `set_mitigating_controls` UNCONDITIONALLY (empty list must clear) AND pass `eligible_control_ids={c.id for c in await ControlRepo(db).list_for_org(user.organization_id)}` per amendment 2 — the #217 scoping that keeps DRAFT/DEPRECATED links alive (mirror routes/scenarios.py:~955-968). On the create path keep the existing `if state.mitigating_control_ids:` guard byte-identical (no eligible_control_ids).
- ATT&CK copy: keep guarded by `library_pin is not None` AND `not is_reestimate` (seeding never sets library fields, so this is defensive only — assert seeding leaves them None in Task 1's tests).
- SME rows: before `persist_estimates`, on the re-estimate path only:

```python
        if is_reestimate:
            await db.execute(
                delete(ScenarioSMEEstimate).where(
                    ScenarioSMEEstimate.scenario_id == scenario.id,
                    ScenarioSMEEstimate.organization_id == user.organization_id,
                )
            )
```

- Draft delete + commit + redirect: unchanged (redirect already uses `scenario.id`, which IS the target id on the update path).

- [ ] **Step 4: Run** — new module + `tests/integration/test_wizard*.py` + the wizard-create integration module: PASS.

- [ ] **Step 5: Commit** — `feat(wizard): finalize updates the target scenario in place (#56)`

---

### Task 5: UI copy (shell, review step, edit-form pointer)

**Files:**
- Modify: `src/idraa/templates/scenarios/wizard/_shell.html` (locate actual path via `grep -rn "Switch to expert mode" src/idraa/templates/`)
- Modify: the step-6 review template (`step_6_review.html`)
- Modify: `src/idraa/templates/scenarios/form.html` (mixture warning pointer)
- Test: extend `tests/integration/test_wizard_reestimate_routes.py`

- [ ] **Step 1: Failing tests** —
1. `test_shell_shows_reestimating_title` — mid-flow GET of step 2 with a targeted draft: body contains `Re-estimating:` and the scenario name; an untargeted draft does NOT contain it.
2. `test_review_step_states_update_semantics` — step-6 GET with targeted draft contains "replaces the estimates" AND "math may have been updated" (amendment 13); the create-path wording is unchanged for untargeted.
3. `test_edit_form_mixture_warning_points_to_reestimate` — extend the existing `test_edit_form_mixture_primary_loss_flattens_with_replacement_warning` in `tests/integration/test_scenario_routes.py`: body also contains "Re-estimate".

- [ ] **Step 2: Implement.** Shell header (inside whatever heading block exists — mirror the local markup):

```html
{% if state.target_scenario_id %}
  <p class="text-meta text-ink-2">Re-estimating: {{ state.name }}</p>
{% endif %}
```

Step-6 review, adjacent to the finalize button:

```html
{% if state.target_scenario_id %}
  <div class="alert alert-warning" role="alert"><span class="text-sm">
    Finalize <strong>replaces the estimates</strong> of scenario
    &ldquo;{{ state.name }}&rdquo; in place &mdash; its run history and
    status are unchanged. Provenance becomes expert judgment. Estimation
    math may have been updated since this scenario was last estimated
    &mdash; pooled distributions can shift even if you keep every value.
  </span></div>
{% endif %}
```

Edit-form pointer — append one sentence inside the existing `mixture_replace_warning` macro copy in `form.html`:

```
To re-elicit from experts instead, use <strong>Re-estimate</strong> on the scenario page.
```

(Templates receive `state` in wizard views — verify the context variable name via the existing shell template and adjust.)

- [ ] **Step 3: Run tests; fix; run the two touched integration modules fully.**
- [ ] **Step 4: Commit** — `feat(ui): re-estimation wizard copy + edit-form pointer (#56)`

---

### Task 6: Gate + docs

- [ ] **Step 1:** Full local gate FOREGROUND from the worktree: `uv run python scripts/run_local_gate.py` — green (ruff, format, mypy, fast pytest).
- [ ] **Step 2:** Spec drift log: append dated entries for every deviation the implementers disclosed (e.g. tags-column outcome, `_audit_diff` variant chosen, CSRF idiom found). Update `## Scope drift log` in the spec.
- [ ] **Step 3:** Commit — `docs(design): wizard re-estimation drift log (#56)`.

---

## Self-review notes (author)

- Spec coverage: entry+seeding (T1/T3), state targeting (T1), finalize branch + provenance + SME replace + conflict (T2/T4), UI copy (T5), error handling (T3 404 / T4 conflict+deleted), testing matrix distributed across tasks, gate (T6). Runs-not-stale and ATT&CK-untouched are absence properties asserted in T4 test 1.
- Known unknowns delegated with explicit instructions (tags column, CSRF idiom, `_audit_diff` variant, shell template path/context var) — each requires disclosure in the implementer report, and T6 logs them.
- Type consistency: `target_scenario_id` is hex-str in state, `uuid.UUID(...)` at the boundary (route), `scenario_id: uuid.UUID` in services — consistent with the module norm (state stores strings, boundaries parse).
