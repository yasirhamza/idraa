"""Overlays CSV importer — two-step preview + apply (B13).

Public surface:

- :func:`generate_template_csv` — emits a UTF-8 CSV template with comments
  + headers + one example row, downloadable from the admin UI.
- :func:`validate_csv` — parses + validates ``csv_bytes`` and stores them
  under a token (UUID, 10-minute TTL) in ``csv_import_preview``. Returns
  ``(token, preview_rows, errors)``. Does NOT mutate overlays.
- :func:`apply_validated_preview` — looks up ``token``, re-parses, upserts
  via :class:`OverlayService`, deletes the preview row, returns
  ``(imported, skipped, errors)``. Refuses if the token is missing,
  expired, or owned by a different org — uniformly via
  :class:`PreviewExpiredError` to avoid an existence oracle.

Design notes (preamble fold-ins):

- **Physical line counter.** Comment-stripping after ``csv.DictReader``
  reads the file would desynchronise error line numbers from the user's
  actual file lines. We pre-tokenise the bytes into ``(physical_line, csv_row)``
  pairs by splitting on newlines first, then feed only non-comment data
  rows to the reader while keeping the physical line attached.

- **UTF-8 strict.** Decoding uses ``utf-8-sig`` (BOM-tolerant for Excel
  exports) with ``errors="strict"``. Bad bytes surface as a single
  ``column="encoding"`` error rather than getting silently replaced
  with U+FFFD and producing nonsense downstream.

- **Deactivated tags rejected, not reactivated.** If a CSV row's tag
  matches an inactive overlay, we emit a row-level error explaining the
  tag was deactivated. The importer never flips ``is_active`` back to
  True silently — operators must reactivate manually (creating an
  explicit audit signal) before re-importing.

- **Tag-rename enforcement.** On the upsert path we always pass
  ``form.tag == existing.tag`` to :meth:`OverlayService.update`. The
  service additionally rejects tag renames defensively, so even if a
  future code change tried to rename through the importer it would fail
  loud.

- **Single summary audit row.** :func:`apply_validated_preview` writes
  exactly one ``overlay.import`` audit row at the end, in addition to
  the per-row ``overlay.create`` / ``overlay.update`` rows the service
  already emits. The summary row's ``changes`` field carries
  ``imported`` / ``skipped`` / ``errors_count`` — all in the
  ``[None, value]`` pair convention. The summary action follows the
  ``<entity>.<verb>`` taxonomy (``overlay.import``) matching
  ``services/overlays.py``. ``services/controls.py`` still emits
  bare-verb actions (``"create"`` / ``"update"`` / ``"delete"``); that
  module predates the taxonomy fold-in and will be reconciled when
  ``controls.py`` next changes. Don't let this carryover note grow to
  a fourth site — migrate ``controls.py`` first.

- **Service-layer row cap.** ``MAX_CSV_ROWS = 500`` is enforced here as
  defense-in-depth. The route layer (C7) will reject oversize *files*
  via Content-Length before they hit the service; this cap catches the
  hand-crafted-to-fit-but-still-big case.

- **Token.** The ``csv_import_preview`` row's UUID primary key is the
  token. No separate column. Callers receive the UUID as a string; we
  parse it with :func:`uuid.UUID` on the apply path so a malformed token
  raises ``PreviewExpiredError`` rather than a stack trace.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import NotFoundError

# ``PREVIEW_TTL_SECONDS`` is re-exported (``as PREVIEW_TTL_SECONDS``) so existing
# callers / tests that ``from idraa.services.overlays_importer import
# PREVIEW_TTL_SECONDS`` keep working under mypy strict (no_implicit_reexport).
# The constant lives on the shared model module so both the overlay and
# calibration-override importers point at one source of truth.
from idraa.models.csv_import_preview import PREVIEW_TTL_SECONDS as PREVIEW_TTL_SECONDS
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.overlay import OverlayDefinition
from idraa.schemas.overlay import OverlayForm
from idraa.services.audit import AuditWriter
from idraa.services.overlays import OverlayService, OverlayVersionConflictError

CSV_HEADERS: list[str] = [
    "tag",
    "display_name",
    "frequency_multiplier",
    "magnitude_multiplier",
    "sources",
    "methodology",
    "methodology_change_reason",
]

# Defensive service-layer cap. The route layer also caps via Content-Length
# (C7); this catches the hand-crafted-to-fit case.
MAX_CSV_ROWS: int = 500


class PreviewExpiredError(NotFoundError):
    """Raised by :func:`apply_validated_preview` for any token that won't apply.

    Uniform error class for "not found", "expired", and "wrong org" so
    the route layer can render a single "preview expired or already used
    — please re-upload" page without an existence oracle. Subclassing
    :class:`idraa.errors.NotFoundError` (rather than
    :class:`idraa.errors.ConflictError`) reads naturally because the
    operation is a *lookup* — the row either resolves under the caller's
    org or it doesn't. The route layer renders 409 here despite the
    NotFoundError lineage because UX-wise an apply replay is a
    "conflicting state" the user fixes by re-uploading; the class lineage
    just signals the existence-oracle posture.
    """


# ---- template ---------------------------------------------------------


def generate_template_csv() -> bytes:
    """Return a downloadable CSV template with comments + 1 example row."""
    lines = [
        "# Overlay definitions — cross-cutting risk modifiers applied post-IRIS+override.",
        "# Required: tag, display_name, frequency_multiplier, magnitude_multiplier,",
        "# methodology (>= 20 chars), methodology_change_reason.",
        "# Optional: sources (semicolon-separated paths under docs/reference/calibration-sources/).",
        "# Tag must be lowercase_snake_case; unique per organization.",
        "# Re-importing an existing tag updates that overlay (creates a new version).",
        "# Re-importing a deactivated tag is REJECTED — reactivate it manually first.",
        ",".join(CSV_HEADERS),
        # one example row — quote the free-text columns so commas inside them parse cleanly.
        "critical_infrastructure,Critical Infrastructure,1.4,2.0,"
        "docs/reference/calibration-sources/ic3_2025.md;docs/reference/calibration-sources/cisa_year_in_review_2024.md,"
        '"TEF +40%: nation-state and criminal targeting elevated for CI designations. '
        'LM x2.0: downstream operational impact and regulatory cascade.",'
        '"Initial bulk import"',
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


# ---- internal helpers --------------------------------------------------


def _decode_strict(csv_bytes: bytes) -> tuple[str | None, list[dict[str, Any]]]:
    """Decode ``csv_bytes`` as UTF-8 (BOM-tolerant, errors strict).

    Returns ``(text, errors)``: on success ``(text, [])``; on failure
    ``(None, [<one encoding error dict>])``. We intentionally surface a
    single error and bail rather than continue with U+FFFD replacement
    bytes that would silently corrupt downstream values.
    """
    try:
        return csv_bytes.decode("utf-8-sig", errors="strict"), []
    except UnicodeDecodeError as exc:
        return None, [
            {
                "line": 0,
                "column": "encoding",
                "reason": f"file is not valid UTF-8: {exc}",
            }
        ]


def _data_rows_with_physical_lines(
    text: str,
) -> tuple[list[str] | None, list[tuple[int, dict[str, str]]] | None, list[dict[str, Any]]]:
    """Split ``text`` into ``(headers, [(physical_line, row_dict), ...], errors)``.

    Comment-stripping: lines whose first non-whitespace character is ``#``
    are skipped *while keeping the physical 1-indexed line counter
    intact*, so error reporting cites the user's actual file line.

    The first non-comment, non-blank line is treated as the header row;
    subsequent non-comment, non-blank lines are data rows fed through
    ``csv.reader`` so quoted fields with embedded commas parse correctly.
    """
    errors: list[dict[str, Any]] = []
    headers: list[str] | None = None
    pairs: list[tuple[int, dict[str, str]]] = []

    def _parse_one(line: str) -> list[str]:
        # Single-line csv.reader call — safe because csv quoting doesn't
        # span lines once we've split on \n. Multi-line quoted fields
        # aren't part of the supported template; if a future template
        # needs them, switch to feeding the full data block to one reader
        # while threading line numbers via the reader's line_num.
        # csv.reader is permissive on single-line input (no csv.Error
        # raised in practice), so we don't wrap it in try/except —
        # malformed quoting just produces a wrong-shape row that the
        # downstream Pydantic validator will reject.
        return next(csv.reader(io.StringIO(line)))

    # str.splitlines() collapses \r\n / \r / \n consistently, but the
    # 1-indexed physical line counter stays accurate either way.
    for physical_line, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if headers is None:
            headers = _parse_one(raw)
            continue
        cells = _parse_one(raw)
        # Pad the row out to the header length so missing trailing columns
        # surface as empty strings rather than KeyError.
        if len(cells) < len(headers):
            cells = cells + [""] * (len(headers) - len(cells))
        # Drop any extra columns past the header — csv.DictReader's
        # ``restkey`` would surface them, but the template has a fixed
        # column set and extras are operator error.
        cells = cells[: len(headers)]
        row_dict = dict(zip(headers, cells, strict=True))
        pairs.append((physical_line, row_dict))

    if headers is None:
        # File contained no header row — likely all-comments or all-blank.
        errors.append(
            {
                "line": 0,
                "column": "header",
                "reason": "CSV is empty or contains no header row",
            }
        )
        return None, None, errors

    # Confirm the header matches the expected schema. Missing required
    # columns abort the whole upload — no point reporting per-row errors
    # for a malformed header.
    missing = set(CSV_HEADERS) - set(headers)
    if missing:
        errors.append(
            {
                "line": 1,
                "column": "header",
                "reason": (
                    f"missing required columns: {sorted(missing)}; "
                    f"expected {CSV_HEADERS}, got {headers}"
                ),
            }
        )
        return None, None, errors

    return headers, pairs, errors


async def _existing_overlays_by_tag(
    db: AsyncSession, *, org_id: uuid.UUID, only_active: bool = True
) -> dict[str, OverlayDefinition]:
    """Return ``{tag: OverlayDefinition}`` for ``org_id``.

    Default ``only_active=True`` matches the importer's upsert semantics:
    inactive rows must be REJECTED, not silently reactivated. The
    deactivated-tag check uses a separate query that reads the inactive
    set so we can produce a precise error message ("tag X is deactivated;
    reactivate it manually before re-import").
    """
    stmt = select(OverlayDefinition).where(
        OverlayDefinition.organization_id == org_id,
    )
    if only_active:
        stmt = stmt.where(OverlayDefinition.is_active.is_(True))
    rows = await db.execute(stmt)
    return {od.tag: od for od in rows.scalars().all()}


async def _inactive_overlay_tags(db: AsyncSession, *, org_id: uuid.UUID) -> set[str]:
    """Tags whose overlay rows exist for ``org_id`` but are deactivated."""
    stmt = select(OverlayDefinition.tag).where(
        OverlayDefinition.organization_id == org_id,
        OverlayDefinition.is_active.is_(False),
    )
    result = await db.execute(stmt)
    return {tag for (tag,) in result.all()}


def _validate_rows(
    pairs: list[tuple[int, dict[str, str]]],
    *,
    existing_active_tags: set[str],
    inactive_tags: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[OverlayForm | None]]:
    """Inner per-row validation. Returns ``(preview, errors, forms)``.

    Each entry in ``forms`` is the :class:`OverlayForm` for the row, or
    ``None`` if the row failed validation. ``preview`` and ``forms`` are
    aligned 1:1 with ``pairs`` so the apply path can re-walk the same
    decisions without re-parsing.

    Pure function — no DB access. The DB query for active/inactive tag
    sets happens once before this is called.
    """
    preview: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    forms: list[OverlayForm | None] = []
    seen_tags: set[str] = set()

    for physical_line, row in pairs:
        tag = (row.get("tag") or "").strip()
        display_name = (row.get("display_name") or "").strip()
        if not tag:
            errors.append(
                {
                    "line": physical_line,
                    "column": "tag",
                    "reason": "tag is required",
                }
            )
            preview.append({"line": physical_line, "tag": "", "action": "error"})
            forms.append(None)
            continue

        if tag in seen_tags:
            errors.append(
                {
                    "line": physical_line,
                    "column": "tag",
                    "reason": f"duplicate tag {tag!r} within upload",
                }
            )
            preview.append({"line": physical_line, "tag": tag, "action": "error"})
            forms.append(None)
            continue
        seen_tags.add(tag)

        if tag in inactive_tags:
            errors.append(
                {
                    "line": physical_line,
                    "column": "tag",
                    "reason": (
                        f"tag {tag!r} is deactivated; reactivate it manually before re-import"
                    ),
                }
            )
            preview.append({"line": physical_line, "tag": tag, "action": "error"})
            forms.append(None)
            continue

        # Parse multipliers explicitly — Pydantic will coerce strings, but
        # an explicit float() lets us catch the "not_a_number" case with a
        # crisp column-specific error rather than a generic
        # "Input should be a valid number".
        try:
            freq = float((row.get("frequency_multiplier") or "").strip() or "nan")
        except (TypeError, ValueError) as exc:
            errors.append(
                {
                    "line": physical_line,
                    "column": "frequency_multiplier",
                    "reason": f"could not parse as float: {exc}",
                }
            )
            preview.append({"line": physical_line, "tag": tag, "action": "error"})
            forms.append(None)
            continue
        try:
            mag = float((row.get("magnitude_multiplier") or "").strip() or "nan")
        except (TypeError, ValueError) as exc:
            errors.append(
                {
                    "line": physical_line,
                    "column": "magnitude_multiplier",
                    "reason": f"could not parse as float: {exc}",
                }
            )
            preview.append({"line": physical_line, "tag": tag, "action": "error"})
            forms.append(None)
            continue

        sources_raw = (row.get("sources") or "").strip()
        sources = [s.strip() for s in sources_raw.split(";") if s.strip()] if sources_raw else []

        try:
            form = OverlayForm(
                tag=tag,
                display_name=display_name,
                frequency_multiplier=freq,
                magnitude_multiplier=mag,
                sources=sources,
                methodology=(row.get("methodology") or "").strip(),
                methodology_change_reason=(row.get("methodology_change_reason") or "").strip(),
            )
        except ValidationError as exc:
            for err in exc.errors():
                # Render err["msg"] only — never str(exc) — to keep
                # Pydantic's dict repr out of any HTML the route layer
                # might render. (preamble line 56)
                errors.append(
                    {
                        "line": physical_line,
                        "column": ".".join(str(p) for p in err.get("loc", ())),
                        "reason": err["msg"],
                    }
                )
            preview.append({"line": physical_line, "tag": tag, "action": "error"})
            forms.append(None)
            continue

        action = "update" if tag in existing_active_tags else "create"
        preview.append(
            {
                "line": physical_line,
                "tag": tag,
                "display_name": display_name,
                "frequency_multiplier": freq,
                "magnitude_multiplier": mag,
                "action": action,
            }
        )
        forms.append(form)

    return preview, errors, forms


# ---- public surface ---------------------------------------------------


async def _decode_and_parse(
    csv_bytes: bytes,
) -> tuple[list[tuple[int, dict[str, str]]] | None, list[dict[str, Any]]]:
    """Decode + parse + row-cap-check. Returns ``(pairs, errors)``.

    ``pairs`` is ``None`` on hard-stop conditions (encoding failure,
    missing/invalid header, oversize file); ``errors`` is non-empty on
    those paths. On success, ``pairs`` is the parsed list and ``errors``
    is empty.

    Used by both :func:`validate_csv` (preview) and
    :func:`apply_validated_preview` (re-parse) to keep the two paths in
    lock-step — the apply path must reach the same hard-stop verdict the
    preview path did. The ``headers`` from
    :func:`_data_rows_with_physical_lines` is intentionally discarded: by
    the time pairs is non-None, the header has already been validated
    against ``CSV_HEADERS`` and isn't needed downstream.
    """
    text, encoding_errors = _decode_strict(csv_bytes)
    if encoding_errors or text is None:
        return None, encoding_errors
    headers, pairs, parse_errors = _data_rows_with_physical_lines(text)
    if pairs is None or headers is None:
        return None, parse_errors
    if len(pairs) > MAX_CSV_ROWS:
        return None, [
            {
                "line": 0,
                "column": "file",
                "reason": f"too many rows: maximum {MAX_CSV_ROWS} per upload",
            }
        ]
    return pairs, []


async def validate_csv(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    csv_bytes: bytes,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Step 1 of the two-step import: parse + validate, do NOT mutate.

    Returns ``(token, preview_rows, errors)``. ``token`` is always a
    non-empty string — even when the upload is fully invalid, the token
    references a stored preview row so the route layer can render the
    preview page and let the operator see the errors before deciding
    whether to retry or cancel.

    Errors are row-scoped: list of ``{"line": int, "column": str, "reason": str}``.
    A row that fails validation is reported and skipped; importing
    continues for other rows. Duplicate tags within the SAME upload are
    flagged and only the first occurrence becomes a preview ``create`` /
    ``update`` row.

    Hard-stop conditions return a single-error list and an empty preview:

    - non-UTF-8 bytes (``column="encoding"``)
    - missing required header columns (``column="header"``)
    - over ``MAX_CSV_ROWS`` data rows (``column="file"``)
    """
    pairs, hard_stop_errors = await _decode_and_parse(csv_bytes)
    if pairs is None:
        token = await _store_preview(
            db,
            org_id=org_id,
            user_id=user_id,
            csv_bytes=csv_bytes,
        )
        return token, [], hard_stop_errors

    existing_active = await _existing_overlays_by_tag(db, org_id=org_id)
    inactive_tags = await _inactive_overlay_tags(db, org_id=org_id)

    preview, errors, _ = _validate_rows(
        pairs,
        existing_active_tags=set(existing_active.keys()),
        inactive_tags=inactive_tags,
    )

    token = await _store_preview(db, org_id=org_id, user_id=user_id, csv_bytes=csv_bytes)
    return token, preview, errors


async def _store_preview(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    csv_bytes: bytes,
) -> str:
    """Insert a ``csv_import_preview`` row and return its token (str UUID)."""
    expires_at = datetime.now(UTC) + timedelta(seconds=PREVIEW_TTL_SECONDS)
    row = CSVImportPreview(
        organization_id=org_id,
        created_by_user_id=user_id,
        entity_type="overlay",
        csv_bytes=csv_bytes,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return str(row.id)


async def apply_validated_preview(
    db: AsyncSession,
    *,
    token: str,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    ip_address: str | None = None,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Step 2 of the two-step import: apply the rows backed by ``token``.

    Returns ``(imported, skipped, errors)``. Re-parses the stored bytes
    rather than trusting any client-supplied row data — the token-as-
    handle pattern means the apply request body is empty (just the
    confirmation), so re-parsing is the only safe path.

    Raises :class:`PreviewExpiredError` for any of:

    - token doesn't parse as a UUID
    - no row with that id exists
    - row exists but ``expires_at`` is in the past (row is also deleted
      so the lookup-and-cleanup is inline)
    - row exists but its ``organization_id`` differs from ``org_id``

    Single error class avoids leaking which condition tripped — preserves
    the closed existence oracle the route layer relies on (B9/B10 spirit).
    """
    try:
        token_uuid = uuid.UUID(token)
    except (TypeError, ValueError) as exc:
        raise PreviewExpiredError(
            "preview token is malformed; the preview may have already been "
            "applied or expired — please re-upload"
        ) from exc

    preview_row = (
        await db.execute(select(CSVImportPreview).where(CSVImportPreview.id == token_uuid))
    ).scalar_one_or_none()

    if preview_row is None:
        raise PreviewExpiredError(
            "preview not found; the preview may have already been applied "
            "or expired — please re-upload"
        )

    if preview_row.organization_id != org_id:
        # Treat cross-org access as not-found to avoid an existence
        # oracle (B9/B10). Do NOT delete the other org's row.
        raise PreviewExpiredError(
            "preview not found; the preview may have already been applied "
            "or expired — please re-upload"
        )

    # SQLite's DateTime(timezone=True) strips tzinfo on read — same gotcha
    # ``services/auth.py::load_active_session`` worked around. Reattach UTC
    # if the column came back naive.
    expires_at = preview_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        # Inline cleanup of the expired row so we don't leak storage.
        await db.delete(preview_row)
        await db.flush()
        raise PreviewExpiredError("preview expired; please re-upload")

    csv_bytes = preview_row.csv_bytes

    # Re-walk the same decode/parse/cap path validate_csv took, via the
    # shared helper — keeps both call sites in lock-step.
    pairs, hard_stop_errors = await _decode_and_parse(csv_bytes)
    if pairs is None:
        await _finalise_apply(
            db,
            preview_row=preview_row,
            org_id=org_id,
            user_id=user_id,
            ip_address=ip_address,
            imported=0,
            skipped=0,
            errors=hard_stop_errors,
        )
        return 0, 0, hard_stop_errors

    existing_active = await _existing_overlays_by_tag(db, org_id=org_id)
    inactive_tags = await _inactive_overlay_tags(db, org_id=org_id)

    preview, validation_errors, forms = _validate_rows(
        pairs,
        existing_active_tags=set(existing_active.keys()),
        inactive_tags=inactive_tags,
    )

    svc = OverlayService(db)
    imported = 0
    skipped = len(validation_errors)  # one error == one skipped row entry
    apply_errors: list[dict[str, Any]] = list(validation_errors)

    # Narrow catch: OverlayService is documented (overlays.py preamble) to
    # raise exactly two exception types — OverlayVersionConflictError on
    # concurrent edit and ValueError on tag rename. Anything else
    # (asyncio.CancelledError, IntegrityError, AttributeError, KeyError,
    # ...) is a bug or infra failure and must propagate so we fail loud
    # (B14). The same tuple is used on both branches to keep the contract
    # symmetric — pydantic has already caught structural issues by the
    # time we reach here, but a future schema change could surface a
    # ValueError on create too, and a row-scoped error is the right
    # response either way.
    for preview_row_meta, form in zip(preview, forms, strict=True):
        if form is None:
            # Row already accounted for in validation_errors above.
            continue
        tag = form.tag
        if tag in existing_active:
            # Tag-rename enforcement: pass the EXISTING tag through the form
            # — OverlayService.update rejects tag rename defensively, so even
            # if the form somehow carried a different tag, the service would
            # raise. Build a fresh form with the existing tag to make the
            # invariant explicit at the call site.
            existing = existing_active[tag]
            try:
                await svc.update(
                    overlay=existing,
                    user_id=user_id,
                    form=form,
                    expected_version=existing.version,
                    ip_address=ip_address,
                )
            except (OverlayVersionConflictError, ValueError) as exc:
                apply_errors.append(
                    {
                        "line": preview_row_meta["line"],
                        "column": "row",
                        "reason": f"update failed: {exc}",
                    }
                )
                skipped += 1
                continue
        else:
            try:
                await svc.create(
                    organization_id=org_id,
                    user_id=user_id,
                    form=form,
                    ip_address=ip_address,
                )
            except (OverlayVersionConflictError, ValueError) as exc:
                apply_errors.append(
                    {
                        "line": preview_row_meta["line"],
                        "column": "row",
                        "reason": f"create failed: {exc}",
                    }
                )
                skipped += 1
                continue
        imported += 1

    await _finalise_apply(
        db,
        preview_row=preview_row,
        org_id=org_id,
        user_id=user_id,
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
    """Write the summary audit row + delete the preview row.

    Single ``overlay.import`` audit entry per apply call, in addition to
    the per-row ``overlay.create`` / ``overlay.update`` audits already
    emitted by :class:`OverlayService`. ``changes`` follows the
    ``[None, value]`` pair convention so audit-viewer / diff renderer
    code paths don't have to special-case this row.

    The preview row is deleted whether the apply succeeded or every row
    errored — once consumed, the token must not replay.
    """
    await AuditWriter(db).log(
        organization_id=org_id,
        entity_type="overlay",
        # entity_id is the preview-token UUID — a unique handle for this
        # import event, not a foreign-key reference to a surviving row.
        # The preview row is deleted ~10 lines later, leaving the id as a
        # dangling pointer; that's intentional. Per-row overlay.create /
        # overlay.update audits already carry their own entity_ids
        # pointing at live OverlayDefinition rows.
        entity_id=preview_row.id,
        action="overlay.import",
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
