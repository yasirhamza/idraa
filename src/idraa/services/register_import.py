"""Register-import flow service (epic #34 P1c Task 3).

Owns the staged-token wizard state behind the register-import UI (Task 4-6
routes): upload → sheet-pick (xlsx multi-sheet only) → column-map →
value-bind → preview → convert, plus named per-org binding profiles. Staging
reuses the #306 ``CSVImportPreview`` model (Task 2 added ``state_json`` to
it) — one row per pending upload, keyed by its own UUID PK as the opaque
token, 10-minute TTL, org-scoped.

``state_json`` accumulates step choices across the full-page-redirect wizard:
``{"filename": str, "sheet_name": str | None, "column_map": {header:
target}, "value_bindings": {"likelihood": {value: label}, "impact": {value:
label}, "category": {value: ThreatCategory value | "__parked__"}},
"applied_profile_id": str | None}``.

**Write rule (Arch-I1, BINDING — mirrors ``wizard_state.py:248`` and the
``CSVImportPreview`` module docstring):** ``state_json`` is a plain ``JSON``
column; SQLAlchemy does not track in-place mutation of a plain ``dict``.
Every setter in this module REASSIGNS the whole dict —
``preview.state_json = {**(preview.state_json or {}), key: value}`` — never
``preview.state_json[key] = value``.

Every step method resolves the staging row via :meth:`get_staged` (Sec-N):
no method takes a ``CSVImportPreview`` row directly, so org-scoping, TTL,
and the ``entity_type`` gate are enforced uniformly at one call site.

Actual FAIR-CAM conversion happens ONLY via
:class:`~idraa.services.qualitative_converter.QualitativeConverterService`
— this module's job stops at producing a validated ``list[BoundRow]``.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §5.
Plan: docs/superpowers/plans/2026-07-18-import-ui-p1c.md Task 3
(+ the BINDING Task 3 plan-gate amendments).
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import NotFoundError, ValidationError
from idraa.models.csv_import_preview import PREVIEW_TTL_SECONDS as PREVIEW_TTL_SECONDS
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.enums import ThreatCategory
from idraa.models.register_binding_profile import RegisterBindingProfile
from idraa.models.user import User
from idraa.services.audit import AuditWriter
from idraa.services.qualitative_bands import QualitativeBandService
from idraa.services.qualitative_converter import (
    BoundRow,
    ClassifiedRows,
    ConversionReport,
    QualitativeConverterService,
)
from idraa.services.register_import_parsers import (
    ParsedRegister,
    list_sheet_names,
    parse_register,
    sniff_register_format,
)

ENTITY_TYPE = "register"

# Sec-I4: bound `name` (profile) — non-empty after strip, at most 100 chars
# (matches RegisterBindingProfile.name's String(100) column).
_MAX_PROFILE_NAME_LEN = 100

# Sec-N (Task 3 base plan): bound `source_file` — over this length is
# rejected at stage_upload, before the row is ever written.
_MAX_FILENAME_LEN = 255

# distinct_values() bound (base plan Task 3 interface): more than this many
# distinct values in a bound column almost certainly means the column map is
# wrong (a free-text field bound as "likelihood", say).
_MAX_DISTINCT_VALUES = 50

_PARKED_CATEGORY = "__parked__"

# The 8 column-map targets a file header may be assigned to.
TARGETS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "likelihood",
        "impact",
        "category",
        "owner",
        "carry_along",
        "ignore",
    }
)
# title/likelihood/impact: exactly one header must map to each (base plan
# Task 3 interface — required for any row to be scoreable at all).
_REQUIRED_EXACTLY_ONE: frozenset[str] = frozenset({"title", "likelihood", "impact"})
# description/category/owner: single-valued, but optional — mapping more
# than one header to any of these would make BoundRow's single field
# ambiguous (which header wins?), so it is rejected rather than silently
# picking one.
_SINGLE_VALUED: frozenset[str] = _REQUIRED_EXACTLY_ONE | frozenset(
    {"description", "category", "owner"}
)


class PreviewExpiredError(NotFoundError):
    """Uniform error for a register-import token that won't apply.

    Mirrors ``services/scenario_import.py``'s ``PreviewExpiredError`` — a
    single class avoids an existence oracle across "missing", "expired",
    "wrong org", and "not a register-import token" (a scenario-import token
    reaching this service). Route layer renders 409.
    """


@dataclass(frozen=True)
class StagedRegister:
    """Result of :meth:`RegisterImportService.stage_upload`."""

    token: str
    fmt: str
    sheet_names: list[str] | None


def _fmt_from_entity_type(entity_type: str) -> str:
    """Recover the stored format from ``"register:<fmt>"``; default ``"csv"``."""
    _, _, fmt = entity_type.partition(":")
    return fmt if fmt in ("xlsx", "csv") else "csv"


def _category_targets() -> set[str]:
    return {c.value for c in ThreatCategory} | {_PARKED_CATEGORY}


def _drift_warnings(snapshot: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Diff a profile's frozen ``mapping_versions_snapshot`` against the
    CURRENT ``mapping_versions()`` output, both in the ``{"canonical": {...},
    "org": {...}}`` per-(kind,label) shape. Checked in BOTH directions:

    - a key present at save time that is now missing or at a different
      version is drift (an existing band was re-derived or removed);
    - a key present now but ABSENT from the snapshot is ALSO drift — most
      notably a NEW per-org override created after the profile was saved,
      which silently shadows a (kind, label) the profile's bindings assumed
      resolved straight to canonical.
    """
    warnings: list[str] = []
    for layer in ("canonical", "org"):
        snap_layer = snapshot.get(layer)
        cur_layer = current.get(layer)
        if not isinstance(snap_layer, dict) or not isinstance(cur_layer, dict):
            # Legacy/malformed snapshot shape (e.g. a profile saved before
            # the Meth-N3 rewire, when "canonical" was a bare int) — flag it
            # rather than silently skip the drift check.
            warnings.append(f"{layer} mapping-version format changed since this profile was saved")
            continue
        for key, snap_version in snap_layer.items():
            cur_version = cur_layer.get(key)
            if cur_version is None:
                warnings.append(
                    f"{key} ({layer}) mapping band no longer exists (was version {snap_version})"
                )
            elif cur_version != snap_version:
                warnings.append(
                    f"{key} ({layer}) mapping band changed since this profile was saved "
                    f"(v{snap_version} -> v{cur_version})"
                )
        for key, cur_version in cur_layer.items():
            if key not in snap_layer:
                warnings.append(
                    f"{key} ({layer}) is new since this profile was saved "
                    f"(now version {cur_version})"
                )
    return warnings


def _header_by_single_target(column_map: dict[str, str]) -> dict[str, str]:
    """Invert ``column_map`` for the single-valued targets only (title,
    description, likelihood, impact, category, owner) — ``carry_along`` and
    ``ignore`` are intentionally excluded (multi-valued / discarded)."""
    result: dict[str, str] = {}
    for header, target in column_map.items():
        if target in _SINGLE_VALUED:
            result[target] = header
    return result


def preselect_bindings(
    distinct: dict[str, list[str]],
    effective_bands: dict[tuple[str, str], Any],
    categories: type[ThreatCategory] = ThreatCategory,
) -> dict[str, dict[str, str]]:
    """Pre-select value bindings on EXACT case-insensitive label match ONLY
    (spec §5 / Global Constraints — zero heuristics, zero fuzzy matching).

    Pure function — no I/O. ``effective_bands`` is the
    ``QualitativeBandService.effective_bands()`` return shape
    (``{(kind, label): EffectiveBand}``); only the ``(kind, label)`` keys are
    used here. A file value with no exact match is simply absent from the
    returned dict (left for the admin to bind manually) — never guessed.
    """
    freq_by_ci = {label.lower(): label for (kind, label) in effective_bands if kind == "frequency"}
    mag_by_ci = {label.lower(): label for (kind, label) in effective_bands if kind == "magnitude"}
    cat_by_ci = {c.value.lower(): c.value for c in categories}

    result: dict[str, dict[str, str]] = {"likelihood": {}, "impact": {}, "category": {}}
    for value in distinct.get("likelihood", []):
        match = freq_by_ci.get(value.strip().lower())
        if match is not None:
            result["likelihood"][value] = match
    for value in distinct.get("impact", []):
        match = mag_by_ci.get(value.strip().lower())
        if match is not None:
            result["impact"][value] = match
    for value in distinct.get("category", []):
        match = cat_by_ci.get(value.strip().lower())
        if match is not None:
            result["category"][value] = match
    return result


class RegisterImportService:
    """Staged-token register-import flow: upload → map → bind → convert."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Staging + org-scoped resolution
    # ------------------------------------------------------------------

    async def stage_upload(
        self,
        *,
        organization_id: uuid.UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
        user: User,
    ) -> StagedRegister:
        """Sniff format + stage the raw bytes under a 10-min token.

        Size cap is belt-and-suspenders with the route's ``Content-Length``
        pre-check (Task 4 amendment Sec-I3) — this post-read ``len(data)``
        check is the one that actually holds for a chunked/streamed upload
        with no (or a lying) ``Content-Length`` header.
        """
        # Arch3-N1: function-level import — services→routes edge, deferred
        # to keep the module graph acyclic-but-explicit (mirrors the
        # precedent at services/scenario_library.py:181).
        from idraa.routes.deps import MAX_UPLOAD_BYTES

        if len(data) > MAX_UPLOAD_BYTES:
            raise ValidationError("upload exceeds the maximum allowed size")

        clean_filename = (filename or "").strip()
        if not clean_filename:
            raise ValidationError("filename is required")
        if len(clean_filename) > _MAX_FILENAME_LEN:
            raise ValidationError(f"filename exceeds {_MAX_FILENAME_LEN} characters")

        try:
            fmt = sniff_register_format(
                filename=clean_filename, content_type=content_type, data=data
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        sheet_names: list[str] | None = None
        if fmt == "xlsx":
            try:
                sheet_names = list_sheet_names(data)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc

        expires_at = datetime.now(UTC) + timedelta(seconds=PREVIEW_TTL_SECONDS)
        row = CSVImportPreview(
            organization_id=organization_id,
            created_by_user_id=user.id,
            entity_type=f"{ENTITY_TYPE}:{fmt}",
            csv_bytes=data,
            expires_at=expires_at,
            state_json={"filename": clean_filename},
        )
        self._db.add(row)
        await self._db.flush()
        return StagedRegister(token=str(row.id), fmt=fmt, sheet_names=sheet_names)

    async def get_staged(self, *, organization_id: uuid.UUID, token: str) -> CSVImportPreview:
        """Resolve ``token`` -> row, enforcing org-scope + TTL + entity-type.

        Raises :class:`PreviewExpiredError` uniformly for malformed/missing/
        expired/wrong-org/wrong-flow tokens (no existence oracle) — a
        scenario-import token (``entity_type="scenario:..."``) is REJECTED
        here just as a register-import token would be rejected by
        ``services/scenario_import.py`` (Sec-N).
        """
        try:
            token_uuid = uuid.UUID(token)
        except (TypeError, ValueError) as exc:
            raise PreviewExpiredError("preview token is malformed; please re-upload") from exc

        row = (
            await self._db.execute(
                select(CSVImportPreview).where(CSVImportPreview.id == token_uuid)
            )
        ).scalar_one_or_none()
        if row is None or row.organization_id != organization_id:
            raise PreviewExpiredError("preview not found; please re-upload")
        if not row.entity_type.startswith(f"{ENTITY_TYPE}:"):
            raise PreviewExpiredError("preview not found; please re-upload")

        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            await self._db.delete(row)
            await self._db.flush()
            raise PreviewExpiredError("preview expired; please re-upload")
        return row

    def _parse_staged(self, preview: CSVImportPreview, state: dict[str, Any]) -> ParsedRegister:
        fmt = _fmt_from_entity_type(preview.entity_type)
        sheet_name = state.get("sheet_name")
        try:
            return parse_register(preview.csv_bytes, fmt, sheet_name)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def _band_labels(self, organization_id: uuid.UUID) -> tuple[set[str], set[str]]:
        effective = await QualitativeBandService(self._db).effective_bands(organization_id)
        freq = {label for (kind, label) in effective if kind == "frequency"}
        mag = {label for (kind, label) in effective if kind == "magnitude"}
        return freq, mag

    async def get_headers(self, *, organization_id: uuid.UUID, token: str) -> list[str]:
        """Parsed file headers for the token's current sheet selection.

        Task 4 addition: the column-map GET route needs the raw headers to
        render one target ``<select>`` per header BEFORE ``column_map``
        exists in ``state_json`` — every other read in this module assumes
        ``column_map`` is already set. Resolves via :meth:`get_staged` like
        every other step (org+TTL+entity-type enforced uniformly).
        """
        preview = await self.get_staged(organization_id=organization_id, token=token)
        state = preview.state_json or {}
        parsed = self._parse_staged(preview, state)
        return parsed.headers

    # ------------------------------------------------------------------
    # Step setters — each reassigns the WHOLE state_json dict (Arch-I1)
    # ------------------------------------------------------------------

    async def set_sheet(self, *, organization_id: uuid.UUID, token: str, sheet_name: str) -> None:
        preview = await self.get_staged(organization_id=organization_id, token=token)
        fmt = _fmt_from_entity_type(preview.entity_type)
        if fmt != "xlsx":
            raise ValidationError("sheet selection only applies to xlsx uploads")
        try:
            sheet_names = list_sheet_names(preview.csv_bytes)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if sheet_name not in sheet_names:
            raise ValidationError(f"unknown sheet {sheet_name!r}; available: {sheet_names}")

        preview.state_json = {**(preview.state_json or {}), "sheet_name": sheet_name}
        await self._db.flush()

    async def set_column_map(
        self, *, organization_id: uuid.UUID, token: str, column_map: dict[str, str]
    ) -> None:
        """Validate + persist the file-header -> target map.

        - Every target must be one of :data:`TARGETS`.
        - ``title``/``likelihood``/``impact`` must each be mapped from
          EXACTLY one header (base plan Task 3 interface).
        - ``description``/``category``/``owner`` may be mapped from AT MOST
          one header each (single-valued fields on ``BoundRow`` — mapping
          two headers to the same target would be ambiguous).
        - A header that is blank OR duplicated in the parsed file cannot be
          mapped to any non-``"ignore"`` target (T1 review NTH): a blank
          header string is meaningless as provenance, and a duplicated
          header name silently collapses in ``dict(zip(headers, row))`` at
          parse time, so binding it would read whichever column happened to
          win the collision.
        """
        preview = await self.get_staged(organization_id=organization_id, token=token)
        state = preview.state_json or {}
        parsed = self._parse_staged(preview, state)
        header_counts = Counter(parsed.headers)

        counts: dict[str, int] = {}
        for header, target in column_map.items():
            if target not in TARGETS:
                raise ValidationError(f"unknown column target {target!r} for header {header!r}")
            counts[target] = counts.get(target, 0) + 1
            if target != "ignore" and (not header.strip() or header_counts[header] > 1):
                raise ValidationError(
                    f"header {header!r} is blank or duplicated in the file — "
                    f"cannot map it to {target!r}"
                )

        for target in _REQUIRED_EXACTLY_ONE:
            if counts.get(target, 0) != 1:
                raise ValidationError(f"exactly one column must map to {target!r}")
        for target in _SINGLE_VALUED - _REQUIRED_EXACTLY_ONE:
            if counts.get(target, 0) > 1:
                raise ValidationError(f"at most one column may map to {target!r}")

        preview.state_json = {**(preview.state_json or {}), "column_map": dict(column_map)}
        await self._db.flush()

    async def distinct_values(
        self, *, organization_id: uuid.UUID, token: str
    ) -> dict[str, list[str]]:
        """Distinct NON-EMPTY values per bound column (likelihood/impact/
        category), sorted. Raises :class:`ValidationError` if any column has
        more than :data:`_MAX_DISTINCT_VALUES` distinct values — almost
        certainly a wrong column mapping (e.g. a free-text field bound as
        "likelihood")."""
        preview = await self.get_staged(organization_id=organization_id, token=token)
        state = preview.state_json or {}
        column_map = state.get("column_map")
        if not column_map:
            raise ValidationError("column map has not been set for this upload yet")

        parsed = self._parse_staged(preview, state)
        header_by_target = _header_by_single_target(column_map)

        result: dict[str, list[str]] = {}
        for target in ("likelihood", "impact", "category"):
            header = header_by_target.get(target)
            if header is None:
                result[target] = []
                continue
            values = sorted({row.get(header, "").strip() for row in parsed.rows} - {""})
            if len(values) > _MAX_DISTINCT_VALUES:
                raise ValidationError(
                    f"column {header!r} has {len(values)} distinct values — is the mapping right?"
                )
            result[target] = values
        return result

    async def set_value_bindings(
        self,
        *,
        organization_id: uuid.UUID,
        token: str,
        bindings: dict[str, dict[str, str]],
    ) -> None:
        """Validate + persist value bindings for the three bound columns.

        Sec-I2 (BINDING): every target is validated server-side —
        ``likelihood`` keys must resolve to a CURRENT effective frequency-
        band label, ``impact`` to a CURRENT magnitude-band label, and
        ``category`` to a ``ThreatCategory`` member or ``"__parked__"``.
        Every distinct file value (per :meth:`distinct_values`) must be
        bound, or this raises — never a silent partial bind.
        """
        preview = await self.get_staged(organization_id=organization_id, token=token)
        freq_labels, mag_labels = await self._band_labels(organization_id)
        category_targets = _category_targets()

        likelihood = dict(bindings.get("likelihood") or {})
        impact = dict(bindings.get("impact") or {})
        category = dict(bindings.get("category") or {})

        self._validate_bindings_group(
            likelihood, valid_targets=freq_labels, group_name="likelihood"
        )
        self._validate_bindings_group(impact, valid_targets=mag_labels, group_name="impact")
        self._validate_bindings_group(
            category, valid_targets=category_targets, group_name="category"
        )

        distinct = await self.distinct_values(organization_id=organization_id, token=token)
        groups = {"likelihood": likelihood, "impact": impact, "category": category}
        for target, group in groups.items():
            for value in distinct.get(target, []):
                if value not in group:
                    raise ValidationError(f"{target} value {value!r} is not bound")

        preview.state_json = {
            **(preview.state_json or {}),
            "value_bindings": {"likelihood": likelihood, "impact": impact, "category": category},
        }
        await self._db.flush()

    @staticmethod
    def _validate_bindings_group(
        group: dict[str, str], *, valid_targets: set[str], group_name: str
    ) -> None:
        for file_value, target in group.items():
            if target not in valid_targets:
                raise ValidationError(
                    f"{group_name} binding {file_value!r} -> {target!r} is not a valid target"
                )

    # ------------------------------------------------------------------
    # Bound-row assembly + preview + apply
    # ------------------------------------------------------------------

    async def build_bound_rows(self, *, organization_id: uuid.UUID, token: str) -> list[BoundRow]:
        """Assemble ``list[BoundRow]`` from the staged bytes + accumulated
        state_json. Re-validates every likelihood/impact/category binding
        against the CURRENT effective bands / ThreatCategory members (Sec-I2)
        — this is the final gate before a row can reach the converter, so an
        invalid category (e.g. left over from a stale/drifted binding
        profile) can NEVER reach the converter's enum coercion, regardless
        of how ``state_json.value_bindings`` was populated.

        A row whose bound likelihood or impact cell is BLANK cannot be
        scored (no band to derive a PERT from) — rather than aborting the
        entire build over one blank cell, that row is routed to PARKED
        (``category=None``), same bucket as an explicitly-parked category
        (D5's existing park semantic, extended to "nothing to score").
        """
        preview = await self.get_staged(organization_id=organization_id, token=token)
        state = preview.state_json or {}
        column_map = state.get("column_map")
        if not column_map:
            raise ValidationError("column map has not been set for this upload yet")
        value_bindings = state.get("value_bindings")
        if not value_bindings:
            raise ValidationError("value bindings have not been set for this upload yet")

        parsed = self._parse_staged(preview, state)
        header_by_target = _header_by_single_target(column_map)

        title_header = header_by_target.get("title")
        likelihood_header = header_by_target.get("likelihood")
        impact_header = header_by_target.get("impact")
        if title_header is None or likelihood_header is None or impact_header is None:
            raise ValidationError(
                "column map is missing a required title/likelihood/impact binding"
            )
        description_header = header_by_target.get("description")
        owner_header = header_by_target.get("owner")
        category_header = header_by_target.get("category")
        carry_headers = sorted(h for h, t in column_map.items() if t == "carry_along")

        freq_labels, mag_labels = await self._band_labels(organization_id)
        category_targets = _category_targets()

        likelihood_bindings: dict[str, str] = value_bindings.get("likelihood") or {}
        impact_bindings: dict[str, str] = value_bindings.get("impact") or {}
        category_bindings: dict[str, str] = value_bindings.get("category") or {}

        bound_rows: list[BoundRow] = []
        for r in parsed.rows:
            source_row = int(r["_row"])
            raw_likelihood = r.get(likelihood_header, "").strip()
            raw_impact = r.get(impact_header, "").strip()
            raw_category = r.get(category_header, "").strip() if category_header else ""

            if not raw_likelihood or not raw_impact:
                # Nothing to score this row against — park it rather than
                # hard-failing the whole batch over one blank cell.
                bound_rows.append(
                    BoundRow(
                        source_row=source_row,
                        title=r.get(title_header, "").strip(),
                        description=self._optional_cell(r, description_header),
                        owner=self._optional_cell(r, owner_header),
                        likelihood_label="",
                        magnitude_label="",
                        category=None,
                        raw={
                            "likelihood": raw_likelihood,
                            "impact": raw_impact,
                            "category": raw_category,
                        },
                        carry_along=self._carry_along(r, carry_headers),
                    )
                )
                continue

            likelihood_label = likelihood_bindings.get(raw_likelihood)
            if likelihood_label is None or likelihood_label not in freq_labels:
                raise ValidationError(
                    f"row {source_row}: likelihood value {raw_likelihood!r} is not bound "
                    "to a valid frequency band"
                )
            magnitude_label = impact_bindings.get(raw_impact)
            if magnitude_label is None or magnitude_label not in mag_labels:
                raise ValidationError(
                    f"row {source_row}: impact value {raw_impact!r} is not bound "
                    "to a valid magnitude band"
                )

            category: ThreatCategory | None
            if category_header is None or not raw_category:
                category = None
            else:
                category_target = category_bindings.get(raw_category)
                if category_target is None or category_target not in category_targets:
                    raise ValidationError(
                        f"row {source_row}: category value {raw_category!r} is not bound "
                        "to a valid target"
                    )
                category = (
                    None if category_target == _PARKED_CATEGORY else ThreatCategory(category_target)
                )

            bound_rows.append(
                BoundRow(
                    source_row=source_row,
                    title=r.get(title_header, "").strip(),
                    description=self._optional_cell(r, description_header),
                    owner=self._optional_cell(r, owner_header),
                    likelihood_label=likelihood_label,
                    magnitude_label=magnitude_label,
                    category=category,
                    raw={
                        "likelihood": raw_likelihood,
                        "impact": raw_impact,
                        "category": raw_category,
                    },
                    carry_along=self._carry_along(r, carry_headers),
                )
            )
        return bound_rows

    @staticmethod
    def _optional_cell(row: dict[str, str], header: str | None) -> str | None:
        if header is None:
            return None
        return row.get(header, "").strip() or None

    @staticmethod
    def _carry_along(row: dict[str, str], carry_headers: list[str]) -> dict[str, str]:
        return {h: row[h] for h in carry_headers if row.get(h, "").strip()}

    async def preview(self, *, organization_id: uuid.UUID, token: str) -> ClassifiedRows:
        """``build_bound_rows`` + a dry ``classify_rows`` pass (Task 3
        amendment — replaces inlining both calls at the route layer)."""
        preview_row = await self.get_staged(organization_id=organization_id, token=token)
        filename = (preview_row.state_json or {}).get("filename") or "register"
        rows = await self.build_bound_rows(organization_id=organization_id, token=token)
        return await QualitativeConverterService(self._db).classify_rows(
            organization_id=organization_id, source_file=filename, rows=rows
        )

    async def apply(
        self,
        *,
        organization_id: uuid.UUID,
        user: User,
        token: str,
        ip_address: str | None = None,
    ) -> ConversionReport:
        """Re-parse + rebuild + convert, then delete the staging row
        (single-use — a re-POST of ``convert`` on the same token 409s)."""
        preview_row = await self.get_staged(organization_id=organization_id, token=token)
        state = preview_row.state_json or {}
        filename = state.get("filename") or "register"
        profile_id_str = state.get("applied_profile_id")
        binding_profile_id = uuid.UUID(profile_id_str) if profile_id_str else None

        rows = await self.build_bound_rows(organization_id=organization_id, token=token)
        report = await QualitativeConverterService(self._db).convert(
            organization_id=organization_id,
            user=user,
            source_file=filename,
            rows=rows,
            ip_address=ip_address,
            binding_profile_id=binding_profile_id,
        )

        await self._db.delete(preview_row)
        await self._db.flush()
        return report

    # ------------------------------------------------------------------
    # Binding profiles
    # ------------------------------------------------------------------

    async def save_profile(
        self,
        *,
        organization_id: uuid.UUID,
        name: str,
        token: str,
        user: User,
    ) -> RegisterBindingProfile:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValidationError("profile name is required")
        if len(clean_name) > _MAX_PROFILE_NAME_LEN:
            raise ValidationError(
                f"profile name must be at most {_MAX_PROFILE_NAME_LEN} characters"
            )

        preview = await self.get_staged(organization_id=organization_id, token=token)
        state = preview.state_json or {}
        column_map = state.get("column_map")
        value_bindings = state.get("value_bindings")
        if not column_map or not value_bindings:
            raise ValidationError(
                "cannot save a profile before the column map and value bindings are set"
            )

        existing = (
            await self._db.execute(
                select(RegisterBindingProfile).where(
                    RegisterBindingProfile.organization_id == organization_id,
                    RegisterBindingProfile.name == clean_name,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValidationError(
                f"a profile named {clean_name!r} already exists for this organization"
            )

        mapping_versions = await QualitativeBandService(self._db).mapping_versions(organization_id)

        profile = RegisterBindingProfile(
            organization_id=organization_id,
            name=clean_name,
            column_map=dict(column_map),
            value_bindings=dict(value_bindings),
            mapping_versions_snapshot=mapping_versions,
            created_by=user.id,
        )
        self._db.add(profile)
        await self._db.flush()

        await AuditWriter(self._db).log(
            organization_id=organization_id,
            entity_type="register_binding_profile",
            entity_id=profile.id,
            action="register_binding_profile.create",
            changes={"name": [None, clean_name]},
            user_id=user.id,
        )
        return profile

    async def list_profiles(self, organization_id: uuid.UUID) -> list[RegisterBindingProfile]:
        rows = (
            (
                await self._db.execute(
                    select(RegisterBindingProfile)
                    .where(RegisterBindingProfile.organization_id == organization_id)
                    .order_by(RegisterBindingProfile.name)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def _get_profile(
        self, *, organization_id: uuid.UUID, profile_id: uuid.UUID
    ) -> RegisterBindingProfile:
        profile = (
            await self._db.execute(
                select(RegisterBindingProfile).where(
                    RegisterBindingProfile.id == profile_id,
                    RegisterBindingProfile.organization_id == organization_id,
                )
            )
        ).scalar_one_or_none()
        if profile is None:
            raise NotFoundError(f"binding profile {profile_id} not found")
        return profile

    async def apply_profile(
        self,
        *,
        organization_id: uuid.UUID,
        token: str,
        profile_id: uuid.UUID,
    ) -> list[str]:
        """Pre-fill ``column_map``/``value_bindings`` from a saved profile.

        Returns drift warnings (profile's frozen ``mapping_versions_snapshot``
        vs the CURRENT ``mapping_versions()``). Any binding whose target is
        no longer valid against the CURRENT effective bands / ThreatCategory
        members is left OUT of the pre-filled bindings (unbound, for the
        admin to re-bind on the /bind page) rather than carried forward
        invalid — this is in addition to (not instead of)
        :meth:`set_value_bindings`'s own validation, which the /bind route
        still runs when the admin submits the (possibly-edited) pre-fill.
        """
        preview = await self.get_staged(organization_id=organization_id, token=token)
        profile = await self._get_profile(organization_id=organization_id, profile_id=profile_id)

        band_service = QualitativeBandService(self._db)
        current_versions = await band_service.mapping_versions(organization_id)
        warnings = _drift_warnings(profile.mapping_versions_snapshot or {}, current_versions)

        freq_labels, mag_labels = await self._band_labels(organization_id)
        category_targets = _category_targets()

        raw_bindings = profile.value_bindings or {}
        validated: dict[str, dict[str, str]] = {"likelihood": {}, "impact": {}, "category": {}}
        for file_value, label in (raw_bindings.get("likelihood") or {}).items():
            if label in freq_labels:
                validated["likelihood"][file_value] = label
            else:
                warnings.append(
                    f"likelihood binding {file_value!r} -> {label!r} is no longer valid; "
                    "left unbound"
                )
        for file_value, label in (raw_bindings.get("impact") or {}).items():
            if label in mag_labels:
                validated["impact"][file_value] = label
            else:
                warnings.append(
                    f"impact binding {file_value!r} -> {label!r} is no longer valid; left unbound"
                )
        for file_value, cat in (raw_bindings.get("category") or {}).items():
            if cat in category_targets:
                validated["category"][file_value] = cat
            else:
                warnings.append(
                    f"category binding {file_value!r} -> {cat!r} is no longer valid; left unbound"
                )

        preview.state_json = {
            **(preview.state_json or {}),
            "column_map": dict(profile.column_map or {}),
            "value_bindings": validated,
            "applied_profile_id": str(profile.id),
        }
        await self._db.flush()
        return warnings
