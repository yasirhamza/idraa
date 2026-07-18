# Qualitative Register Converter — Design (epic #34)

**Status:** approved-pending-owner-review · **Date:** 2026-07-18 · **Epic:** #34 (ex-riskflow#549) · **ERM seam:** #39

Convert the artifact every prospective user already has — a qualitative risk register
(title, likelihood, impact, category, owner; the heat-map spreadsheet) — into **draft
FAIR scenarios**. Adoption on-ramp. Prior art: tidyrisk `evaluator` (Severski), whose
core model we adopt: *qualitative labels are join keys into an org-editable
quantitative mapping table*.

## Decision record (owner, 2026-07-18)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Ingestion | **Mapping UI + xlsx**: upload real register (.xlsx/.csv, any headers) → column-map → value-bind → preview → convert |
| D2 | Scale flexibility | **evaluator model + layered doctrine**: labels join against a layered mapping table (canonical + org); no fixed scale, no auto-detection heuristics |
| D3 | Likelihood encoding | **TEF = band, vulnerability = neutral (1,1,1), `vuln_framing="legacy_residual"`** — LEF ≡ register likelihood; F2 banner drives inherent re-framing at review |
| D4 | Converted rows | **Real `EntityStatus.DRAFT`** on Scenario (first use): excluded from runs/aggregates/dashboard/reports until audit-logged promotion |
| D5 | ERM sequencing | **Park seam now, ERM epic post-MVP (#39)**: non-information-risk categories bind to an explicit "parked" target; mapping schema stays domain-neutral |

Corrected issue premises (survey 2026-07-18): #306 import has **no column mapper**
(fixed headers only) — the mapper is new UI; revenue-tier loss scaling was **removed**
2026-07-07 (#517: "the envelope IS the calibration") — bands are not revenue-scaled;
no Scenario row uses DRAFT today — D4 is a new workflow, not reuse.

## 1. Scope

- **P1a — DRAFT workflow** (independent slice, lands first): DRAFT on Scenario,
  central exclusion, promote flow, banner.
- **P1b — mapping tables + conversion service**: canonical band table, org override
  layer, converter core, conversion report. Methodology-reviewer territory.
- **P1c — import UI**: upload / column-map / value-bind / preview / confirm, xlsx
  parser, binding profiles.
- **P2 — library-matching assist** (own design pass later): suggest one of the 102
  curated entries per register row from taxonomy/tag/category signals.
  Structured/exact matching first — fuzzy string matching would be a codebase first
  (controls-importer convention is "no fuzzy matching, no synonym table") and needs
  its own justification.
- **Out of scope:** auto-finalizing conversions; bidirectional register sync;
  GRC-tool APIs; ERM risk classes (#39); revenue scaling of bands (deprecated);
  persisting parked rows (re-import + saved binding profile covers the need).

## 2. Mapping-table model

### 2.1 Structure (domain-neutral by construction)

Two band kinds — `frequency` (events/year) and `magnitude` (USD) — **not** named
TEF/PL in the schema, so #39 can widen binding targets without schema rework.

New tables (UUIDs, `organization_id`, audit-logged like `ScenarioLibraryOverride`):

- `qualitative_mapping_band` — canonical layer, **seeded from code, immutable via
  UI**: `id, kind ∈ {frequency, magnitude}, label, low, mode, high, sort_order,
  derivation (text), version`. Pinning tests assert exact values.
- `qualitative_mapping_org_band` — org layer: same value columns + `reason` (NOT
  NULL), `version`, soft-delete, admin CRUD. An org row either **overrides** a
  canonical label or **adds** a new label. Effective table = canonical ⊕ org
  (org wins per label), same merge discipline as library overrides.

### 2.2 Canonical values — epistemic status: banding convention, not calibration

**Explicitly labeled "v3 derivation — log-decade banding convention; priors for
calibrated review, not an empirical claim."** No source is cited for the numbers
because none is needed for a stated convention; the feature's entire framing is
that these are starting envelopes an analyst must review (D3/D4 enforce that
structurally). This is deliberately NOT presented as O-RA-derived until primary
verification (below).

Frequency bands (events/year), log-decade edges, PERT mode = geometric midpoint
√(low·high) of the band:

| label | low | mode | high |
|---|---|---|---|
| very_low | 0.01 | 0.03 | 0.1 |
| low | 0.1 | 0.3 | 1 |
| moderate | 1 | 3 | 10 |
| high | 10 | 32 | 100 |
| very_high | 100 | 158 | 250 |

Top band is open-ended in concept; capped at 250/yr (≈ business-daily) — documented
in `derivation`. Modes rounded to 2 significant figures from the geometric midpoint
(√(100·250) ≈ 158).

Magnitude bands (USD), same rule:

| label | low | mode | high |
|---|---|---|---|
| very_low | 1,000 | 3,200 | 10,000 |
| low | 10,000 | 32,000 | 100,000 |
| moderate | 100,000 | 320,000 | 1,000,000 |
| high | 1,000,000 | 3,200,000 | 10,000,000 |
| very_high | 10,000,000 | 100,000,000 | 1,000,000,000 |

Top band mode = √(10M·1B) ≈ $100M; cap $1B aligns with the library's catastrophic
envelope ceiling. Orgs whose loss capacity differs express it through the org layer
(that IS the evaluator workshop-calibration step), not a revenue multiplier.

**O-RA upgrade path (owner action, tracked in the epic):** O-RA 2.0.1 (The Open
Group, C20A) publishes example measurement scales but sits behind a licensed login
(verified 2026-07-18: pubs.opengroup.org → SSO). When the owner pulls the PDF
(free individual login), pin section/table numbers; if O-RA's example boundaries
differ, either adopt them (canonical version bump, migration) or document divergence
in `derivation`. Until then the convention framing stands on its own. Cite facts and
boundaries only — never reproduce O-RA prose (licensed-material rule).

### 2.3 Band → PERT derivation rule (methodology-gated)

`{distribution: "pert", low: band.low, mode: band.mode, high: band.high}` for both
TEF and PL. Rationale: bands are order-of-magnitude claims; the geometric midpoint
is the log-symmetric central value, consistent with the multiplicative character of
both frequency and loss. This mirrors evaluator's BetaPERT-per-label model
(`qualitative_mappings.csv`: `type,label,l,ml,h,conf`; MIT-licensed, values
independently chosen by each org there too).

## 3. Conversion semantics

Per register row, after binding:

- `threat_event_frequency` = frequency band PERT (D3).
- `vulnerability` = `{distribution: "pert", low: 1.0, mode: 1.0, high: 1.0}` —
  validator-legal (non-strict ordering, [0,1] bounds). **Plan-time check:** fair_cam
  engine sampling of a zero-variance PERT; fallback `{0.99, 1.0, 1.0}` if degenerate
  sampling misbehaves. LEF ≡ register likelihood either way (≤1% shift under
  fallback).
- `vuln_framing = "legacy_residual"` — the shipped F2 banner + confirm/re-frame flow
  is the calibration-review driver. Register likelihood is (almost always) residual;
  this framing says so instead of double-counting controls.
- `primary_loss` = magnitude band PERT. `secondary_loss = NULL` (engine-safe
  post-#525). Conversion report flags every row: "SL not derivable from a single
  impact score — add during review or anchor to a library entry (P2)."
- `threat_category` = bound enum member (NOT NULL on Scenario). Category values may
  also bind to **PARKED** (D5): row is skipped, counted, and listed in the report
  ("N rows parked — Idraa models information & OT risk today; see #39"), never an
  error.
- `name` = register title (dedup per §3.1); `description` = register description +
  a "Register provenance" block (owner, raw likelihood/impact/category values, any
  unmapped columns the user chose to carry, source file + row number).
- `source` = new `ScenarioSource.QUALITATIVE_CONVERTED`.
- `conversion_metadata` (new nullable JSON on Scenario, validated by a Pydantic
  model): `{source_file, source_row, raw: {likelihood, impact, category}, bindings:
  {likelihood_label, magnitude_label, category}, mapping_versions: {canonical, org},
  binding_profile_id?, converted_at}` — reproducible + auditable, same pinning
  discipline as library clones.
- `status = EntityStatus.DRAFT` (D4). All other fields take model defaults.

### 3.1 Re-import / dedup

Same-name dedup as #306 (skip + report) **plus** same-source detection: an incoming
row whose `(source_file_stem, source_row)` — or same title — matches an existing
scenario's `conversion_metadata` is reported as "already converted" and skipped.
Quarterly re-uploads with a saved binding profile converge to "only the new rows".

## 4. DRAFT workflow (P1a)

- **Exclusion is implemented once, centrally**: the scenario listing/selection query
  layer gains an explicit `include_drafts` (default False); consumers that must
  exclude drafts — single-run creation selector, AGGREGATE selection, dashboard
  posture/coverage, PDF/portfolio reports, run executor guard (defense in depth:
  refuse to execute a DRAFT scenario) — all go through it. Scenario list UI shows
  drafts with a DRAFT badge + filter chip (visibility is the point of review).
- **Contract test enumerates consumers**: a test walks every call site of the query
  layer (import-graph assertion) and asserts the run-creation, aggregate, dashboard,
  and report paths reject/omit DRAFT rows — the guardrail must be provably total,
  or it's theater.
- **Promote** (analyst/admin): DRAFT → ACTIVE, audit-logged (`AuditWriter`),
  row-version bump; idempotent like `confirm_vuln_framing`. Edit-in-DRAFT allowed
  (the scenario edit form IS the review surface). No demote — deprecation exists.
- **Banner**: converted DRAFT scenarios show a conversion-provenance banner (band
  bindings + "priors for review" epistemic caveat) alongside the legacy_residual
  banner; copy is methodology-reviewed. Promotion requires the legacy_residual
  confirm to have happened OR explicit acknowledgment in the promote dialog.

## 5. Import pipeline (P1c)

- **Formats**: `.csv` (existing reader conventions) + `.xlsx` via openpyxl
  (`read_only=True`, `data_only=True` — cached values only, formulas never
  evaluated), first worksheet default + sheet picker, zip-member size cap before
  parse (zip-bomb guard), 5 MB / 500-row / `Content-Length` caps as #306.
- **Flow** (admin-only, CSRF, staged like #306 with token + 10-min TTL,
  re-parse-on-confirm): upload → column-map (register columns → title, description,
  likelihood, impact, category, owner, carry-along extras; title+likelihood+impact
  required) → value-bind (distinct values per bound column → band labels /
  ThreatCategory / PARKED; pre-selected only on **exact case-insensitive label
  match** — no heuristics) → preview (per-row derived params, skips, errors) →
  convert → result page = conversion report (also persisted as an audit event).
- **Binding profiles**: named, per-org saved `{column_map, value_bindings}`;
  selecting a profile pre-fills both steps; profile stores mapping-table versions
  it was authored against and warns on drift.
- Both parsers emit identical `(source_line, field_dict)` pairs into one shared
  validator (single-validator seam proven by #306).

## 6. Testing

- Pinning tests: canonical band values + derivation rule (exact).
- Contract tests: ORM↔DTO field sync for new models; adapter iteration test (N≥3
  register rows → N scenarios/skips preserved); DRAFT exclusion consumer walk (§4).
- Unit: binding resolution (org-over-canonical, PARKED, unmatched), PERT
  construction, dedup, conversion_metadata pinning.
- Integration: full upload→convert happy path (xlsx + csv), re-import convergence,
  promote flow + audit rows, legacy_residual banner presence, run-executor DRAFT
  refusal.
- E2E (Playwright): one register upload → column-map → bind → preview → convert →
  promote journey.
- Security-shaped: oversized xlsx, zip bomb, formula-bearing xlsx (values-only
  read asserted), CSRF on all POSTs, role gates.

## 7. Risks / open items

1. **Ordinal→ratio laundering** — the classic risk-matrix sin (Hubbard/Seiersen).
   Mitigated structurally (DRAFT + dual banners + report + never-auto-final); copy
   must present converted numbers as priors, never results. Methodology reviewer
   owns wording.
2. **O-RA verification** — §2.2 upgrade path; owner pulls C20A; values may shift
   under a canonical version bump. Does not block P1 (convention framing).
3. **Degenerate PERT** — §3 plan-time engine check with pinned fallback.
4. **DRAFT leak** — §4 contract test must enumerate consumers via import graph,
   not a hand-list.
5. **Mixed ERM registers** — handled by PARKED (D5); the failure mode "most rows
   parked" is a report outcome, not an error; #39 is the widening path.
6. **xlsx attack surface** — §5 hardening; openpyxl read-only/data-only.

## Scope budget

- target_task_count: 20 (P1a ≈ 4, P1b ≈ 8, P1c ≈ 8; each P-slice is one PR)
- review budget: one 4-reviewer plan-gate over spec+plan (iterate-to-zero), one
  4-reviewer final PR-gate per P-slice (3 total); methodology persona on §2/§3
  and conversion copy in every round.
- timeline budget: 3 working sessions (one per P-slice), sequential; P2 is a
  separate future spec+plan and is NOT in this budget.

## Scope drift log

- 2026-07-18 (spec vs originating issue #34): **corrected stale premises** — #306
  has no column mapper (mapper is new UI, D1); #517 revenue scaling was removed,
  not reusable (bands unscaled, org layer instead); Scenario DRAFT state didn't
  exist (D4 adds it as new workflow).
- 2026-07-18: **added** ERM park seam (D5) after owner raised the enterprise-risk
  epic; generalized ERM itself **cut** to new epic #39 (post-MVP).
- 2026-07-18: **cut** O-RA-cited canonical values to an upgrade path (login-walled
  primary source); interim values reframed as an explicitly-labeled log-decade
  convention.
- 2026-07-18: **cut** persisting parked rows (YAGNI — re-import + binding profile
  covers it).

## 8. Ceremony

Epic milestone: 4-reviewer plan-gate on this spec + plan (methodology /
spec-compliance / architect / security-auditor, Opus-min, iterate-to-zero), same
at each P-slice final PR-gate. Methodology persona (Opus, max effort) mandatory on
§2/§3 and all conversion copy. Adapter surfaces (new ORM↔DTO mappers,
conversion_metadata) fall under the data-contract paranoid-gate rule.
