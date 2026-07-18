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
- All service mutations audit via `AuditWriter` with `"qualitative_band.create|update|delete"` / conversion via `"scenario.convert_qualitative"` batch action / converter-aware confirm via `"scenario.confirm_frequency_baseline"` (Task 5b). Every `.log()` call passes `organization_id=organization_id`.
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
- `QualitativeMappingOrgBand` (`IdMixin, TimestampMixin, OrgMixin`): same value columns (`kind, label, low, mode, high`), `reason: Text NOT NULL`, `version: int default 1`, `row_version: int default 1`, `created_by: UUID | None FK users.id ON DELETE SET NULL`, `deleted_at: DateTime | None`. `UniqueConstraint(organization_id, kind, label)` — SUPERSEDED by the amendments: partial unique index `ux_qual_org_band_org_kind_label` (deleted_at IS NULL), `ux_` prefix per fx_rate precedent.
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
- [ ] **Step 5: write the 10-row seed JSON from spec §2.2** (both tables verbatim; floats: use `0.01, 0.032, 0.1` etc. (M1-corrected 2sf modes), magnitude as integers-as-numbers `1000 … 1000000000`).
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
    "very_low": (0.01, 0.032, 0.1), "low": (0.1, 0.32, 1), "moderate": (1, 3.2, 10),
    "high": (10, 32, 100), "very_high": (100, 160, 250),
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
        # spec §2.3: mode = geometric midpoint rounded to EXACTLY 2 significant
        # figures, uniformly (plan-gate M1). These log-decade midpoints are all
        # √10-pattern (mantissa 3.16 or 1.58), so 2sf rounding deviates ≤1.19%;
        # bound at 3.5% rejects 1sf roundings (5.13% off) with margin.
        assert abs(b["mode"] - gm) / gm < 0.035, (b["label"], b["kind"], gm)


def test_derivations_carry_provenance():
    for b in _bands():
        d = b["derivation"]
        if b["kind"] == "magnitude":
            assert "O-RA" in d and "Table 1" in d and "§6.6" in d and "p.33" in d, b["label"]
            # the two spec-§2.2 honest caveats (M3): example-scale/management
            # approval + input-ward direction-of-use vs §6.5
            assert "example" in d.lower() and "§6.5" in d, b["label"]
            if b["label"] == "very_high":
                assert "p99.9" in d, "M2 cap rationale must be pinned"
        else:
            assert "convention" in d and "O-RA" in d, b["label"]  # names the absence
            assert "priors" in d.lower(), b["label"]  # epistemic label (N1)
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
- `@dataclass(frozen=True) BoundRow`: `source_row: int; title: str; description: str | None; owner: str | None; likelihood_label: str; magnitude_label: str; category: ThreatCategory | None  # None == PARKED; raw: dict[str, str]  # EXACTLY the 3 bound cells: {likelihood, impact, category} raw values (spec §3 fixed subset — full-row capture is NOT stored); carry_along: dict[str, str]  # leftover columns user chose to keep`
- `@dataclass ConversionReport`: `created: list[ConvertedRow]` (`scenario_id, source_row, title`), `parked: list[int]`, `skipped_duplicates: list[SkippedRow]` (`source_row, title, reason ∈ {"name", "same_source"}`), `errors: list[RowError]` (`source_row, message`), `sl_note: str` (the fixed SL-not-derivable sentence from spec §3), `mapping_versions: dict`, `source_file: str`.
- `QualitativeConverterService(session)`: `async convert(*, organization_id, user, source_file: str, rows: list[BoundRow], ip_address=None) -> ConversionReport`.

Semantics (spec §3, all mandatory):
- Per row: TEF = frequency band PERT (`{"distribution": "PERT", low, mode, high}` from effective table by `likelihood_label`); PL = magnitude band PERT; vulnerability = the Task 3-verified neutral encoding; `secondary_loss=None`; unknown label → RowError (binding happened upstream; an unknown label here is a bug surface, not user error).
- `category is None` → parked (count, never error). `category` set → `threat_category`.
- `description` = original description + `"\n\n--- Register provenance ---\n"` block: owner, raw likelihood/impact/category values, carry_along pairs, `source_file` + `source_row`. Plain text, no markup.
- Build a `ScenarioForm` (`name=title`, `scenario_type` — read the enum and use the generic/default member the expert form defaults to; `source=ScenarioSource.QUALITATIVE_REGISTER_IMPORT`, `status=EntityStatus.DRAFT`) and persist via `ScenarioService.create()` — NEVER writes the ORM directly (inherits P1a create gates + validation). After create, set `scenario.vuln_framing = "legacy_residual"` and `scenario.conversion_metadata` (the service owns these two ORM-only fields; form carries neither), single flush.
- `conversion_metadata` = `{"source_file", "source_row", "raw": {"likelihood","impact","category"} (fixed 3-key subset, validator-enforced), "bindings": {"likelihood_label", "magnitude_label", "category"}, "mapping_versions": <Task 4>, "converted_at": <UTC ISO>}` validated by a `ConversionMetadata` Pydantic model (`extra="forbid"`) before assignment.
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

## Plan-gate round-1 amendments (BINDING — override conflicting text above)

Applied from the 4-reviewer round-1 findings; each delta is part of its named task's contract.

**Task 1**
- Frequency seed modes are the corrected 2sf values: `0.032 / 0.32 / 3.2 / 32 / 160` (M1; spec §2.2 updated).
- Magnitude `derivation` strings MUST additionally contain spec §2.2's two honest caveats — (a) O-RA Table 1 is an *example* scale requiring management approval (the org layer implements this), (b) direction-of-use is inverted vs O-RA (§6.5 caution named) — and the $1B rationale uses the corrected σ≈2.27-cluster p99.9 wording from spec §2.2 (M2/M3). Frequency `derivation` strings MUST contain the "priors for calibrated review, not an empirical claim" label (N1).
- `QualitativeMappingBand` inherits `IdMixin` + `TimestampMixin` (single-column UUID PK — NOT ScenarioLibraryEntry's composite-PK/custom-`__init__` pattern; "mirror" refers to seed/immutability discipline only). Seed INSERT supplies `version = 1` explicitly (ORM default is not visible to raw SQL) and uses BOUND PARAMS (`sa.text("INSERT ... VALUES (:id, :kind, ...)")`, never f-string interpolation) (Arch-N5, Sec-N2).
- `QualitativeMappingOrgBand`: replace the plain `UniqueConstraint(organization_id, kind, label)` with a PARTIAL unique index active only for live rows — `sa.Index("ux_qual_org_band_org_kind_label", "organization_id", "kind", "label", unique=True, sqlite_where=sa.text("deleted_at IS NULL"), postgresql_where=sa.text("deleted_at IS NULL"))` — so delete-then-recreate of a label works (Arch-I3). Task 4 gains a `delete then re-create same label succeeds` test.
- Migration imports ONLY the `BandSeed` class from the loader module; `json.loads`, the path anchor, and the INSERT loop live inline in the migration (precedent `c1d2e3f4a5b6`) so later loader refactors cannot break `alembic upgrade` (Arch-N3). Add a seed-migration test (`tests/migrations/`): 10 rows on upgrade, ORM read-back of one band by id (guards the `.hex` id format — recurring foot-gun), downgrade drops (Arch-N4).
- `models/__init__.py`: add both classes to the import AND `__all__` (registration is load-bearing for autogenerate + `create_all`) (Arch-N6).

**Task 3**
- The degenerate-PERT check MUST drive the RUN-EXECUTOR mapper path (`_dict_to_fair_distribution` / the code path at `run_executor.py:~128` that lowercases `distribution`), NOT fair_cam's validator with a raw uppercase dict — fair_cam accepts only lowercase `"pert"` (Spec-N4). It must ALSO assert the full `ScenarioService.create()` path accepts the chosen vuln encoding, so the fallback decision is pinned against the real gate (Arch-N1).

**Task 4**
- `get_org_band(organization_id, band_id)` filters `organization_id` IN THE WHERE (mirror `get_override`) so update AND delete both get repo-level IDOR closure; `delete_org_band` carries the org-ownership guard explicitly; the IDOR test exercises BOTH update and delete cross-org (Sec-I2).

**Task 5**
- BOTH dedup branches (name, same-source) are org-scoped: `Scenario.organization_id == organization_id` in every WHERE. Add a test: a matching `(source_file_stem, source_row)` and an identical name in a DIFFERENT org do NOT dedup (Sec-I1).
- Input bounds, fail-closed (Sec-I3): title ≤255 / description ≤4000 rely on ScenarioForm, but the converter additionally enforces BEFORE any write: each `raw` / `carry_along` VALUE ≤ 2000 chars, `carry_along` ≤ 20 keys, key names ≤ 100 chars; violation → RowError for that row (NEVER silent truncation). `ConversionMetadata` gains these as validators.
- `ConversionMetadata` gains `binding_profile_id: str | None = None` (forward-compat for P1c; Spec-I2).
- `scenario_type = ScenarioType.CUSTOM` explicitly (Meth-N4).
- Per-row isolation uses `async with session.begin_nested():` around each row's create+post-create writes; RowError rolls back only that savepoint. `ScenarioForm` CONSTRUCTION sits INSIDE the per-row try (its 4000-char description ValidationError → RowError, not batch abort). Add a test: legal-but-large carry_along whose composed provenance block exceeds 4000 chars → that row RowErrors (never truncates), siblings still created. Add a poison-path test: force a POST-flush failure on row 2 of 3 (e.g. monkeypatch the second flush or violate a DB constraint) and assert rows 1 and 3 are still created (Arch-I4).
- Batch audit row: `entity_type="scenario"`, `entity_id=organization_id` (set-level convention per `log_bulk_export`), `user_id=user.id`, `ip_address=ip_address`; `changes["created"]` holds `str(uuid)` ids and the changes dict also records `{"vuln_framing": "legacy_residual", "conversion_metadata": "set"}` markers for provenance (Arch-I2/N2, Sec-N1).
- **Task 5b (NEW — Meth-M4/Spec-I1):** modify `ScenarioService.confirm_vuln_framing` so that when `scenario.source == ScenarioSource.QUALITATIVE_REGISTER_IMPORT` the audit action written is `"scenario.confirm_frequency_baseline"` (changes dict unchanged shape) — the epistemic act on a converted row is acceptance of the frequency baseline, not a vuln-values review (vuln is a neutral pass-through). Test: converted scenario → confirm → audit row carries the converter-aware action; non-converted scenario keeps `"scenario.confirm_vuln_framing"`. Banner/refusal COPY updates land in P1c (drift-logged).
- Sweep classification (Arch-I1): add `"services/qualitative_converter.py": "shows-all-by-design",  # dedup reads ALL statuses incl DRAFT (spec §3.1)` to `AUDITED` in `tests/arch/test_draft_exclusion_sweep.py` in the same commit that creates the file, or the gate fails.
- The N≥3 adapter-iteration test is homed in `tests/contracts/test_qualitative_converter_iteration.py` per the data-contract policy (Spec-N1); the remaining converter tests stay in `tests/unit/`/`tests/integration/`.

**Task 6**
- Drift-log additions: converter-aware confirm COPY → P1c (audit action landed in P1b, Task 5b); ORM↔DTO field-sync for band models is N/A until P1c's band form DTO exists (no DTO pair in P1b; ORM snapshots cover structure) (Spec-N2).

## Scope budget

- target_task_count: 6 (single PR)
- review budget: 4-reviewer plan-gate (iterate-to-zero) + per-task methodology+spec reviews + 4-reviewer final PR-gate.
- timeline budget: 1 working session.

## Scope drift log

- 2026-07-18: `ScenarioSource` member renamed from spec's `QUALITATIVE_CONVERTED` to the enum's own anticipated `QUALITATIVE_REGISTER_IMPORT` (models/enums.py:88 placeholder comment predates the spec).
- 2026-07-18: org-band admin ROUTES/UI deferred to P1c (spec §2.1 "admin CRUD" satisfied at service layer in P1b; the P1c binding UI is the consumer).
- 2026-07-18: canonical table is org-less (mirrors ScenarioLibraryEntry); spec §2.1's "organization_id on every table" applies to the org layer only — the canonical layer is global seeded data like the scenario library.
