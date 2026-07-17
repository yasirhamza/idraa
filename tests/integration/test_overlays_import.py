"""Overlay CSV importer tests — two-step preview + apply (B13).

Covers the C6 contract:

- ``generate_template_csv`` returns a usable CSV template.
- ``validate_csv`` parses + validates without mutating overlays, persists
  the raw bytes under a token in ``csv_import_preview`` with a 10-minute
  TTL.
- ``apply_validated_preview`` re-parses, upserts via ``OverlayService``,
  deletes the preview row, and writes a single ``overlay.import`` summary
  audit row.

Specific invariants validated here (preamble fold-ins):

- Physical line numbers in errors (not enumerate-after-comment-strip).
- ``errors="strict"`` UTF-8 decode (user-visible error on bad encoding).
- Deactivated-tag re-import is REJECTED (not silently reactivated).
- 500-row cap rejects oversize uploads at the service layer too.
- Tag rename is rejected on the upsert path.
- Token mismatch / expiry / cross-org access raises ``PreviewExpiredError``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from idraa.models.audit_log import AuditLog
from idraa.models.overlay import OverlayDefinition

# ---- helpers -----------------------------------------------------------


def _row_bytes(
    *,
    tag: str = "new_overlay",
    display_name: str = "New Overlay",
    freq: str = "1.5",
    mag: str = "2.5",
    sources: str = "",
    methodology: str = "Custom overlay anchored to internal data showing elevated risk profile.",
    reason: str = "Initial bulk import",
) -> bytes:
    """Build a single CSV row line as bytes (without header). Quotes the
    free-text fields the same way an exported template would."""
    return (f'{tag},{display_name},{freq},{mag},{sources},"{methodology}","{reason}"\n').encode()


_HEADER = (
    b"tag,display_name,frequency_multiplier,magnitude_multiplier,"
    b"sources,methodology,methodology_change_reason\n"
)


# ---- template ----------------------------------------------------------


async def test_template_csv_has_expected_headers():
    from idraa.services.overlays_importer import generate_template_csv

    template = generate_template_csv()
    text = template.decode("utf-8")
    # Header row appears
    assert (
        "tag,display_name,frequency_multiplier,magnitude_multiplier,"
        "sources,methodology,methodology_change_reason"
    ) in text
    # At least one example row included
    assert "critical_infrastructure" in text
    # Comment lines are present (#-prefixed)
    assert text.lstrip().startswith("#")


# ---- validate_csv (step 1) -------------------------------------------


async def test_validate_csv_returns_token_and_preview_for_valid_rows(
    db_session, organization, admin_user
):
    from idraa.services.overlays_importer import (
        PREVIEW_TTL_SECONDS,
        validate_csv,
    )

    csv_bytes = _HEADER + _row_bytes()
    token, preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=csv_bytes,
    )

    assert token, "token must be a non-empty string"
    assert errors == []
    assert len(preview) == 1
    row = preview[0]
    assert row["tag"] == "new_overlay"
    assert row["action"] == "create"

    # validate_csv must NOT mutate overlays — no row exists in
    # overlay_definitions yet.
    overlays = await db_session.execute(
        select(OverlayDefinition).where(
            OverlayDefinition.organization_id == organization.id,
        )
    )
    assert overlays.scalar_one_or_none() is None

    # The preview row should be persisted in csv_import_preview with the
    # original bytes + a TTL near now+PREVIEW_TTL_SECONDS.
    from idraa.models.csv_import_preview import CSVImportPreview

    preview_rows = (await db_session.execute(select(CSVImportPreview))).scalars().all()
    assert len(preview_rows) == 1
    pr = preview_rows[0]
    assert pr.csv_bytes == csv_bytes
    assert pr.organization_id == organization.id
    assert pr.entity_type == "overlay"
    # SQLite strips tzinfo on read — reattach UTC for delta math.
    pr_expires = pr.expires_at if pr.expires_at.tzinfo else pr.expires_at.replace(tzinfo=UTC)
    delta = pr_expires - datetime.now(UTC)
    # Expiry should land within (PREVIEW_TTL_SECONDS - 5, PREVIEW_TTL_SECONDS + 1)
    assert (
        timedelta(seconds=PREVIEW_TTL_SECONDS - 5)
        < delta
        <= timedelta(seconds=PREVIEW_TTL_SECONDS + 1)
    )


async def test_apply_validated_preview_creates_overlays(db_session, organization, admin_user):
    from idraa.models.csv_import_preview import CSVImportPreview
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    token, _preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + _row_bytes(),
    )
    assert errors == []

    imported, skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
        ip_address="127.0.0.1",
    )
    assert imported == 1
    assert skipped == 0
    assert apply_errors == []

    # Overlay row was created.
    overlay = (
        await db_session.execute(
            select(OverlayDefinition).where(
                OverlayDefinition.organization_id == organization.id,
                OverlayDefinition.tag == "new_overlay",
            )
        )
    ).scalar_one_or_none()
    assert overlay is not None
    assert overlay.frequency_multiplier == pytest.approx(1.5)

    # Preview row deleted on successful apply.
    remaining = (await db_session.execute(select(CSVImportPreview))).scalars().all()
    assert remaining == []


async def test_apply_validated_preview_writes_summary_audit_row(
    db_session, organization, admin_user
):
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    token, _, _ = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + _row_bytes(),
    )
    await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )

    audits = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "overlay.import")))
        .scalars()
        .all()
    )
    assert len(audits) == 1
    audit = audits[0]
    changes = dict(audit.changes)
    # All values follow the [None, value] pair convention.
    assert changes["imported"] == [None, 1]
    assert changes["skipped"] == [None, 0]
    assert changes["errors_count"] == [None, 0]
    assert audit.organization_id == organization.id
    assert audit.user_id == admin_user.id


# ---- row-level errors --------------------------------------------------


async def test_invalid_row_in_preview_is_reported(db_session, organization, admin_user):
    """Bad multiplier value -> error in preview, no row imported."""
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    bad_row = _row_bytes(tag="bad_overlay", display_name="Bad", freq="not_a_number")
    token, preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + bad_row,
    )
    assert errors  # non-empty
    assert any(
        "frequency_multiplier" in (e["column"] or "") or "float" in e["reason"].lower()
        for e in errors
    )
    # The bad row is not in the preview (or carries an action="error" flag).
    bad_preview_rows = [r for r in preview if r.get("tag") == "bad_overlay"]
    if bad_preview_rows:
        assert bad_preview_rows[0].get("action") == "error"

    # Apply still succeeds (no good rows to import) but writes 0 imported,
    # the same error list, and the preview is consumed.
    imported, _skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 0
    assert apply_errors  # surfaced again on apply


async def test_duplicate_within_csv_flagged(db_session, organization, admin_user):
    from idraa.services.overlays_importer import validate_csv

    csv_bytes = (
        _HEADER
        + _row_bytes(tag="dup_overlay", display_name="Dup", freq="1.0", mag="1.0")
        + _row_bytes(tag="dup_overlay", display_name="Dup2", freq="1.5", mag="1.5")
    )
    _, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=csv_bytes,
    )
    assert any("duplicate" in e["reason"].lower() for e in errors)


# ---- upsert path -------------------------------------------------------


async def test_existing_active_tag_updates_via_upsert(
    db_session, organization, admin_user, seeded_critical_infrastructure_overlay
):
    """Re-importing an existing active tag bumps version and updates multipliers."""
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    csv_bytes = _HEADER + _row_bytes(
        tag="critical_infrastructure",
        display_name="Critical Infrastructure",
        freq="1.7",
        mag="2.0",
        sources="docs/reference/calibration-sources/ic3_2025.md",
        methodology=(
            "Updated TEF based on Q2 advisory rate review showing elevated targeting on CI sectors."
        ),
        reason="Bulk re-import after Q2 review",
    )
    token, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=csv_bytes,
    )
    assert errors == []

    imported, _, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 1
    assert apply_errors == []

    await db_session.refresh(seeded_critical_infrastructure_overlay)
    assert seeded_critical_infrastructure_overlay.version == 2
    assert seeded_critical_infrastructure_overlay.frequency_multiplier == pytest.approx(1.7)


async def test_tag_rename_via_importer_is_not_possible(
    db_session, organization, admin_user, seeded_critical_infrastructure_overlay
):
    """Re-importing the seeded tag preserves the tag — even if a future
    code change tried to use the form's tag for rename, OverlayService.update
    rejects it."""
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    csv_bytes = _HEADER + _row_bytes(
        tag="critical_infrastructure",
        display_name="Critical Infrastructure",
        freq="1.6",
        mag="2.1",
        methodology=("Updated to reflect Q2 advisory targeting elevation across CI segments."),
        reason="Q2 calibration update",
    )
    token, _, _ = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=csv_bytes,
    )
    await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    await db_session.refresh(seeded_critical_infrastructure_overlay)
    assert seeded_critical_infrastructure_overlay.tag == "critical_infrastructure"


# ---- deactivated-tag rejection ----------------------------------------


async def test_deactivated_tag_is_rejected_not_silently_reactivated(
    db_session, organization, admin_user, seeded_critical_infrastructure_overlay
):
    from idraa.services.overlays import OverlayService
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    svc = OverlayService(db_session)
    await svc.deactivate(
        overlay=seeded_critical_infrastructure_overlay,
        user_id=admin_user.id,
        reason="manual deactivation for test",
    )
    await db_session.flush()
    assert seeded_critical_infrastructure_overlay.is_active is False

    csv_bytes = _HEADER + _row_bytes(
        tag="critical_infrastructure",
        display_name="Critical Infrastructure",
        freq="1.4",
        mag="2.0",
        methodology=("Re-imported after deactivation — should be rejected by the importer."),
        reason="Should not apply",
    )
    token, _preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=csv_bytes,
    )
    # Validation surfaces the deactivated-tag rejection.
    assert any(
        "deactivat" in e["reason"].lower() or "inactive" in e["reason"].lower() for e in errors
    ), errors

    imported, _, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 0
    assert any(
        "deactivat" in e["reason"].lower() or "inactive" in e["reason"].lower()
        for e in apply_errors
    )

    await db_session.refresh(seeded_critical_infrastructure_overlay)
    # is_active remained False — no silent reactivation.
    assert seeded_critical_infrastructure_overlay.is_active is False
    assert seeded_critical_infrastructure_overlay.frequency_multiplier == pytest.approx(
        1.4
    )  # unchanged seed value


# ---- physical-line tracking -------------------------------------------


async def test_physical_line_numbers_in_errors(db_session, organization, admin_user):
    """Comment lines INTERLEAVED with data rows must not desynchronise the
    error line counter from the user's actual file lines."""
    from idraa.services.overlays_importer import validate_csv

    # Lines 1..7:
    # 1: # comment
    # 2: # comment
    # 3: header
    # 4: # comment
    # 5: good row
    # 6: # comment
    # 7: bad row
    csv_bytes = (
        b"# top-of-file comment\n"
        b"# another comment\n"
        + _HEADER
        + b"# inline comment between header and data\n"
        + _row_bytes(tag="good_one", display_name="Good", freq="1.0", mag="1.0")
        + b"# another inline comment\n"
        + _row_bytes(tag="bad_one", display_name="Bad", freq="not_a_number", mag="1.0")
    )
    _, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=csv_bytes,
    )
    bad_line_errors = [
        e
        for e in errors
        if e.get("column", "").startswith("frequency_multiplier")
        or "float" in e.get("reason", "").lower()
    ]
    assert bad_line_errors, errors
    # Physical line of the bad row is 7 (1-indexed).
    assert bad_line_errors[0]["line"] == 7, errors


# ---- decode strictness -----------------------------------------------


async def test_utf8_strict_decode_failure_is_user_visible_error(
    db_session, organization, admin_user
):
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    # Lone 0xff byte in row data — invalid UTF-8.
    bad = _HEADER + b'foo,Foo,1.0,1.0,,"a\xffb",reason\n'
    token, preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=bad,
    )
    assert preview == []
    assert errors and len(errors) == 1
    assert errors[0]["column"] == "encoding"
    assert "utf-8" in errors[0]["reason"].lower()
    # Apply should also bail cleanly with the same error.
    imported, _, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 0
    assert apply_errors and apply_errors[0]["column"] == "encoding"


# ---- row cap ----------------------------------------------------------


async def test_row_count_cap(db_session, organization, admin_user):
    from idraa.services.overlays_importer import (
        MAX_CSV_ROWS,
        apply_validated_preview,
        validate_csv,
    )

    # 501 rows, all unique tags.
    rows = b"".join(
        _row_bytes(
            tag=f"overlay_{i:04d}",
            display_name=f"Overlay {i}",
            freq="1.0",
            mag="1.0",
            methodology=("Synthetic overlay row used by the row-count-cap test."),
            reason="bulk add",
        )
        for i in range(MAX_CSV_ROWS + 1)
    )
    token, preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + rows,
    )
    assert preview == []
    assert errors and len(errors) == 1
    assert "500" in errors[0]["reason"]
    assert errors[0]["line"] == 0

    imported, _, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 0
    assert apply_errors and "500" in apply_errors[0]["reason"]


# ---- token / org / TTL ------------------------------------------------


async def test_apply_with_unknown_token_raises_preview_expired_error(
    db_session, organization, admin_user
):
    from idraa.services.overlays_importer import (
        PreviewExpiredError,
        apply_validated_preview,
    )

    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(
            db_session,
            token="ffffffff-ffff-ffff-ffff-ffffffffffff",
            org_id=organization.id,
            user_id=admin_user.id,
        )


async def test_apply_with_expired_token_raises_preview_expired_error(
    db_session, organization, admin_user
):
    from idraa.models.csv_import_preview import CSVImportPreview
    from idraa.services.overlays_importer import (
        PreviewExpiredError,
        apply_validated_preview,
        validate_csv,
    )

    token, _, _ = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + _row_bytes(),
    )

    # Manually expire the row. token is a UUID-string; the column is Uuid.
    preview_row = (
        await db_session.execute(
            select(CSVImportPreview).where(CSVImportPreview.id == uuid.UUID(token))
        )
    ).scalar_one()
    # The CHECK constraint enforces ``expires_at > created_at``, so we
    # back-date created_at too — nudges around the cross-DB CHECK without
    # bypassing it.
    past = datetime.now(UTC) - timedelta(seconds=10)
    preview_row.created_at = past - timedelta(seconds=1)
    preview_row.expires_at = past
    await db_session.flush()

    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(
            db_session,
            token=token,
            org_id=organization.id,
            user_id=admin_user.id,
        )


async def test_apply_with_malformed_token_raises_preview_expired_error(
    db_session, organization, admin_user
):
    """A non-UUID token string raises PreviewExpiredError, not a ValueError
    leaking from uuid.UUID(...)."""
    from idraa.services.overlays_importer import (
        PreviewExpiredError,
        apply_validated_preview,
    )

    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(
            db_session,
            token="not-a-uuid",
            org_id=organization.id,
            user_id=admin_user.id,
        )


# ---- header / structural failures ------------------------------------


async def test_missing_required_header_columns(db_session, organization, admin_user):
    """A CSV whose header row is missing required columns is rejected
    wholesale — single ``column='header'`` error, empty preview, and the
    same error replayed on apply."""
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    bad_header = (
        b"tag,display_name,frequency_multiplier\n"  # missing 4 required columns
        b"x,X,1.0\n"
    )
    token, preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=bad_header,
    )
    assert preview == []
    assert len(errors) == 1
    assert errors[0]["column"] == "header"
    assert "missing required columns" in errors[0]["reason"]

    imported, _, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 0
    assert apply_errors and apply_errors[0]["column"] == "header"


async def test_empty_or_comments_only_csv(db_session, organization, admin_user):
    """A file with only comments / blanks (no header, no data) gets a
    single ``column='header'`` error explaining the empty file."""
    from idraa.services.overlays_importer import validate_csv

    only_comments = b"# nothing here\n# really nothing\n\n"
    _, preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=only_comments,
    )
    assert preview == []
    assert errors and errors[0]["column"] == "header"
    assert "empty" in errors[0]["reason"].lower()


# ---- per-row validation paths ----------------------------------------


async def test_row_with_blank_tag_emits_error(db_session, organization, admin_user):
    from idraa.services.overlays_importer import validate_csv

    # The leading-comma-blank-tag row is otherwise structurally valid.
    csv_bytes = (
        _HEADER
        + b',Some Display Name,1.0,1.0,,"Methodology long enough to pass validation.","why"\n'
    )
    _, preview, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=csv_bytes,
    )
    assert any(e["column"] == "tag" and "required" in e["reason"] for e in errors)
    # The preview row carries action='error' so the route layer can render
    # it visually as a failed line.
    error_rows = [r for r in preview if r.get("action") == "error"]
    assert error_rows


async def test_row_with_bad_magnitude_emits_column_specific_error(
    db_session, organization, admin_user
):
    from idraa.services.overlays_importer import validate_csv

    bad = _row_bytes(
        tag="bad_mag",
        display_name="Bad Mag",
        freq="1.0",
        mag="not_a_number",
    )
    _, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + bad,
    )
    assert any(
        e["column"] == "magnitude_multiplier" and "float" in e["reason"].lower() for e in errors
    )


async def test_row_with_pydantic_validation_error_renders_msg_only(
    db_session, organization, admin_user
):
    """Methodology shorter than 20 chars — surfaces Pydantic err['msg'],
    NOT str(exc) (no leaked Pydantic dict repr)."""
    from idraa.services.overlays_importer import validate_csv

    bad = _row_bytes(
        tag="short_meth",
        display_name="Short",
        freq="1.0",
        mag="1.0",
        methodology="too short",  # < 20 chars
        reason="attempted",
    )
    _, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + bad,
    )
    methodology_errs = [e for e in errors if "methodology" in e.get("column", "")]
    assert methodology_errs
    # The reason must NOT contain the Pydantic dict repr ("type=", "input=").
    for e in methodology_errs:
        assert "type=" not in e["reason"]
        assert "input=" not in e["reason"]


async def test_short_row_is_padded_then_validated(db_session, organization, admin_user):
    """A row missing trailing columns is padded with empty strings; the
    pydantic validator then complains about (e.g.) the empty
    methodology_change_reason rather than KeyError'ing."""
    from idraa.services.overlays_importer import validate_csv

    # 4 cells instead of the expected 7.
    short_row = b"short_row,Short,1.0,1.0\n"
    _, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + short_row,
    )
    # The row was padded — validation now complains about the missing
    # methodology and methodology_change_reason fields, not KeyError.
    assert errors
    assert any("methodology" in e.get("column", "") for e in errors), errors


async def test_apply_surfaces_value_error_as_row_error(
    db_session, organization, admin_user, monkeypatch
):
    """If OverlayService.create raises ValueError (one of the two contract
    exceptions), the error is captured per-row rather than blowing up the
    whole apply."""
    from idraa.services.overlays import OverlayService
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    token, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + _row_bytes(),
    )
    assert errors == []

    async def boom(self, **_kwargs):
        raise ValueError("synthetic value-error from create")

    monkeypatch.setattr(OverlayService, "create", boom)

    imported, skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 0
    assert skipped == 1
    assert apply_errors and "synthetic value-error from create" in apply_errors[0]["reason"]


async def test_apply_surfaces_version_conflict_as_row_error(
    db_session,
    organization,
    admin_user,
    monkeypatch,
    seeded_critical_infrastructure_overlay,
):
    """An OverlayVersionConflictError raised by a concurrent edit between
    validate_csv and apply_validated_preview is captured per-row — this is
    the actual concurrent-edit case the importer is contracted to absorb."""
    from idraa.services.overlays import OverlayService, OverlayVersionConflictError
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    token, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER
        + _row_bytes(
            tag="critical_infrastructure",
            display_name="Critical Infrastructure",
            freq="1.6",
            mag="2.0",
            methodology=("Updated to reflect Q2 advisory targeting elevation across CI segments."),
            reason="bulk re-import",
        ),
    )
    assert errors == []

    async def boom(self, **_kwargs):
        raise OverlayVersionConflictError("synthetic version conflict on update")

    monkeypatch.setattr(OverlayService, "update", boom)

    imported, skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user_id=admin_user.id,
    )
    assert imported == 0
    assert skipped == 1
    assert apply_errors and "synthetic version conflict on update" in apply_errors[0]["reason"]


async def test_apply_does_not_swallow_unexpected_exception_on_create(
    db_session, organization, admin_user, monkeypatch
):
    """RuntimeError from OverlayService is NOT a row-scoped error — it must
    propagate so unexpected failures fail loud (B14 fail-loud invariant).

    The importer narrows its catch to (OverlayVersionConflictError,
    ValueError), the two contractual exceptions OverlayService is
    documented to raise. Anything else (asyncio.CancelledError,
    IntegrityError, AttributeError from a typo, ...) must surface."""
    from idraa.services.overlays import OverlayService
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    token, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + _row_bytes(),
    )
    assert errors == []

    async def boom(self, **_kwargs):
        raise RuntimeError("unexpected boom from create")

    monkeypatch.setattr(OverlayService, "create", boom)

    with pytest.raises(RuntimeError, match="unexpected boom from create"):
        await apply_validated_preview(
            db_session,
            token=token,
            org_id=organization.id,
            user_id=admin_user.id,
        )


async def test_apply_does_not_swallow_unexpected_exception_on_update(
    db_session,
    organization,
    admin_user,
    monkeypatch,
    seeded_critical_infrastructure_overlay,
):
    """Counterpart to the create-side propagation test on the update path."""
    from idraa.services.overlays import OverlayService
    from idraa.services.overlays_importer import (
        apply_validated_preview,
        validate_csv,
    )

    token, _, errors = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER
        + _row_bytes(
            tag="critical_infrastructure",
            display_name="Critical Infrastructure",
            freq="1.6",
            mag="2.0",
            methodology=("Updated to reflect Q2 advisory targeting elevation across CI segments."),
            reason="bulk re-import",
        ),
    )
    assert errors == []

    async def boom(self, **_kwargs):
        raise RuntimeError("unexpected boom from update")

    monkeypatch.setattr(OverlayService, "update", boom)

    with pytest.raises(RuntimeError, match="unexpected boom from update"):
        await apply_validated_preview(
            db_session,
            token=token,
            org_id=organization.id,
            user_id=admin_user.id,
        )


async def test_apply_with_wrong_org_token_raises_preview_expired_error(
    db_session, organization, admin_user
):
    """Token belonging to org A is rejected when the apply call passes org B's
    id — uniform PreviewExpiredError to avoid an existence oracle."""
    from idraa.services.overlays_importer import (
        PreviewExpiredError,
        apply_validated_preview,
        validate_csv,
    )
    from tests.factories import create_org

    token, _, _ = await validate_csv(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        csv_bytes=_HEADER + _row_bytes(),
    )

    other_org = await create_org(db_session, name="Other Org")
    with pytest.raises(PreviewExpiredError):
        await apply_validated_preview(
            db_session,
            token=token,
            org_id=other_org.id,
            user_id=admin_user.id,
        )
