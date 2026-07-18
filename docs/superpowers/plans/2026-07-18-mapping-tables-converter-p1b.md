# Mapping Tables + Conversion Service (epic #34 P1b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The backend of the qualitative register converter: layered qualitative mapping bands (canonical O-RA-cited seed + org override), and a conversion service that turns bound register rows into DRAFT scenarios with full provenance — no UI (P1c).

**Architecture:** Canonical bands mirror the `ScenarioLibraryEntry` seed pattern (org-less, seeded via validated JSON + alembic, immutable, pinning-tested). Org bands mirror `ScenarioLibraryOverride` (OrgMixin, reason NOT NULL, soft-delete, audit). The converter builds `ScenarioForm` payloads from the effective band table and persists through `ScenarioService.create()`, inheriting every P1a gate (create status domain, validation) for free. Reports are a returned dataclass; P1c renders them.

**Tech Stack:** SQLAlchemy 2 async + Alembic, Pydantic seed validation, pytest.

**Spec:** `docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md` §2 + §3 (P1b slice). All band values and derivations are pinned in spec §2.2 — copy them EXACTLY; they are methodology-gated.

## Global Constraints

- Canonical band values/derivations come from spec §2.2 verbatim — any numeric deviation is a BLOCKER.
- New alembic migrations use `uuid.uuid4().hex` (lint-enforced; never `str(uuid4())` or hyphenated literals).
- `ScenarioSource` member name is **`QUALITATIVE_REGISTER_IMPORT`** (the enum's own anticipatory comment at `models/enums.py:88` names it; supersedes the spec's `QUALITATIVE_CONVERTED` — drift-logged).
- Converted scenarios: `status=DRAFT`, `vuln_framing="legacy_residual"`, vulnerability `{"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 1.0}` (Task 3 verifies the engine on this; fallback `0.99/1.0/1.0` only if degenerate sampling misbehaves — record which was used).
- Seed JSON `distribution` key value is `"PERT"` (uppercase, matching `seed_library_entries.json`).
- All service mutations audit via `AuditWriter` with `"qualitative_band.create|update|delete"` / conversion via `"scenario.convert_qualitative"` batch action.
- Snapshot/contract updates: `pyproject.toml` field_sync allowlist + `pytest tests/contracts/test_schema_snapshots.py --snapshot-update` (diff must be reviewed, not blind-committed).
- No routes, no templates, no UI (P1c). No revenue scaling (deprecated #517).

---

### Task 1: Models + migration + enum member

**Files:**
- Create: `src/idraa/models/qualitative_mapping.py`
- Modify: `src/idraa/models/enums.py:88` (replace the placeholder comment with the member)
- Modify: `src/idraa/models/scenario.py` (add `conversion_metadata` JSON nullable, after `library_pin`)
- Modify: `src/idraa/models/__init__.py` (export new models)
- Modify: `pyproject.toml` field_sync scenario allowlist (add `conversion_metadata`)
- Create: `alembic/versions/<autogen-id>_qualitative_mapping_bands.py`
- Create: `data/seed_qualitative_bands.json`
- Create: `src/idraa/services/seed_qualitative_bands_loader.py` (Pydantic `BandSeed` + loader)
- Test: `tests/unit/test_qualitative_mapping_models.py`, snapshot regen

**Interfaces (Produces):**
- `QualitativeMappingBand` (canonical, org-less, mirrors ScenarioLibraryEntry style): `id: UUID pk`, `kind: str` (`"frequency"|"magnitude"`, app-enforced — no DB CHECK per project convention), `label: str`, `low: float`, `mode: float`, `high: float`, `sort_order: int`, `derivation: Text NOT NULL`, `version: int default 1`. `UniqueConstraint(kind, label, version, name="uq_qual_band_kind_label_version")`.
- `QualitativeMappingOrgBand` (`IdMixin, TimestampMixin, OrgMixin`): same value columns (`kind, label, low, mode, high`), `reason: Text NOT NULL`, `version: int default 1`, `row_version: int default 1`, `created_by: UUID | None FK users.id ON DELETE SET NULL`, `deleted_at: DateTime | None`. `UniqueConstraint(organization_id, kind, label, name="uq_qual_org_band_org_kind_label")`.
- `ScenarioSource.QUALITATIVE_REGISTER_IMPORT = "qualitative_register_import"`.
- `Scenario.conversion_metadata: dict | None` (JSON, nullable).
- `data/seed_qualitative_bands.json`: 10 objects `{kind, label, low, mode, high, sort_order, derivation}` — values copied EXACTLY from spec §2.2's two tables; `derivation` strings must state: geometric-midpoint rule; for magnitude — O-RA Table 1 §6.6 p.33 edge citation + label correspondence + $1K/$1B closure rationale (p99.9-of-catastrophic-tails wording per spec); for frequency — "v3 log-decade convention by analogy with O-RA Table 1; O-RA publishes no frequency scale" + 250/yr cap rationale.

- [ ] **Step 1: failing model test** — `tests/unit/test_qualitative_mapping_models.py`:

```python
"""Model-shape tests for the qualitative mapping band layer (epic #34 P1b)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from idraa.models.enums import ScenarioSource
from idraa.models.qualitative_mapping import QualitativeMappingBand, QualitativeMappingOrgBand


def test_scenario_source_has_qualitative_member():
    assert ScenarioSource.QUALITATIVE_REGISTER_IMPORT.value == "qualitative_register_import"


@pytest.mark.asyncio
async def test_band_tables_roundtrip(db_session):
    band = QualitativeMappingBand(kind="frequency", label="moderate", low=1.0, mode=3.0,
                                  high=10.0, sort_order=2, derivation="test", version=1)
    db_session.add(band)
    await db_session.flush()
    got = (await db_session.execute(select(QualitativeMappingBand))).scalars().one()
    assert (got.kind, got.label, got.mode) == ("frequency", "moderate", 3.0)
```

(plus an OrgBand roundtrip test asserting `organization_id`/`reason`/`deleted_at` columns exist — same shape, seeded org via existing factories.)

- [ ] **Step 2: run, verify ImportError.**
- [ ] **Step 3: implement models** (mirror `scenario_library.py` column style exactly — `Mapped[...]`/`mapped_column`, `native_enum=False` not needed since `kind` is `str`); enum member replacing the placeholder comment; `conversion_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)` on Scenario; exports.
- [ ] **Step 4: migration** — `uv run alembic revision --autogenerate -m "qualitative mapping bands"` then EDIT: verify both `create_table`s + `add_column` for scenarios; append the seed phase: load `data/seed_qualitative_bands.json` via `seed_qualitative_bands_loader.load_validated_bands()` (Pydantic `BandSeed`: `kind: Literal["frequency","magnitude"]`, `label: str` pattern `^[a-z_]+$`, `low/mode/high: float` with `@model_validator` asserting `0 <= low <= mode <= high` and `low < high`, `sort_order: int`, `derivation: str min_length=40`, `extra="forbid"`), inserting per-row via `sa.text` with `"id": uuid.uuid4().hex`. Downgrade: drop both tables + `drop_column`. Path anchor: `Path(idraa.__file__).resolve().parent.parent.parent / "data" / ...` (the loader owns this, migration imports the loader).
- [ ] **Step 5: write the 10-row seed JSON from spec §2.2** (both tables verbatim; floats: use `0.01, 0.03, 0.1` etc., magnitude as integers-as-numbers `1000 … 1000000000`).
- [ ] **Step 6: field_sync allowlist** — add `conversion_metadata` to `[tool.idraa.contracts.field_sync.scenario] allowlist` (it is ORM-only; the form never carries it — the converter writes it directly).
- [ ] **Step 7: migrate a scratch DB + run tests + snapshot regen** — `uv run alembic upgrade head` (against the dev DB per project convention), `uv run pytest tests/unit/test_qualitative_mapping_models.py tests/contracts/ -q`; run `--snapshot-update` ONLY for `test_schema_snapshots.py`, inspect the diff (expected: Scenario gains conversion_metadata; two new ORM snapshots), include it.
- [ ] **Step 8: Commit** — `feat(models): qualitative mapping bands + conversion metadata (epic #34 P1b)`

### Task 2: Canonical pinning tests

**Files:** Create: `tests/unit/test_qualitative_band_pins.py`

Mirrors `test_seed_recuration.py` style: read `data/seed_qualitative_bands.json` directly and pin EVERY value:

- [ ] **Step 1: write pins** —

```python
"""Pin canonical qualitative band values to spec §2.2 (methodology-gated).

Any change here is a calibration change: it requires a spec §2.2 edit and a
methodology re-review, never a casual re-pin.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import idraa

SEED = Path(idraa.__file__).resolve().parent.parent.parent / "data" / "seed_qualitative_bands.json"

EXPECTED_FREQUENCY = {  # label: (low, mode, high)
    "very_low": (0.01, 0.03, 0.1), "low": (0.1, 0.3, 1), "moderate": (1, 3, 10),
    "high": (10, 32, 100), "very_high": (100, 158, 250),
}
EXPECTED_MAGNITUDE = {
    "very_low": (1_000, 3_200, 10_000), "low": (10_000, 32_000, 100_000),
    "moderate": (100_000, 320_000, 1_000_000), "high": (1_000_000, 3_200_000, 10_000_000),
    "very_high": (10_000_000, 100_000_000, 1_000_000_000),
}


def _bands():
    return json.loads(SEED.read_text(encoding="utf-8"))


def test_exactly_ten_bands_five_per_kind():
    bands = _bands()
    assert len(bands) == 10
    assert sum(1 for b in bands if b["kind"] == "frequency") == 5
    assert sum(1 for b in bands if b["kind"] == "magnitude") == 5


def test_frequency_values_pinned():
    got = {b["label"]: (b["low"], b["mode"], b["high"])
           for b in _bands() if b["kind"] == "frequency"}
    assert got == EXPECTED_FREQUENCY


def test_magnitude_values_pinned():
    got = {b["label"]: (b["low"], b["mode"], b["high"])
           for b in _bands() if b["kind"] == "magnitude"}
    assert got == EXPECTED_MAGNITUDE


def test_modes_are_2sf_geometric_midpoints():
    for b in _bands():
        gm = math.sqrt(b["low"] * b["high"])
        # spec §2.3: mode = geometric midpoint rounded to 2 significant figures
        assert abs(b["mode"] - gm) / gm < 0.35, (b["label"], b["kind"], gm)


def test_derivations_carry_provenance():
    for b in _bands():
        d = b["derivation"]
        if b["kind"] == "magnitude":
            assert "O-RA" in d and "Table 1" in d, b["label"]
        else:
            assert "convention" in d and "O-RA" in d, b["label"]  # names the absence
```

(Note: the 2sf tolerance is loose on purpose for `very_high` magnitude where mode=1e8 vs gm≈1e8 exactly; the exact-value pins above are the real guard — this test documents the RULE.)

- [ ] **Step 2-3: run (pass immediately if Task 1 seed is correct — a failure here means the seed JSON deviated), commit** — `test(unit): pin canonical qualitative band values (epic #34 P1b)`

### Task 3: Engine degenerate-PERT verification (spec §3 plan-time check)

**Files:** Create: `tests/unit/test_degenerate_vuln_pert.py`

- [ ] **Step 1: write the check** — run the actual fair_cam engine path used by runs with `vulnerability={"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 1.0}` and a normal TEF/PL, assert: no exception, all vulnerability-derived samples finite, LEF ≈ TEF (mean within 2%). Find the engine entry the run executor uses (`grep -rn "FAIREngine\|run_simulation" src/idraa/services/run_executor.py fair_cam/ | head`) and drive the SMALLEST public API that exercises PERT sampling of vulnerability (≥2000 iterations, fixed seed if the API takes one).
- [ ] **Step 2: run.** If it fails on the degenerate triple: change the converter constant (Task 4) to `{"low": 0.99, "mode": 1.0, "high": 1.0}`, adjust this test to pin THAT encoding (LEF within ~1% of TEF), and record the outcome in the task-report + a one-line spec §3 note (the spec pre-authorizes exactly this fallback).
- [ ] **Step 3: commit** — `test(unit): verify engine handles neutral vulnerability PERT (epic #34 P1b)`

### Task 4: Band repo + effective-table + org-band service

**Files:**
- Create: `src/idraa/repositories/qualitative_mapping_repo.py`
- Create: `src/idraa/services/qualitative_bands.py`
- Test: `tests/unit/test_qualitative_bands_service.py`

**Interfaces (Produces):**
- `QualitativeMappingRepo(session)`: `list_canonical() -> list[QualitativeMappingBand]`; `list_org_bands(organization_id) -> list[QualitativeMappingOrgBand]` (filters `deleted_at IS NULL`); `get_org_band(organization_id, band_id)`.
- `@dataclass(frozen=True) EffectiveBand: kind: str; label: str; low: float; mode: float; high: float; source: str  # "canonical" | "org"; source_version: int`
- `QualitativeBandService(session)`:
  - `effective_bands(organization_id) -> dict[tuple[str, str], EffectiveBand]` — canonical ⊕ org, org wins per (kind, label); excludes soft-deleted org rows.
  - `mapping_versions(organization_id) -> dict` — `{"canonical": <max canonical version>, "org": {label_key: version, ...}}` for conversion_metadata pinning.
  - `create_org_band(*, organization_id, kind, label, low, mode, high, reason, user, ip_address=None)` — validates `kind ∈ {frequency, magnitude}`, label `^[a-z_]{1,40}$`, `0 <= low <= mode <= high`, `low < high`, magnitude/frequency positivity; rejects duplicate active (org, kind, label); audit `"qualitative_band.create"`.
  - `update_org_band(*, organization_id, band_id, low, mode, high, reason, expected_row_version, user, ip_address=None)` — org-ownership (IDOR), optimistic lock, bumps `version`+`row_version`, audit `"qualitative_band.update"`.
  - `delete_org_band(*, organization_id, band_id, user, ip_address=None)` — soft-delete, audit `"qualitative_band.delete"`.

- [ ] **Steps 1-5 (TDD):** tests first — effective-table merge (org overrides one label, adds a novel label, soft-deleted org row ignored), create/update/delete each writing rows + audit rows (assert action strings), validation rejections (bad kind, label pattern, ordering violation, duplicate), IDOR (org B cannot touch org A's band), optimistic-lock conflict. Mirror `test_library_override_crud_service.py` fixtures/style. Implement mirroring `ScenarioLibraryService` (typed domain errors reuse `idraa.errors`: `ValidationError`, `NotFoundError`, `IDORError`, and the existing version-conflict error class if generic — else define `QualitativeBandVersionConflictError` beside the library one). Commit — `feat(services): qualitative band layer with org overrides (epic #34 P1b)`

### Task 5: Converter service + report

**Files:**
- Create: `src/idraa/services/qualitative_converter.py`
- Test: `tests/unit/test_qualitative_converter.py`, `tests/integration/test_qualitative_converter_integration.py`

**Interfaces (Produces):**
- `@dataclass(frozen=True) BoundRow`: `source_row: int; title: str; description: str | None; owner: str | None; likelihood_label: str; magnitude_label: str; category: ThreatCategory | None  # None == PARKED; raw: dict[str, str]  # original cell values; carry_along: dict[str, str]  # leftover columns user chose to keep`
- `@dataclass ConversionReport`: `created: list[ConvertedRow]` (`scenario_id, source_row, title`), `parked: list[int]`, `skipped_duplicates: list[SkippedRow]` (`source_row, title, reason ∈ {"name", "same_source"}`), `errors: list[RowError]` (`source_row, message`), `sl_note: str` (the fixed SL-not-derivable sentence from spec §3), `mapping_versions: dict`, `source_file: str`.
- `QualitativeConverterService(session)`: `async convert(*, organization_id, user, source_file: str, rows: list[BoundRow], ip_address=None) -> ConversionReport`.

Semantics (spec §3, all mandatory):
- Per row: TEF = frequency band PERT (`{"distribution": "PERT", low, mode, high}` from effective table by `likelihood_label`); PL = magnitude band PERT; vulnerability = the Task 3-verified neutral encoding; `secondary_loss=None`; unknown label → RowError (binding happened upstream; an unknown label here is a bug surface, not user error).
- `category is None` → parked (count, never error). `category` set → `threat_category`.
- `description` = original description + `"\n\n--- Register provenance ---\n"` block: owner, raw likelihood/impact/category values, carry_along pairs, `source_file` + `source_row`. Plain text, no markup.
- Build a `ScenarioForm` (`name=title`, `scenario_type` — read the enum and use the generic/default member the expert form defaults to; `source=ScenarioSource.QUALITATIVE_REGISTER_IMPORT`, `status=EntityStatus.DRAFT`) and persist via `ScenarioService.create()` — NEVER writes the ORM directly (inherits P1a create gates + validation). After create, set `scenario.vuln_framing = "legacy_residual"` and `scenario.conversion_metadata` (the service owns these two ORM-only fields; form carries neither), single flush.
- `conversion_metadata` = `{"source_file", "source_row", "raw": {...}, "bindings": {"likelihood_label", "magnitude_label", "category"}, "mapping_versions": <Task 4>, "converted_at": <UTC ISO>}` validated by a `ConversionMetadata` Pydantic model (`extra="forbid"`) before assignment.
- Dedup BEFORE create: (a) name match vs ALL statuses in org (spec §3.1 — NOT the ACTIVE-only `_existing_active_names`); (b) same-source: any org scenario whose `conversion_metadata.source_file` stem + `source_row` match. Both → skipped with reason.
- One batch audit row after the loop: action `"scenario.convert_qualitative"`, `changes={"created": [ids], "parked": n, "skipped": n, "errors": n, "source_file": ...}`.
- Row isolation: a RowError (e.g. validation failure) must not abort the batch — try/except per row, continue; the report carries it.

- [ ] **Steps 1-6 (TDD):** unit tests: 3-row happy path creates 3 DRAFTs (adapter-iteration contract: N≥3 in → N preserved across create/park/skip buckets), park handling, both dedup reasons, unknown-label RowError isolation (row 2 fails, rows 1+3 still created), provenance block content, conversion_metadata pinned shape + versions, audit batch row. Integration test: converted scenario is EXCLUDED from run creation (POST /analyses → 422 — proving P1a composition end-to-end) and carries both banners' preconditions (`status=DRAFT`, `vuln_framing="legacy_residual"`). Implement. Commit — `feat(services): qualitative register converter core (epic #34 P1b)`

### Task 6: Full gate + docs

- [ ] **Step 1:** spec drift-log entry: `QUALITATIVE_CONVERTED` → `QUALITATIVE_REGISTER_IMPORT` (in-code anticipated name); Task 3 outcome (which vuln encoding shipped) noted in spec §3 if the fallback fired.
- [ ] **Step 2:** `uv run python scripts/run_local_gate.py` FOREGROUND — all steps green (fix only what this branch broke).
- [ ] **Step 3:** Commit — `docs(design): P1b drift-log + encoding outcome (epic #34)`

---

## Final

Branch `feat/34-p1b-mapping-converter` off current main. PR after 4-reviewer final PR-gate converges 0/0 (epic milestone; methodology persona REQUIRED — this slice ships the band tables + PERT derivations + all conversion copy). Note for the PR body: adapter-surface rule applies (new ORM↔DTO bridge = the converter's ScenarioForm construction + conversion_metadata model) — that is exactly why this plan goes through the full plan-gate before execution.

## Scope budget

- target_task_count: 6 (single PR)
- review budget: 4-reviewer plan-gate (iterate-to-zero) + per-task methodology+spec reviews + 4-reviewer final PR-gate.
- timeline budget: 1 working session.

## Scope drift log

- 2026-07-18: `ScenarioSource` member renamed from spec's `QUALITATIVE_CONVERTED` to the enum's own anticipated `QUALITATIVE_REGISTER_IMPORT` (models/enums.py:88 placeholder comment predates the spec).
- 2026-07-18: org-band admin ROUTES/UI deferred to P1c (spec §2.1 "admin CRUD" satisfied at service layer in P1b; the P1c binding UI is the consumer).
- 2026-07-18: canonical table is org-less (mirrors ScenarioLibraryEntry); spec §2.1's "organization_id on every table" applies to the org layer only — the canonical layer is global seeded data like the scenario library.
