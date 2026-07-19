"""Scenario import service — two-step preview + apply, CSV/JSON.

Clones the overlays importer (services/overlays_importer.py) structure:
``validate_upload`` (parse + validate + stage under a 10-min token) and
``apply_validated_preview`` (re-parse + create + delete preview). The two
parsers (CSV/JSON) feed one ``_validate_rows`` pipeline. Create-only with a
skip-duplicate guard (active same-name scenario already exists → skip).

#27 Task 7: CSV import cannot express ``lognormal_mixture`` — a CSV row's
distribution kind columns (``tef_dist``/``pl_dist``/``sl_dist``) select
which of the four flat columns to assemble (``scenario_import_parsers.
_assemble_distributions``), and there is no column for a component list.
Mixture authoring/import is JSON-only; a CSV row remains a single lognormal
(or PERT). See ``generate_template_csv`` below and
``scenario_export``'s ``_dist_cells`` for the export-side flatten.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import NotFoundError
from idraa.models.attack import ATTACK_DOMAINS, AttackTechnique, ScenarioAttackMapping
from idraa.models.csv_import_preview import PREVIEW_TTL_SECONDS as PREVIEW_TTL_SECONDS
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.enums import (
    AssetClass,
    EntityStatus,
    ScenarioEffect,
    ScenarioSource,
    ScenarioType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.fx_rate import FX_RATE_MAX, FX_RATE_MIN
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.schemas.scenario import ScenarioForm
from idraa.services.audit import AuditWriter
from idraa.services.fair_cam_validation import (
    FAIRCAMValidationError,
    validate_fair_distributions,
)
from idraa.services.fx_rates import is_selectable_currency
from idraa.services.scenario_import_parsers import (
    parse_csv_flat,
    parse_json_nested,
    sniff_format,
)
from idraa.services.scenarios import ScenarioService

ENTITY_TYPE = "scenario"


class PreviewExpiredError(NotFoundError):
    """Uniform error for a token that won't apply (missing/expired/wrong-org).

    Mirrors overlays_importer.PreviewExpiredError — a single class avoids an
    existence oracle. Route layer renders 409.
    """


def _enum_ok(value: str | None, enum_cls: type[StrEnum]) -> bool:
    if value is None:
        return True
    return value in {e.value for e in enum_cls}


def _structural_dist_problem(col: str, dist: Any, *, allow_lognormal: bool) -> str | None:
    """Return an error string if ``dist`` is not a valid PERT (or, when allowed,
    lognormal / lognormal_mixture) structural shape; ``None`` if it is
    structurally sound.

    This is the §2.5 anti-blob-smuggling guard (I2/Meth-I1 + B4/Sec-B2 + Epic B
    #326; #27 extends it to lognormal_mixture). It PRESERVES the exact-key-set
    property — a distribution dict may carry ONLY its kind's keys, so a giant
    blob cannot be smuggled into the JSON column alongside the legitimate keys
    (for lognormal_mixture, the top-level dict AND every component dict are
    each exact-key-set checked). It does structural + numeric-TYPE gating
    only; numeric finiteness, the ``0 < sigma <= 10`` bound, weight
    positivity/sum, and the component-count cap are enforced downstream by
    ``validate_fair_distributions`` (Sec-I1/Sec-I2/Sec-N1).
    """
    if not isinstance(dist, dict):
        return f"{col} must be a distribution object"
    kind = str(dist.get("distribution", "")).lower()
    if kind == "lognormal":
        if not allow_lognormal:
            return f"{col}.distribution lognormal not allowed for {col} (must be PERT)"
        if set(dist.keys()) != {"distribution", "mean", "sigma"}:
            return f"{col} lognormal must have exactly keys {{distribution, mean, sigma}}"
        for k in ("mean", "sigma"):
            v = dist.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return f"{col}.{k} must be numeric"
        return None
    if kind == "lognormal_mixture":
        # #27: same allow_lognormal gate as scalar lognormal reuses (mixture
        # is a lognormal shape — tef/pl/sl only; vulnerability stays PERT-only).
        # Numeric finiteness, the 0 < sigma <= 10 bound, weight positivity,
        # weight-sum-to-1, and the component-count cap are enforced downstream
        # by _validate_finite (Sec-I1/Sec-I2/Sec-N1) — this is structural +
        # numeric-TYPE gating only, mirroring the scalar lognormal branch above.
        if not allow_lognormal:
            return f"{col}.distribution lognormal_mixture not allowed for {col} (must be PERT)"
        if set(dist.keys()) != {"distribution", "components"}:
            return f"{col} lognormal_mixture must have exactly keys {{distribution, components}}"
        components = dist.get("components")
        if not isinstance(components, list) or len(components) == 0:
            return f"{col}.components must be a non-empty list"
        for i, comp in enumerate(components):
            if not isinstance(comp, dict):
                return f"{col}.components[{i}] must be an object"
            if set(comp.keys()) != {"mean", "sigma", "weight"}:
                return f"{col}.components[{i}] must have exactly keys {{mean, sigma, weight}}"
            for k in ("mean", "sigma", "weight"):
                v = comp.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return f"{col}.components[{i}].{k} must be numeric"
        return None
    # PERT (and legacy uppercase "PERT"); any other kind falls through to error.
    if set(dist.keys()) != {"distribution", "low", "mode", "high"}:
        return f"{col} must have exactly keys {{distribution, low, mode, high}}"
    if kind != "pert":
        return f"{col}.distribution must be PERT or lognormal"
    return None


# #482: enterprise/ics T#### plus MITRE ATLAS AML.T#### (parent-only, both).
_TECHNIQUE_ID_RE = re.compile(r"^(T\d{4}|AML\.T\d{4})$")


def _structural_attack_techniques_problem(raw: Any) -> str | None:
    """Return an error string if ``raw`` is not a valid ``attack_techniques`` shape.

    Issue #475 T12: JSON-only optional field — a list of natural-key dicts
    ``{domain, technique_id, rationale?}``. ``domain`` must be one of
    ``ATTACK_DOMAINS``; ``technique_id`` must match ``^T\\d{4}$``; ``rationale``
    (Sec-N4) is optional and, when present, a string of at most 2000 chars.
    Exact-key-set enforced (mirrors the §2.5 distribution guard) so a blob
    cannot be smuggled in alongside the three legitimate keys. Dedupe + the
    ``MAX_ATTACK_MAPPINGS`` cap are NOT done here — the caller applies those
    after this structural pass succeeds (Sec-I3/Sec2-N2).
    """
    if not isinstance(raw, list):
        return "attack_techniques must be a list"
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return f"attack_techniques[{i}] must be an object"
        extra_keys = set(item.keys()) - {"domain", "technique_id", "rationale"}
        if extra_keys:
            return f"attack_techniques[{i}] has unexpected keys: {sorted(extra_keys)}"
        domain = item.get("domain")
        if domain not in ATTACK_DOMAINS:
            return f"attack_techniques[{i}].domain must be one of {list(ATTACK_DOMAINS)}"
        technique_id = item.get("technique_id")
        if not isinstance(technique_id, str) or not _TECHNIQUE_ID_RE.match(technique_id):
            return f"attack_techniques[{i}].technique_id must match ^T\\d{{4}}$"
        rationale = item.get("rationale")
        if rationale is not None and (not isinstance(rationale, str) or len(rationale) > 2000):
            return f"attack_techniques[{i}].rationale must be a string of at most 2000 characters"
    return None


def _validate_rows(
    pairs: list[tuple[int, dict[str, Any]]],
    *,
    existing_names: set[str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[ScenarioForm | None],
    list[tuple[str, str]],
    list[list[dict[str, Any]] | None],
]:
    """Per-row validation, pure (no DB). Returns
    ``(preview, errors, forms, entry_meta, attack_meta)``.

    ``forms[i]`` is a ScenarioForm only when ``preview[i]["action"]=="create"``.
    ``entry_meta[i]`` is ``(entry_currency_str, entry_rate_str)`` for every row
    (empty strings when absent); populated by popping the two keys from ``fd``
    BEFORE ``ScenarioForm(**fd)`` (which is ``extra='forbid'``).
    ``attack_meta[i]`` (issue #475 T12) is the structurally-validated,
    deduped-and-capped ``attack_techniques`` list for a "create" row, or
    ``None`` for every other row (JSON-only; CSV rows never carry the key).
    Duplicate (active same-name existing, or intra-file repeat) → action "skip",
    form None, NOT added to errors. Validation failure → action "error",
    form None, one or more error dicts appended.
    """
    preview: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    forms: list[ScenarioForm | None] = []
    entry_meta: list[tuple[str, str]] = []
    attack_meta: list[list[dict[str, Any]] | None] = []
    seen_names: set[str] = set()

    for line, fd in pairs:
        # P2 multi-currency: pop BEFORE ScenarioForm(**fd) (extra='forbid').
        # CSV: appended by parse_csv_flat outside _field_dict.
        # JSON: fd = dict(obj), so present when the file included them.
        meta_currency = str(fd.pop("entry_currency", "") or "").strip()
        meta_rate = str(fd.pop("entry_rate", "") or "").strip()
        # Issue #475 T12: JSON-only, popped before ScenarioForm(**fd)
        # (extra='forbid') the same way entry_currency/entry_rate are.
        raw_attack_techniques = fd.pop("attack_techniques", None)

        name = str(fd.get("name") or "").strip()
        key = name.casefold()

        # 1. Pydantic structural validation (extra='forbid' blocks smuggling).
        try:
            form = ScenarioForm(**fd)
        except ValidationError as exc:
            for err in exc.errors():
                errors.append(
                    {
                        "line": line,
                        "column": ".".join(str(p) for p in err.get("loc", ())) or "row",
                        "reason": err["msg"],
                    }
                )
            preview.append({"line": line, "name": name, "action": "error"})
            forms.append(None)
            entry_meta.append((meta_currency, meta_rate))
            attack_meta.append(None)
            continue

        # 2. Enum membership (ScenarioForm carries these as plain strings).
        enum_problem: tuple[str, type[StrEnum]] | None = None
        for col, enum_cls in (
            ("threat_category", ThreatCategory),
            ("threat_actor_type", ThreatActorType),
            ("asset_class", AssetClass),
            # effect is unreachable here since ScenarioForm's field validator
            # (PR #451 Sec-N1) rejects non-enum strings at step 1 — kept as
            # intentionally-redundant defense-in-depth on this import path.
            ("effect", ScenarioEffect),
            ("scenario_type", ScenarioType),
            ("status", EntityStatus),
        ):
            raw = getattr(form, col)
            raw_value = raw.value if hasattr(raw, "value") else raw
            if not _enum_ok(raw_value, enum_cls):
                enum_problem = (col, enum_cls)
                break
        if enum_problem is not None:
            bad_col, bad_enum_cls = enum_problem
            errors.append(
                {
                    "line": line,
                    "column": bad_col,
                    "reason": (
                        f"{getattr(form, bad_col)!r} is not a valid {bad_col}; "
                        f"expected one of {sorted(e.value for e in bad_enum_cls)}"
                    ),
                }
            )
            preview.append({"line": line, "name": name, "action": "error"})
            forms.append(None)
            entry_meta.append((meta_currency, meta_rate))
            attack_meta.append(None)
            continue

        # 2.4 Create-domain parity (epic #34 P1a): ScenarioService's
        # _stamp_new_scenario chokepoint rejects DEPRECATED/DELETED at create,
        # so accept only the creatable subset here — a row must not preview
        # as "create" and then 422 at apply.
        raw_status = form.status
        status_value = raw_status.value if hasattr(raw_status, "value") else raw_status
        if status_value not in (EntityStatus.ACTIVE.value, EntityStatus.DRAFT.value):
            errors.append(
                {
                    "line": line,
                    "column": "status",
                    "reason": (
                        f"{status_value!r} is not a creatable status; new "
                        "scenarios may only be imported as 'active' or 'draft'"
                    ),
                }
            )
            preview.append({"line": line, "name": name, "action": "error"})
            forms.append(None)
            entry_meta.append((meta_currency, meta_rate))
            attack_meta.append(None)
            continue

        # 2.5 Distribution structural guard (I2/Meth-I1 + B4/Sec-B2 + Epic B #326):
        # each distribution dict must carry EXACTLY its kind's keys — PERT →
        # {distribution, low, mode, high}; lognormal → {distribution, mean,
        # sigma} (tef/pl/sl only; vulnerability is PERT-only). The exact-key-set
        # check blocks a giant blob smuggled into the JSON column. Enforced HERE
        # because ScenarioForm types these as bare dict[str, Any] and
        # validate_fair_distributions ignores extra keys. Numeric finiteness +
        # the 0 < sigma <= 10 bound are enforced downstream in step 3.
        dist_problem: str | None = None
        col_name: str = ""
        for col, allow_ln in (
            ("threat_event_frequency", True),
            ("vulnerability", False),
            ("primary_loss", True),
            ("secondary_loss", True),
        ):
            dist = getattr(form, col)
            if dist is None:
                continue  # only secondary_loss may be None
            dist_problem = _structural_dist_problem(col, dist, allow_lognormal=allow_ln)
            if dist_problem is not None:
                # Column name always references the distribution so existing
                # callers/tests keying on ".distribution" keep matching.
                col_name = f"{col}.distribution"
                break
        if dist_problem is not None:
            errors.append({"line": line, "column": col_name, "reason": dist_problem})
            preview.append({"line": line, "name": name, "action": "error"})
            forms.append(None)
            entry_meta.append((meta_currency, meta_rate))
            attack_meta.append(None)
            continue

        # 3. FAIR distribution validation (same call ScenarioService.create makes;
        #    now incl. the v3 vulnerability [0,1] check added to the wrapper in Step 0).
        try:
            validate_fair_distributions(
                threat_event_frequency=form.threat_event_frequency,
                vulnerability=form.vulnerability,
                primary_loss=form.primary_loss,
                secondary_loss=form.secondary_loss,
            )
        except FAIRCAMValidationError as exc:
            # Name the offending distribution from the first ValidationResult's
            # field_name (e.g. 'primary_loss_loss' → contains 'primary_loss');
            # fall back to the generic 'distributions' when none is carried.
            col_name = exc.errors[0][0] if getattr(exc, "errors", None) else "distributions"
            errors.append({"line": line, "column": col_name, "reason": str(exc)})
            preview.append({"line": line, "name": name, "action": "error"})
            forms.append(None)
            entry_meta.append((meta_currency, meta_rate))
            attack_meta.append(None)
            continue

        # 3.5 ATT&CK technique mapping structural validation (issue #475 T12):
        # optional JSON-only field. Structural shape first (§2.5-style guard),
        # THEN dedupe (domain, technique_id) pairs, THEN cap at
        # MAX_ATTACK_MAPPINGS (Sec-I3: dedupe-first, or legitimate files whose
        # duplicates collapse under the cap get wrongly rejected). Malformed
        # shape or an over-cap deduped list → row error (mirrors the
        # distribution structural guard's degrade-to-row-error behavior rather
        # than an unhandled IntegrityError/500 at flush).
        resolved_attack_techniques: list[dict[str, Any]] | None = None
        attack_problem: str | None = None
        if raw_attack_techniques is not None:
            attack_problem = _structural_attack_techniques_problem(raw_attack_techniques)
            if attack_problem is None:
                # Arch3-N1: function-level import — services→routes edge,
                # deferred to keep it acyclic-but-explicit (mirrors the
                # precedent at services/scenario_library.py:181).
                from idraa.routes.scenario_form_helpers import MAX_ATTACK_MAPPINGS

                deduped: list[dict[str, Any]] = []
                seen_mapping_keys: set[tuple[str, str]] = set()
                for item in raw_attack_techniques:
                    mapping_key = (item["domain"], item["technique_id"])
                    if mapping_key in seen_mapping_keys:
                        continue
                    seen_mapping_keys.add(mapping_key)
                    deduped.append(item)
                if len(deduped) > MAX_ATTACK_MAPPINGS:
                    attack_problem = (
                        f"attack_techniques has {len(deduped)} distinct mappings after "
                        f"dedupe, exceeding the maximum of {MAX_ATTACK_MAPPINGS}"
                    )
                else:
                    resolved_attack_techniques = deduped
        if attack_problem is not None:
            errors.append({"line": line, "column": "attack_techniques", "reason": attack_problem})
            preview.append({"line": line, "name": name, "action": "error"})
            forms.append(None)
            entry_meta.append((meta_currency, meta_rate))
            attack_meta.append(None)
            continue

        # 4. Duplicate guard (after the row is otherwise valid).
        if key in seen_names or key in existing_names:
            preview.append({"line": line, "name": name, "action": "skip"})
            forms.append(None)
            entry_meta.append((meta_currency, meta_rate))
            attack_meta.append(None)
            continue
        seen_names.add(key)

        preview.append({"line": line, "name": name, "action": "create"})
        forms.append(form)
        entry_meta.append((meta_currency, meta_rate))
        attack_meta.append(resolved_attack_techniques)

    return preview, errors, forms, entry_meta, attack_meta


async def _existing_scenario_names(db: AsyncSession, *, org_id: uuid.UUID) -> set[str]:
    """casefold()-ed names of ALL scenarios in the org, any status (the dedup key).

    Parity with the qualitative converter (spec §3.1): a DRAFT sitting in the
    review queue must block a same-name import, or promote-after-import yields
    two ACTIVE scenarios sharing one name. The pre-P1a ACTIVE-only filter was
    harmless only while non-ACTIVE states were unreachable for scenarios.
    """
    stmt = select(Scenario.name).where(Scenario.organization_id == org_id)
    rows = (await db.execute(stmt)).scalars().all()
    return {name.casefold() for name in rows}


def _parse(
    data: bytes, fmt: str
) -> tuple[list[tuple[int, dict[str, Any]]] | None, list[dict[str, Any]]]:
    return parse_json_nested(data) if fmt == "json" else parse_csv_flat(data)


async def _store_preview(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    data: bytes,
    fmt: str,
) -> str:
    """Stage the raw bytes under a 10-min token; return the token (str UUID).

    The resolved format (``"csv"``/``"json"``) is persisted in ``entity_type``
    as ``"scenario:<fmt>"`` so the apply path parses with the SAME format the
    preview used. Filename/content-type aren't available at apply time, so
    persisting the decision (rather than re-sniffing) is what keeps preview and
    confirm in lock-step — including the format-conflict path, where the stored
    ``fmt`` reproduces the preview's verdict on re-parse. ``entity_type`` is a
    free-form String(64), so this needs no migration.
    """
    expires_at = datetime.now(UTC) + timedelta(seconds=PREVIEW_TTL_SECONDS)
    row = CSVImportPreview(
        organization_id=org_id,
        created_by_user_id=user_id,
        entity_type=f"{ENTITY_TYPE}:{fmt}",
        csv_bytes=data,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return str(row.id)


def _fmt_from_entity_type(entity_type: str) -> str:
    """Recover the stored format from ``"scenario:<fmt>"``; default ``"csv"``."""
    _, _, fmt = entity_type.partition(":")
    return fmt if fmt in ("csv", "json") else "csv"


async def validate_upload(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    data: bytes,
    filename: str | None,
    content_type: str | None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Step 1: sniff + parse + validate + stage. Never mutates scenarios.

    Always returns a token (even on a fully-invalid upload) so the route can
    render the preview page with the errors.
    """
    try:
        fmt = sniff_format(filename=filename, content_type=content_type, data=data)
    except ValueError as exc:
        token = await _store_preview(db, org_id=org_id, user_id=user_id, data=data, fmt="csv")
        return token, [], [{"line": 0, "column": "format", "reason": str(exc)}]

    pairs, hard_stop = _parse(data, fmt)
    if pairs is None:
        token = await _store_preview(db, org_id=org_id, user_id=user_id, data=data, fmt=fmt)
        return token, [], hard_stop

    existing = await _existing_scenario_names(db, org_id=org_id)
    preview, errors, _, _meta, _attack_meta = _validate_rows(pairs, existing_names=existing)
    token = await _store_preview(db, org_id=org_id, user_id=user_id, data=data, fmt=fmt)
    return token, preview, errors


async def apply_validated_preview(
    db: AsyncSession,
    *,
    token: str,
    org_id: uuid.UUID,
    user: User,
    ip_address: str | None = None,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Step 2: re-parse the staged bytes + create the non-dup valid rows.

    Raises ``PreviewExpiredError`` uniformly for malformed/missing/expired/
    wrong-org tokens (no existence oracle).
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

    data = preview_row.csv_bytes
    pairs, hard_stop = _parse(data, _fmt_from_entity_type(preview_row.entity_type))
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

    existing = await _existing_scenario_names(db, org_id=org_id)
    preview, errors, forms, entry_meta, attack_meta = _validate_rows(pairs, existing_names=existing)

    svc = ScenarioService(db)
    imported = 0
    skipped = sum(1 for p in preview if p["action"] == "skip")
    apply_errors: list[dict[str, Any]] = list(errors)

    # Issue #475 T12/Sec-I3: one catalog load for the whole apply run (~280
    # rows) — resolved in-memory per mapping below, NEVER one SELECT per
    # mapping entry (a large file would otherwise issue thousands of
    # sequential queries in one admin request on a single-process app).
    catalog_rows = (await db.execute(select(AttackTechnique))).scalars().all()
    catalog_by_key: dict[tuple[str, str], AttackTechnique] = {
        (t.domain, t.technique_id): t for t in catalog_rows
    }

    for meta, form, (em_currency, em_rate_str), am_list in zip(
        preview, forms, entry_meta, attack_meta, strict=True
    ):
        if form is None:
            continue  # error/skip rows already accounted for

        # P2 multi-currency: validate entry_currency in the apply layer (has db).
        # Exported CSV pl_*/sl_* are already USD — import carries these as pure
        # provenance metadata and does NOT call convert_loss_inputs_to_usd.
        entry_currency = (em_currency or "USD").strip() or "USD"
        if not await is_selectable_currency(db, org_id, entry_currency):
            apply_errors.append(
                {
                    "line": meta["line"],
                    "column": "entry_currency",
                    "reason": (
                        f"entry_currency {entry_currency!r} is not available; "
                        "configure a rate for this currency first"
                    ),
                }
            )
            skipped += 1
            continue

        # Bounds-check entry_rate when provided (close the divide-amplification
        # class: garbage/zero/negative provenance rate is rejected, never stored).
        entry_rate: Decimal | None = None
        if em_rate_str:
            try:
                entry_rate = Decimal(em_rate_str)
            except (InvalidOperation, ArithmeticError):
                apply_errors.append(
                    {
                        "line": meta["line"],
                        "column": "entry_rate",
                        "reason": f"entry_rate {em_rate_str!r} is not a valid decimal",
                    }
                )
                skipped += 1
                continue
            # NaN/sNaN/Inf/-Inf are valid Decimal literals but signal on any
            # comparison, which would raise InvalidOperation on the bounds check
            # below (outside this try/except). Reject them explicitly first.
            if not entry_rate.is_finite():
                apply_errors.append(
                    {
                        "line": meta["line"],
                        "column": "entry_rate",
                        "reason": f"entry_rate {em_rate_str!r} is not a finite number",
                    }
                )
                skipped += 1
                continue
            if not (FX_RATE_MIN <= entry_rate <= FX_RATE_MAX):
                apply_errors.append(
                    {
                        "line": meta["line"],
                        "column": "entry_rate",
                        "reason": (
                            f"entry_rate {entry_rate} is outside the allowed range "
                            f"[{FX_RATE_MIN}, {FX_RATE_MAX}]"
                        ),
                    }
                )
                skipped += 1
                continue

        # I4/Sec-I3: force source to FILE_IMPORT and clear library_entry_id
        # regardless of what the file claimed. library_entry_id is a DECLARED
        # ScenarioForm field (extra="forbid" does NOT block it); if it reached
        # ScenarioService.create populated, create() would resolve the library
        # entry and flip source → LIBRARY_DERIVED, overriding the FILE_IMPORT we
        # stamp here. Clearing it before create is the only thing preventing a
        # file from claiming library provenance.
        form = form.model_copy(
            update={"source": ScenarioSource.FILE_IMPORT, "library_entry_id": None}
        )
        try:
            scenario = await svc.create(
                organization_id=org_id,
                form=form,
                current_user=user,
                ip_address=ip_address,
            )
        except (FAIRCAMValidationError, ValueError) as exc:
            apply_errors.append(
                {"line": meta["line"], "column": "row", "reason": f"create failed: {exc}"}
            )
            skipped += 1
            continue

        # Set provenance metadata on the created row (mirror Task 3 set-on-row).
        # NO conversion: stored distributions are already USD (Invariant 3).
        scenario.entry_currency = entry_currency
        scenario.entry_rate = entry_rate
        imported += 1

        # Issue #475 T12: resolve this row's ATT&CK technique mappings against
        # the preloaded catalog. Unknown or deprecated → apply_errors + SKIP
        # THAT MAPPING (the scenario itself still imports; a mapping-skip does
        # NOT bump the `skipped` SCENARIO counter). Resolved mappings ALWAYS
        # insert as source="user" — file provenance is not trusted (mirrors
        # the I4/Sec-I3 library_entry_id-clearing precedent above).
        seen_mapping_keys: set[tuple[str, str]] = set()
        for am in am_list or []:
            mapping_key = (am["domain"], am["technique_id"])
            if mapping_key in seen_mapping_keys:
                continue  # structural check already dedupes; belt-and-suspenders
            seen_mapping_keys.add(mapping_key)
            tech = catalog_by_key.get(mapping_key)
            if tech is None or tech.deprecated:
                apply_errors.append(
                    {
                        "line": meta["line"],
                        "column": "attack_techniques",
                        "reason": (
                            f"ATT&CK technique {am['domain']}/{am['technique_id']} "
                            + ("is deprecated" if tech is not None else "not found in catalog")
                            + " — mapping skipped (scenario imported)"
                        ),
                    }
                )
                continue
            db.add(
                ScenarioAttackMapping(
                    organization_id=org_id,
                    scenario_id=scenario.id,
                    technique_id=tech.id,
                    source="user",
                    rationale=am.get("rationale"),
                )
            )

    await _finalise_apply(
        db,
        preview_row=preview_row,
        org_id=org_id,
        user_id=user.id,
        ip_address=ip_address,
        imported=imported,
        skipped=skipped,
        errors=apply_errors,
    )
    return imported, skipped, apply_errors


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
    """Write one summary ``scenario.import`` audit row + delete the preview."""
    await AuditWriter(db).log(
        organization_id=org_id,
        entity_type="scenario",
        entity_id=preview_row.id,
        action="scenario.import",
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


def generate_template_csv() -> bytes:
    """Downloadable CSV template: header row + one example row.

    I6/SC-I7: NO ``#`` comment lines — ``parse_csv_flat`` is comment-unaware, so
    a commented template would NOT round-trip (the comments would be read as a
    bogus header). Field guidance lives in ``import.html``, not in the CSV.

    #27 Task 7: the CSV format cannot express ``lognormal_mixture`` — there is
    no column for a component list, only the flat ``{prefix}_dist``/``low``/
    ``mode``/``high`` quartet per node. The example row below stays PERT-only;
    mixture authoring requires the JSON format (``generate_sample_json``
    below covers PERT only too, but the JSON *shape* — unlike CSV — CAN carry
    a ``{"distribution": "lognormal_mixture", "components": [...]}`` node;
    see ``tests/unit/test_scenario_import_validate.py``'s mixture tests for
    the accepted shape).
    """
    from idraa.services.scenario_import_parsers import CSV_HEADERS

    # I-1: the example row must align with the 28-column CSV_HEADERS — the
    # per-node ``tef_dist`` / ``pl_dist`` / ``sl_dist`` columns (Epic B) sit
    # immediately before each node's low/mode/high triplet; ``effect`` (Task 3)
    # sits after ``asset_class``; ``entry_currency``/``entry_rate`` (P2) are last.
    # The example carries an explicit ``PERT`` cell in each dist position.
    lines = [
        ",".join(CSV_HEADERS),
        "Phishing to AD ransomware,Email-borne ransomware via AD,custom,ransomware,"
        "cybercriminals,phishing_then_lateral_movement,systems,availability,1.0,active,PERT,"
        "PERT,0.1,0.5,2,0.2,0.35,0.6,PERT,100000,1000000,15000000,PERT,50000,500000,5000000",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def generate_sample_json() -> bytes:
    """Downloadable JSON sample (array of one scenario object)."""
    import json

    sample = [
        {
            "name": "Phishing to AD ransomware",
            "description": "Email-borne ransomware via AD",
            "scenario_type": "custom",
            "threat_category": "ransomware",
            "threat_actor_type": "cybercriminals",
            "attack_vector": "phishing_then_lateral_movement",
            "asset_class": "systems",
            "effect": "availability",
            "version": "1.0",
            "status": "active",
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
            # Issue #475 T12: optional ATT&CK technique mappings, natural keys.
            # Unknown/deprecated technique_ids are skipped with a row error at
            # apply time (the scenario itself still imports).
            "attack_techniques": [
                {
                    "domain": "enterprise",
                    "technique_id": "T1566",
                    "rationale": "Initial access via phishing email.",
                },
            ],
        }
    ]
    return (json.dumps(sample, indent=2) + "\n").encode("utf-8")
