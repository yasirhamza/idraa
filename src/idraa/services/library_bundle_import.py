"""Scenario-library bundle import service — JSON-only, two-step preview + apply.

A *bundle* is a JSON array of ``LibraryEntrySeed``-shaped objects, identical to
``data/seed_library_entries.json``. JSON preserves int/float so there is NO
``collapse_num`` normalization (unlike the CSV scenario-import path).

This module mirrors the P1 scenario-import structure
(``services/scenario_import.py``): a parser (``parse_bundle``) feeds a pure
per-entry validator (``_validate_entries``), which the Task-3 staging/apply
functions consume. Validation REUSES the exact code-seed bar:
``LibraryEntrySeed`` (the migration-time Pydantic schema) plus
``validate_fair_distributions`` (the same FAIR finite/PERT/vuln-bound guard the
form-create and scenario-import paths use) — no FAIR math is re-derived here.

Imported content is stored GLOBALLY (no organization scoping on library
entries) and re-served to every org, so the validator additionally enforces
per-field length / list-size caps and a ``status == 'published'`` guard
(imports go live immediately) — defense-in-depth above the seed schema.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import NotFoundError
from idraa.models.csv_import_preview import PREVIEW_TTL_SECONDS, CSVImportPreview
from idraa.models.enums import (
    AssetClass,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.models.user import User
from idraa.services.audit import AuditWriter
from idraa.services.fair_cam_validation import (
    FAIRCAMValidationError,
    validate_fair_distributions,
)
from idraa.services.scenario_import import _structural_dist_problem
from idraa.services.seed_library_loader import LibraryEntrySeed

ENTITY_TYPE = "library_bundle"

# Row cap — a bundle larger than this is rejected as a whole-file hard-stop.
# Production library content is curated (~31 seed entries today); 500 is a
# generous headroom that still bounds memory + per-request validation cost.
MAX_ENTRIES = 500


class PreviewExpiredError(NotFoundError):
    """Uniform error for a token that won't apply (missing/expired/wrong-org).

    Mirrors ``scenario_import.PreviewExpiredError`` — a single class avoids an
    existence oracle. Route layer renders 409.
    """


def parse_bundle(
    data: bytes,
) -> tuple[list[tuple[int, dict[str, Any]]] | None, list[dict[str, Any]]]:
    """Parse upload bytes into ``(pairs, hard_stop_errors)``.

    ``pairs is None`` on any hard-stop (encoding / non-array / malformed-JSON /
    over-deep nesting / non-object element / row-cap). Otherwise
    ``pairs == [(index, raw_obj), ...]`` with ``index`` the 0-based array
    position, and ``hard_stop_errors == []``. Error dict shape:
    ``{"index": int, "field": str, "reason": str}`` (``index == -1`` for a
    whole-file error).
    """
    try:
        textval = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        return None, [{"index": -1, "field": "encoding", "reason": f"not valid UTF-8: {exc}"}]
    try:
        parsed = json.loads(textval)
    except json.JSONDecodeError as exc:
        return None, [{"index": -1, "field": "json", "reason": f"invalid JSON: {exc.msg}"}]
    except (RecursionError, ValueError) as exc:
        # json.loads raises RecursionError (NOT a ValueError subclass) on
        # extreme nesting; catch it explicitly so the parser returns a clean
        # json hard-stop instead of crashing the request.
        return None, [{"index": -1, "field": "json", "reason": f"JSON too deeply nested: {exc}"}]
    if not isinstance(parsed, list):
        return None, [
            {"index": -1, "field": "json", "reason": "expected a JSON array of library entries"}
        ]
    if len(parsed) > MAX_ENTRIES:
        return None, [
            {"index": -1, "field": "file", "reason": f"too many entries: max {MAX_ENTRIES}"}
        ]
    pairs: list[tuple[int, dict[str, Any]]] = []
    for idx, obj in enumerate(parsed):
        if not isinstance(obj, dict):
            return None, [
                {"index": idx, "field": "json", "reason": f"array element {idx} is not an object"}
            ]
        pairs.append((idx, obj))
    return pairs, []


# Per-field caps (Sec-B1/B2 — imported content is stored GLOBALLY and re-served
# to every org, so bound every operator-supplied string/list before it lands).
_MAX_LEN = {
    "slug": 128,
    "name": 255,
    "attack_vector": 128,
    "description": 4000,
    "canonical_fair_gap": 2000,
    "example_incidents": 4000,
}
_MAX_LIST = {
    "tags": 32,
    "source_citations": 64,
    "applicable_industries": 64,
    "applicable_sub_sectors": 64,
    "applicable_org_sizes": 64,
    "suggested_control_ids": 256,
}
_MAX_ELEM = 512


def _bounds_and_status_errors(
    idx: int, raw: dict[str, Any], seed: dict[str, Any]
) -> list[dict[str, Any]]:
    """Caps + unknown-key + status guard (run AFTER ``LibraryEntrySeed.model_validate``).

    ``LibraryEntrySeed`` does NOT set ``extra="forbid"``, so unknown keys are
    silently dropped at validation — we re-check ``raw.keys()`` against
    ``model_fields`` here to reject them explicitly rather than swallowing
    operator typos. Length / list-size caps and the ``status == 'published'``
    guard live here because imports go live globally + immediately.
    """
    errs: list[dict[str, Any]] = []
    extra = set(raw.keys()) - set(LibraryEntrySeed.model_fields)
    if extra:
        errs.append({"index": idx, "field": "entry", "reason": f"unknown keys: {sorted(extra)}"})
    for f, cap in _MAX_LEN.items():
        v = seed.get(f)
        if isinstance(v, str) and len(v) > cap:
            errs.append({"index": idx, "field": f, "reason": f"exceeds max length {cap}"})
    for f, cap in _MAX_LIST.items():
        v = seed.get(f)
        if isinstance(v, list):
            if len(v) > cap:
                errs.append({"index": idx, "field": f, "reason": f"exceeds max {cap} items"})
            if any(isinstance(e, str) and len(e) > _MAX_ELEM for e in v):
                errs.append(
                    {"index": idx, "field": f, "reason": f"an element exceeds {_MAX_ELEM} chars"}
                )
    if seed.get("status") != "published":
        errs.append(
            {
                "index": idx,
                "field": "status",
                "reason": "bundle entries must have status 'published' (imports go live immediately)",
            }
        )
    return errs


def _validate_entries(
    pairs: list[tuple[int, dict[str, Any]]],
    *,
    existing_slugs: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any] | None]]:
    """Per-entry validation (pure, no DB). Returns ``(preview, errors, seeds)``.

    ``preview[i]`` == ``{"index", "slug", "name", "action"}`` with action one of
    ``"add" | "skip" | "error"``, aligned 1:1 with ``pairs``. ``seeds[i]`` is the
    validated ``LibraryEntrySeed.model_dump()`` ONLY when ``action == "add"``,
    else ``None``. ``"skip"`` == slug already in ``existing_slugs`` OR an
    intra-bundle duplicate; ``"error"`` == any validation failure (with one or
    more dicts appended to ``errors``).
    """
    preview: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seeds: list[dict[str, Any] | None] = []
    seen: set[str] = set()

    for idx, raw in pairs:
        slug = str(raw.get("slug") or "").strip()
        name = str(raw.get("name") or "").strip()

        # 1. LibraryEntrySeed — the exact code-seed schema (≥20-char description /
        #    canonical_fair_gap, status pattern, calibration_anchor shape + tier).
        try:
            seed = LibraryEntrySeed.model_validate(raw).model_dump()
        except ValidationError as exc:
            for e in exc.errors():
                errors.append(
                    {
                        "index": idx,
                        "field": ".".join(str(p) for p in e.get("loc", ())) or "entry",
                        "reason": e["msg"],
                    }
                )
            preview.append({"index": idx, "slug": slug, "name": name, "action": "error"})
            seeds.append(None)
            continue

        # 1b. Bounds + unknown-key + status guard (Sec-B1/B2 — global stored content).
        bounds_errs = _bounds_and_status_errors(idx, raw, seed)
        if bounds_errs:
            errors.extend(bounds_errs)
            preview.append({"index": idx, "slug": slug, "name": name, "action": "error"})
            seeds.append(None)
            continue

        # 2. Enum membership (defense-in-depth — LibraryEntrySeed types these as
        #    plain str, so it does NOT enforce the enum value-set itself).
        bad_enum = None
        for col, enum_cls in (
            ("threat_event_type", ThreatCategory),
            ("threat_actor_type", ThreatActorType),
            ("asset_class", AssetClass),
        ):
            if seed[col] not in {m.value for m in enum_cls}:
                bad_enum = col
                break
        if bad_enum is not None:
            errors.append(
                {
                    "index": idx,
                    "field": bad_enum,
                    "reason": f"{seed[bad_enum]!r} is not a valid {bad_enum}",
                }
            )
            preview.append({"index": idx, "slug": slug, "name": name, "action": "error"})
            seeds.append(None)
            continue

        # 3. Distribution structural guard (exact keys + PERT, or lognormal on
        #    tef/pl/sl — Epic B #326). Reuses the SAME helper as the scenario
        #    import §2.5 guard so the two enforcement points cannot drift. vuln
        #    stays PERT-only. Numeric finiteness + sigma bound are step 4.
        dist_bad = None
        dist_reason = ""
        for col, allow_ln in (
            ("threat_event_frequency", True),
            ("vulnerability", False),
            ("primary_loss", True),
            ("secondary_loss", True),
        ):
            d = seed[col]
            if d is None:
                continue
            problem = _structural_dist_problem(col, d, allow_lognormal=allow_ln)
            if problem is not None:
                dist_bad = col
                dist_reason = problem
                break
        if dist_bad is not None:
            errors.append({"index": idx, "field": dist_bad, "reason": dist_reason})
            preview.append({"index": idx, "slug": slug, "name": name, "action": "error"})
            seeds.append(None)
            continue

        # 4. FAIR distribution validation — the EXACT code-seed bar (finite guard
        #    + PERT + vulnerability ∈ [0, 1]). Reuses the same wrapper the
        #    form-create and scenario-import paths use; no FAIR math re-derived.
        try:
            validate_fair_distributions(
                threat_event_frequency=seed["threat_event_frequency"],
                vulnerability=seed["vulnerability"],
                primary_loss=seed["primary_loss"],
                secondary_loss=seed["secondary_loss"],
            )
        except FAIRCAMValidationError as exc:
            errors.append({"index": idx, "field": "distributions", "reason": str(exc)})
            preview.append({"index": idx, "slug": slug, "name": name, "action": "error"})
            seeds.append(None)
            continue

        # 5. Skip-duplicate (only AFTER the entry is otherwise valid).
        if slug in seen or slug in existing_slugs:
            preview.append({"index": idx, "slug": slug, "name": name, "action": "skip"})
            seeds.append(None)
            continue
        seen.add(slug)

        preview.append({"index": idx, "slug": slug, "name": name, "action": "add"})
        seeds.append(seed)

    return preview, errors, seeds


# ---------------------------------------------------------------------------
# Task 3: two-step staging + apply.
#
# Mirrors ``services/scenario_import.py``: ``validate_upload`` parses +
# validates + stages the raw bytes under a 10-min token; ``apply_validated_
# preview`` re-parses the SAME staged bytes (TOCTOU guard), re-validates, and
# inserts the add-entries as ``source='imported'`` published rows. Both flush
# only — the route's get_db dependency owns the commit.
# ---------------------------------------------------------------------------


async def _existing_slugs(db: AsyncSession) -> set[str]:
    """All slugs already in the library (any version). The dedup key.

    Library entries are GLOBAL (no org scoping), so a slug collides against the
    whole table — seed AND previously-imported rows alike.
    """
    rows = (await db.execute(select(ScenarioLibraryEntry.slug))).scalars().all()
    return set(rows)


async def _store_preview(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    data: bytes,
) -> str:
    """Stage the raw bundle bytes under a 10-min token; return the token (str UUID)."""
    row = CSVImportPreview(
        organization_id=org_id,
        created_by_user_id=user_id,
        entity_type=ENTITY_TYPE,
        csv_bytes=data,
        expires_at=datetime.now(UTC) + timedelta(seconds=PREVIEW_TTL_SECONDS),
    )
    db.add(row)
    await db.flush()
    return str(row.id)


async def validate_upload(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    data: bytes,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Step 1: parse + validate + stage. Never mutates library entries.

    Always returns a token (even on a hard-stop bundle) so the confirm step can
    render the error page from the staged bytes.
    """
    pairs, hard_stop = parse_bundle(data)
    if pairs is None:
        token = await _store_preview(db, org_id=org_id, user_id=user_id, data=data)
        return token, [], hard_stop

    existing = await _existing_slugs(db)
    preview, errors, _ = _validate_entries(pairs, existing_slugs=existing)
    token = await _store_preview(db, org_id=org_id, user_id=user_id, data=data)
    return token, preview, errors


# Raw INSERT column list — must match ``scenario_library_entries`` (model +
# migration 0897a0ff350e) plus ``source``. Every NOT-NULL column is listed:
# the migration relies on server_default='seed' for ``source``, but imports MUST
# stamp 'imported' explicitly, so ``source`` is added here. ``status='published'``
# is guaranteed by the validator (status guard). Parameterized binds only — no
# string interpolation of operator-supplied data (SQL injection).
_INSERT_LIBRARY_ENTRY = text(
    """
    INSERT INTO scenario_library_entries
      (id, version, slug, name, status, threat_event_type,
       threat_actor_type, asset_class, attack_vector, tags,
       description, example_incidents, source_citations,
       canonical_fair_gap, applicable_industries,
       applicable_sub_sectors, applicable_org_sizes,
       threat_event_frequency, vulnerability, primary_loss,
       secondary_loss, suggested_control_ids, standards_references,
       calibration_anchor, loss_tier, loss_shape, loss_form_profile, source, row_version, created_at, updated_at)
    VALUES
      (:id, 1, :slug, :name, :status, :tet,
       :tat, :ac, :av, :tags,
       :desc, :ex, :cit,
       :gap, :ind,
       :sub, :sizes,
       :tef, :vuln, :pl,
       :sl, :ctrl, :std,
       :anchor, :loss_tier, :loss_shape, :lfp, 'imported', 1, :now, :now)
    """
)


def _insert_params(seed: dict[str, Any], now: str) -> dict[str, Any]:
    """Build the bind params for one library-entry INSERT.

    ``json.dumps`` every list/dict column (the JSON columns are TEXT under
    SQLite; the raw INSERT bypasses SQLAlchemy's JSON serialization). ``id`` is
    a fresh no-hyphen ``uuid4().hex`` — the recurring foot-gun: the column's
    ``UuidType(as_uuid=True)`` adapter binds ids as 32-char no-hyphen hex, so a
    hyphenated ``str(uuid4())`` would 404 every id-based ORM query.
    """

    def j(v: Any) -> Any:
        return json.dumps(v) if isinstance(v, (list, dict)) else v

    return {
        "id": uuid.uuid4().hex,
        "slug": seed["slug"],
        "name": seed["name"],
        "status": seed["status"],
        "tet": seed["threat_event_type"],
        "tat": seed["threat_actor_type"],
        "ac": seed["asset_class"],
        "av": seed.get("attack_vector"),
        "tags": j(seed.get("tags", [])),
        "desc": seed["description"],
        "ex": seed.get("example_incidents"),
        "cit": j(seed.get("source_citations", [])),
        "gap": seed["canonical_fair_gap"],
        "ind": j(seed.get("applicable_industries")),
        "sub": j(seed.get("applicable_sub_sectors")),
        "sizes": j(seed.get("applicable_org_sizes")),
        "tef": j(seed["threat_event_frequency"]),
        "vuln": j(seed["vulnerability"]),
        "pl": j(seed["primary_loss"]),
        "sl": j(seed.get("secondary_loss")),
        "ctrl": j(seed.get("suggested_control_ids", [])),
        "std": j(seed.get("standards_references")),
        "anchor": j(seed["calibration_anchor"]),
        "loss_tier": seed["loss_tier"],
        # Milestone B (#loss-pert-overhaul): .get default so back-compat
        # bundles (no key) land as capped — matching the seed-schema default.
        "loss_shape": seed.get("loss_shape", "capped"),
        # D-i (#497): .get default so back-compat bundles (no key) don't KeyError.
        "lfp": j(seed.get("loss_form_profile", [])),
        "now": now,
    }


async def apply_validated_preview(
    db: AsyncSession,
    *,
    token: str,
    org_id: uuid.UUID,
    user: User,
    ip_address: str | None = None,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Step 2: re-parse the staged bytes + insert the non-dup valid entries.

    Raises ``PreviewExpiredError`` uniformly for malformed/missing/expired/
    wrong-org tokens (no existence oracle). TOCTOU guard: the staged bytes are
    re-parsed + re-validated here rather than trusting the preview verdict.
    """
    try:
        token_uuid = uuid.UUID(token)
    except (TypeError, ValueError) as exc:
        raise PreviewExpiredError("preview token is malformed; please re-upload") from exc

    preview_row = (
        await db.execute(select(CSVImportPreview).where(CSVImportPreview.id == token_uuid))
    ).scalar_one_or_none()
    if preview_row is None or preview_row.organization_id != org_id:
        raise PreviewExpiredError("preview not found; please re-upload")

    expires_at = preview_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        await db.delete(preview_row)
        await db.flush()
        raise PreviewExpiredError("preview expired; please re-upload")

    pairs, hard_stop = parse_bundle(preview_row.csv_bytes)
    if pairs is None:
        await _finalise_apply(
            db,
            preview_row=preview_row,
            org_id=org_id,
            user_id=user.id,
            ip_address=ip_address,
            imported=0,
            skipped=0,
            errors=hard_stop,
        )
        return 0, 0, hard_stop

    existing = await _existing_slugs(db)
    preview, errors, seeds = _validate_entries(pairs, existing_slugs=existing)
    skipped = sum(1 for p in preview if p["action"] == "skip")
    imported = 0
    now = datetime.now(UTC).isoformat()
    for seed in seeds:
        if seed is None:
            continue
        await db.execute(_INSERT_LIBRARY_ENTRY, _insert_params(seed, now))
        imported += 1

    await _finalise_apply(
        db,
        preview_row=preview_row,
        org_id=org_id,
        user_id=user.id,
        ip_address=ip_address,
        imported=imported,
        skipped=skipped,
        errors=errors,
    )
    return imported, skipped, errors


async def _finalise_apply(
    db: AsyncSession,
    *,
    preview_row: CSVImportPreview,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    ip_address: str | None,
    imported: int,
    skipped: int,
    errors: list[dict[str, Any]],
) -> None:
    """Write one summary ``library_bundle.import`` audit row + delete the preview."""
    await AuditWriter(db).log(
        organization_id=org_id,
        entity_type=ENTITY_TYPE,
        entity_id=preview_row.id,
        action="library_bundle.import",
        changes={
            "imported": [None, imported],
            "skipped": [None, skipped],
            "errors_count": [None, len(errors)],
        },
        user_id=user_id,
        ip_address=ip_address,
    )
    await db.delete(preview_row)
    await db.flush()


def generate_template_json() -> bytes:
    """Downloadable JSON bundle template: an array of one example entry.

    Used by Task 4's template route. Mirrors the LibraryEntrySeed shape
    (``data/seed_library_entries.json``) so a downloaded template round-trips
    through ``validate_upload`` → ``apply_validated_preview``.
    """
    sample = [
        {
            "slug": "example-imported-scenario",
            "name": "Example imported scenario",
            "status": "published",
            "threat_event_type": "ransomware",
            "threat_actor_type": "cybercriminals",
            "asset_class": "systems",
            "attack_vector": "phishing_then_lateral_movement",
            "tags": ["example"],
            "description": (
                "Replace this with a 20+ character description of the scenario "
                "this library entry models."
            ),
            "example_incidents": None,
            "source_citations": [],
            "canonical_fair_gap": (
                "Replace with a 20+ character note on which FAIR canonical gap this entry fills."
            ),
            "applicable_industries": None,
            "applicable_sub_sectors": None,
            "applicable_org_sizes": None,
            "threat_event_frequency": {
                "distribution": "PERT",
                "low": 0.1,
                "mode": 0.5,
                "high": 2,
            },
            "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
            "primary_loss": {
                "distribution": "PERT",
                "low": 100000,
                "mode": 1000000,
                "high": 15000000,
            },
            "secondary_loss": {
                "distribution": "PERT",
                "low": 50000,
                "mode": 500000,
                "high": 5000000,
            },
            "suggested_control_ids": [],
            "standards_references": None,
            "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
        }
    ]
    return (json.dumps(sample, indent=2) + "\n").encode("utf-8")
