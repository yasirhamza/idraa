# Register Import UI (epic #34 P1c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user-facing slice that makes epic #34 usable end-to-end: upload a real register (.xlsx/.csv) → column-map → value-bind → preview → convert → report, plus the org-band admin CRUD UI and the converter-aware copy — everything P1a/P1b built, wired to a browser.

**Architecture:** Staged-token flow extending the #306 `CSVImportPreview` model with a `state_json` column (immutable staged bytes + accumulating step choices; re-parse per step; 10-min TTL). Step navigation is full-page 303 redirects threading the token (the app-wide wizard precedent — no HTMX step nav exists to mirror). New `register_import` service owns parsing/distinct-extraction/bind-resolution and hands `BoundRow` lists to P1b's `QualitativeConverterService.convert()`. Org-band CRUD mirrors `library_overrides` routes/templates exactly.

**Tech Stack:** FastAPI + Jinja2 + DaisyUI, openpyxl (promoted to runtime dep, read-only/data-only), existing converter/band services.

**Spec:** `docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md` §5 (+ §1 P1c bullet, §3 Meth-I1 copy half). **P1c briefs from the P1b gates (all BINDING here):** early `user.organization_id == organization_id` assert in `convert()`; bound `source_file` length (≤255, over → 422 at upload); `RowError` for caught `SQLAlchemyError` becomes a generic message with server-side `logger.exception`; wire per-band versions into `mapping_versions` (use `EffectiveBand.source_version` — replaces the lossy global max); converter-aware confirm/banner COPY + promote-refusal string for converted rows; org-band admin CRUD UI; binding profiles.

## Global Constraints

- All import routes `require_role(UserRole.ADMIN)`; global CSRF covers every POST. Org-band CRUD: ADMIN (mirror library_overrides).
- Reuse `MAX_UPLOAD_BYTES` (`routes/deps.py:15`) + `MAX_ROWS = 500` (`scenario_import_parsers.py:78`) — no new Settings keys.
- xlsx reads: `openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)`; BEFORE parsing, zip-member guard: reject if any `zipfile.ZipInfo.file_size > 50 * 1024 * 1024` or member count > 200 (zip-bomb).
- Value-bind pre-selection ONLY on exact case-insensitive label match (spec §5) — zero heuristics.
- Copy discipline: converted rows are priors pending review; the frequency-baseline framing per spec §3 Meth-I1 (both P1c briefs in spec §3 lines re: union-of-action-strings + vuln_framing overload apply to copy).
- Templates: desktop-first admin pages wrapped in `only_on_md()` like library_overrides; sidebar ADMIN section gains entries for both new pages.
- No fair_cam changes. No new engine math. Conversion still happens ONLY via `QualitativeConverterService.convert()`.

---

### Task 1: openpyxl runtime dep + register parser

**Files:**
- Modify: `pyproject.toml` (move `openpyxl>=3.1` from dev extra to `[project] dependencies`; keep the dev-extra line removed; update the L40-42 comment), run `uv lock` + `uv sync --extra dev`
- Create: `src/idraa/services/register_import_parsers.py`
- Test: `tests/unit/test_register_import_parsers.py`

**Interfaces (Produces):**
- `sniff_register_format(filename: str, content_type: str | None, data: bytes) -> str` — returns `"xlsx"` or `"csv"`; xlsx by extension/content-type OR the `PK\x03\x04` magic; conflict → `ValueError` (mirror `scenario_import_parsers.sniff_format` shape).
- `list_sheet_names(data: bytes) -> list[str]` — xlsx only; runs the zip guard first.
- `parse_register(data: bytes, fmt: str, sheet_name: str | None) -> ParsedRegister` where `@dataclass ParsedRegister: headers: list[str]; rows: list[dict[str, str]]  # header -> str(cell), 1-based source_row in key "_row"` — enforces MAX_ROWS (hard error dict like #306), skips fully-empty rows, coerces every cell via `str(v).strip()` with `None` → `""`; csv path mirrors `scenario_import_parsers` reader conventions (UTF-8-sig, delimiter sniff NOT needed — comma per #306 precedent).
- `_zip_guard(data: bytes) -> None` — raises `ValueError("workbook rejected: ...")` per Global Constraints bounds.

- [ ] **Step 1: failing tests** — build tiny xlsx fixtures IN the test via openpyxl write-mode (allowed in tests): happy 3-row sheet; two-sheet workbook (list_sheet_names order); formula cell (`=1+1`) asserting the parser sees the CACHED value or `""` never the formula string (write with `data_only` semantics: write value+formula, assert no leading `=` in output); >500 rows → hard error; zip-bomb guard (craft a zip with an oversize member via `zipfile` directly) → ValueError; csv happy path + BOM; sniff conflicts (xlsx magic named .csv → ValueError; .xlsx without magic → ValueError).
- [ ] **Step 2: verify fail. Step 3: implement. Step 4: verify pass + `uv run pytest tests/unit/test_register_import_parsers.py -q`. Step 5: commit** — `feat(import): register parsers with xlsx hardening (epic #34 P1c)`

### Task 2: staging state + binding profiles (models + migration)

**Files:**
- Modify: `src/idraa/models/csv_import_preview.py` (add `state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)` — accumulating step choices; docstring notes it is register-import-only today)
- Create: `src/idraa/models/register_binding_profile.py` — `RegisterBindingProfile(IdMixin, TimestampMixin, OrgMixin)`: `name: String(100) NOT NULL`, `column_map: JSON NOT NULL`, `value_bindings: JSON NOT NULL`, `mapping_versions_snapshot: JSON NOT NULL` (drift warning basis), `created_by FK users.id SET NULL nullable`; `UniqueConstraint(organization_id, name, name="uq_register_profile_org_name")`
- Modify: `src/idraa/models/__init__.py` (import + `__all__`)
- Create: `alembic/versions/<autogen>_register_import_ui.py` (autogenerate; verify add_column + create_table; downgrade drops both)
- Test: `tests/unit/test_register_binding_profile_model.py` + snapshot regen (`--snapshot-update` ONLY test_schema_snapshots.py; expected diff: CSVImportPreview + new model)

- [ ] **Steps (TDD as Task 1): model roundtrip tests, migration up/down test in tests/migrations/, snapshot regen with inspected diff. Commit** — `feat(models): register import staging state + binding profiles (epic #34 P1c)`

### Task 3: import-flow service + P1b hardening briefs

**Files:**
- Create: `src/idraa/services/register_import.py`
- Modify: `src/idraa/services/qualitative_converter.py` (three briefs)
- Modify: `src/idraa/services/qualitative_bands.py` (per-band mapping_versions)
- Test: `tests/unit/test_register_import_service.py`, extend `tests/unit/test_qualitative_converter.py` + `test_qualitative_bands_service.py`

**Interfaces (Produces):** `RegisterImportService(db)` with:
- `stage_upload(*, organization_id, filename, content_type, data, user) -> StagedRegister` (`token: uuid`, `fmt`, `sheet_names: list[str] | None`) — size caps (reuse #306 checks), `len(filename) > 255 → ValidationError` (brief: bound source_file), stores `CSVImportPreview(entity_type=f"register:{fmt}", csv_bytes=data, state_json={"filename": filename})`.
- `get_staged(*, organization_id, token) -> CSVImportPreview` — TTL check → `PreviewExpiredError`; org check (409 shape, no oracle).
- `set_sheet(*, organization_id, token, sheet_name)`; `set_column_map(*, organization_id, token, column_map: dict[str, str])` — validates targets ∈ {title, description, likelihood, impact, category, owner, carry_along, ignore}, `title`+`likelihood`+`impact` mapped exactly once each → else `ValidationError`; `set_value_bindings(*, organization_id, token, bindings: dict[str, dict[str, str]])` — three groups (`likelihood`→frequency labels, `impact`→magnitude labels, `category`→ThreatCategory values or `"__parked__"`); every distinct file value must be bound → else ValidationError. Each setter merges into `state_json` (re-read + assign + flush).
- `distinct_values(*, organization_id, token) -> dict[str, list[str]]` — parses staged bytes, distinct non-empty values for the three bound columns, sorted, each ≤ 50 distinct → else ValidationError ("column X has N distinct values — is the mapping right?").
- `preselect_bindings(distinct: dict, effective_bands, categories) -> dict` — exact case-insensitive match ONLY.
- `build_bound_rows(*, organization_id, token) -> list[BoundRow]` — assembles from staged bytes + state_json (raw = the 3 bound cells; carry_along = mapped carry_along columns; caps inherited from converter).
- `apply(*, organization_id, user, token, ip_address) -> ConversionReport` — re-parse + rebuild + `QualitativeConverterService.convert()`, then DELETE the staging row (single-use).
- Profile ops: `save_profile(*, organization_id, name, token, user)` (snapshot column_map+bindings+`mapping_versions()`), `apply_profile(*, organization_id, token, profile_id) -> list[str]` returns drift warnings (profile snapshot vs current `mapping_versions()` mismatch → warning strings; unbindable values left unbound), `list_profiles(organization_id)`.

Converter briefs (same task): (a) first line of `convert()`: `if user.organization_id != organization_id: raise IDORError(...)` + test; (b) `SQLAlchemyError` rows → `RowError(message="internal error converting this row — see server logs")` + `logger.exception` + test asserting no SQL text leaks; (c) `qualitative_bands.mapping_versions` now returns `{"canonical": {"kind:label": version, ...}, "org": {...}}` built FROM `effective_bands()` per-band `source`/`source_version` (fixes the lossy global max; update its tests + the converter metadata test pin).

- [ ] **Steps (TDD): full test set incl. profile drift-warning, single-use token deletion, unbound-value rejection, distinct>50 guard, org-scoping negative tests. Commit** — `feat(services): register import flow + converter hardening briefs (epic #34 P1c)`

### Task 4: routes + templates — upload, sheet, column-map

**Files:**
- Create: `src/idraa/routes/register_import.py` (router mounted in app like scenario_import's)
- Create: `templates/register_import/upload.html`, `column_map.html` (sheet picker folded into upload result when >1 sheet: intermediate `sheet.html`)
- Modify: `templates/layouts/_sidebar.html` (ADMIN section: `('/register-import', 'Register import')`)
- Test: `tests/integration/test_register_import_routes.py` (part 1)

Routes (all ADMIN, 303-redirect nav threading `?token=`):
- `GET /register-import` → upload form (accept `.xlsx,.csv`; profile dropdown from `list_profiles`).
- `POST /register-import` (multipart) → `stage_upload`; 1 sheet or csv → 303 to `/register-import/{token}/columns`; else 303 to `/register-import/{token}/sheet`.
- `GET+POST /register-import/{token}/sheet` → radio list of sheets → `set_sheet` → 303 columns.
- `GET /register-import/{token}/columns` → table of file headers, each with a target `<select>` (form_field macro, options = the 8 targets, default `ignore`; pre-fill from profile if applied); `POST` → `set_column_map` → 303 to `/register-import/{token}/bind`. 422 re-render with `build_flash` on ValidationError (mirror library_overrides pattern); expired token → 409 `import_expired.html` reuse.

- [ ] **Steps (TDD: route status/flow tests incl. RBAC 403 for analyst, CSRF implicit, expired 409, sheet flow). Commit** — `feat(routes): register import upload + column-map steps (epic #34 P1c)`

### Task 5: routes + templates — value-bind + profiles

**Files:**
- Modify: `src/idraa/routes/register_import.py`
- Create: `templates/register_import/bind.html`
- Test: extend `tests/integration/test_register_import_routes.py`

- `GET /register-import/{token}/bind` — three fieldsets (likelihood / impact / category), one row per distinct file value: value text + `<select>` of targets (frequency band labels from `effective_bands` for likelihood; magnitude labels for impact; ThreatCategory members + `"Parked — out of scope (non-information risk)"` for category), pre-selected per `preselect_bindings`; an info callout names the park semantics (spec D5 copy: counted + reported, never errors) and links `/qualitative-bands` for band management; "Save these bindings as a profile" optional name input.
- `POST /register-import/{token}/bind` → `set_value_bindings` (+ `save_profile` when name given; duplicate name → 422 flash) → 303 preview. Unbound value → 422 re-render with per-field errors.
- `POST /register-import/{token}/apply-profile` (from upload OR bind page) → `apply_profile` → 303 back with drift warnings flashed (warning level).

- [ ] **Steps (TDD: bind flow, pre-selection exactness — "High" pre-selects `high`, "Hi" does NOT; park binding; profile save/apply + drift warning; unbound 422). Commit** — `feat(routes): value-bind step + binding profiles (epic #34 P1c)`

### Task 6: preview, convert, report

**Files:**
- Modify: `src/idraa/routes/register_import.py`
- Create: `templates/register_import/preview.html`, `report.html`
- Test: extend integration file + `tests/integration/test_register_import_e2e_flow.py` (full journey, httpx-level)

- `GET /register-import/{token}/preview` — `build_bound_rows` + dry classification (would-create / parked / duplicate / error) rendered via the `preview_table` macro (badge action_key), counts, the `sl_note`, and the epistemic callout ("converted scenarios land as DRAFTS with starting priors — never results"; methodology-owned copy). Convert button disabled at 0 would-create (mirror #306).
- `POST /register-import/{token}/convert` → `apply()` → render `report.html` directly (200): created (links to each scenario), parked, skipped (reason), errors, mapping versions, "what next" box (review → confirm frequency baseline → promote). Token now deleted; re-POST → 409 expired page (single-use test).
- Full-journey test: upload xlsx → columns → bind (with one parked category + one unbindable-then-bound value) → preview counts → convert → report shows created N; then assert the created scenarios are DRAFT + legacy_residual + excluded from `/analyses/new`.

- [ ] **Steps (TDD). Commit** — `feat(routes): preview, convert, and report steps (epic #34 P1c)`

### Task 7: org-band admin CRUD UI

**Files:**
- Create: `src/idraa/routes/qualitative_bands.py`, `templates/qualitative_bands/{list,form}.html`
- Modify: `templates/layouts/_sidebar.html` (ADMIN: `('/qualitative-bands', 'Mapping bands')`)
- Test: `tests/integration/test_qualitative_bands_routes.py`

Mirror `library_overrides.py` exactly: list page shows the EFFECTIVE table (canonical rows marked "canonical", org rows marked "override/custom" with edit/delete), new/edit form (kind select, label, low/mode/high via form_field number/money by kind, reason textarea required, hidden expected_row_version on edit), 422 flash re-render, 409 optimistic-lock re-render, delete POST with confirm. Canonical rows are read-only (no edit/delete affordance). RBAC: ADMIN all routes; reviewer/analyst 403 test.

- [ ] **Steps (TDD incl. IDOR 404 cross-org, lock-conflict 409, delete-then-recreate flow). Commit** — `feat(routes): qualitative band admin CRUD (epic #34 P1c)`

### Task 8: converter-aware copy + e2e + gate

**Files:**
- Modify: `templates/scenarios/view.html` (converted-row banner variants), `src/idraa/services/scenarios.py` (promote-refusal string variant), spec drift-log
- Create: `tests/e2e/test_register_import_journey.py` (Playwright, marked e2e)
- Test: extend `tests/integration/test_vuln_framing_confirm.py` + `test_draft_workflow.py`

- View banner: when `scenario.source == QUALITATIVE_REGISTER_IMPORT` and `vuln_framing == 'legacy_residual'`, the F2 banner block renders converter-aware copy: heading "Frequency baseline needs review." body explaining the register likelihood was encoded as LEF (TEF=band, vulnerability neutral) and that confirming accepts the frequency baseline; button text "Confirm — accept frequency baseline"; WARNING sentence per spec §3 brief: attaching FAIR-CAM controls after confirming double-discounts an already-residual register frequency unless TEF is re-framed as inherent during review. Non-converted rows keep the existing copy verbatim (assert both variants).
- Promote-refusal string: in `promote()`, converted rows raise "confirm the frequency baseline before promoting — see the banner on this scenario" (non-converted keep the current string). Tests for both.
- Playwright e2e (desktop viewport): the Task 6 journey through a real browser, asserting the bind step's pre-selection and the report page links resolve.
- Spec drift-log: P1c copy shipped (closes the Meth-I1 deferral).
- **Full local gate FOREGROUND** (`uv run python scripts/run_local_gate.py`) + the chart-e2e-style rule: run `uv run pytest tests/e2e/test_register_import_journey.py -q` explicitly (fast gate deselects e2e).

- [ ] **Steps (TDD). Commit** — `feat(ui): converter-aware review copy + e2e journey (epic #34 P1c)`

---

## Final

Branch `feat/34-p1c-import-ui` off current main. 4-reviewer final PR-gate (epic milestone) before merge; methodology persona owns Task 8 copy + the preview/bind epistemic callouts. After merge: deploy decision goes to the owner (the epic is now end-to-end usable).

## Scope budget

- target_task_count: 8 (single PR)
- review budget: 4-reviewer plan-gate (iterate-to-zero) + per-task methodology+spec reviews + 4-reviewer final PR-gate
- timeline budget: 1-2 working sessions

## Scope drift log

- 2026-07-18: staging = `CSVImportPreview` + new `state_json` column (survey: wizard_drafts is for free-form field edits; import state is choices-over-immutable-bytes — #306 shape is the right base per spec §5).
- 2026-07-18: step nav = full-page 303 redirects (app-wide precedent; no HTMX step-nav precedent exists to mirror).
- 2026-07-18: openpyxl promoted dev-extra → runtime dependency (first runtime xlsx READ in the codebase; no zip-guard precedent existed — new hardening per spec §5).
- 2026-07-18: all nine P1b-gate deferral briefs folded in (Tasks 3 and 8); union-of-action-strings brief is N/A in P1c (no confirm-activity view exists; recorded for whichever slice builds one).
- 2026-07-18: inline org-band creation on the bind page reduced to a LINK to the band CRUD page (Task 7) — inline create-in-flow would duplicate the CRUD form inside a staged flow for marginal gain (YAGNI).
