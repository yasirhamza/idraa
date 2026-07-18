"""CSVImportPreview — short-lived staging table for two-step CSV imports.

Per plan §B13: bulk-import flows are split into ``validate_csv`` (parse +
validate, store raw bytes here under a token with a 10-minute TTL) and
``apply_validated_preview`` (re-parse + upsert + delete the preview row).
This row carries no business state — it is a transient staging area that
keeps the route layer's preview/confirm pages stateless across requests.

The table is **shared** across entity types: the C6 importer writes
``entity_type='overlay'`` rows; PR δ (calibration overrides) will write
``entity_type='calibration_override'`` rows against the same schema.
``entity_type`` is a free-form discriminator rather than an enum so a new
import flow can land without an Alembic migration.

Token shape: the row's UUID primary key (``id``) doubles as the opaque
token returned to the caller. No separate token column — the PK already
gives us a per-row unique handle, and reusing it avoids a second secret
that could drift out of sync with the row identity.

FK behaviour:

- ``organization_id`` ondelete RESTRICT — matches every other business
  table (organizations are pinned for the life of the v3 phase 1).
- ``created_by_user_id`` ondelete SET NULL — same as ``audit_log``: the
  user may be deactivated/deleted but the preview row should outlive
  them long enough for downstream auditing if they were the last to
  upload.

The ``ix_csv_import_preview_expires_at`` index lets a future cleanup job
(out of scope for C6) sweep expired rows efficiently.

``state_json`` (epic #34 P1c Task 2): accumulating step-choice storage for
the staged register-import flow (``entity_type`` prefixed ``"register:"``,
see ``services/register_import.py`` Task 3) — sheet selection, column map,
value bindings pile up here across the wizard's full-page-redirect steps,
re-read and re-written at each step against the same immutable
``csv_bytes``. Register-import-only today: the C6 overlay importer and the
calibration-override importer leave it NULL. A future flow could reuse it,
but nothing else writes it yet.

**Write rule (Arch-I1 plan-gate amendment, BINDING):** ``state_json`` is a
plain ``JSON`` column — SQLAlchemy does NOT track in-place mutation of a
plain ``dict`` attribute (no ``MutableDict`` wrapper here, deliberately).
Every setter MUST reassign the whole dict rather than mutate a key in
place::

    # correct — reassigns the whole dict, SQLAlchemy sees the change
    preview.state_json = {**(preview.state_json or {}), "column_map": value}

    # WRONG — silently lost on flush/commit, no dirty-tracking fires
    preview.state_json["column_map"] = value

This mirrors the only other in-repo precedent for a raw-``JSON``-column
step-accumulator, ``services/wizard_state.py:248``. Do not introduce
``MutableDict`` here as a shortcut — the reassignment discipline is the
chosen fix, consistent with the existing precedent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin

# 10-minute TTL — set on csv_import_preview.expires_at by both overlay and override importers.
PREVIEW_TTL_SECONDS: int = 600


class CSVImportPreview(IdMixin, TimestampMixin, OrgMixin, Base):
    """One row per pending two-step CSV upload.

    Lifecycle: ``validate_csv`` inserts; ``apply_validated_preview`` reads
    + deletes. Rows that aren't applied within ``expires_at`` are stale
    and any apply attempt is rejected uniformly with
    ``PreviewExpiredError`` (whether not-found, expired, or wrong-org —
    a single error class avoids an existence oracle).
    """

    __tablename__ = "csv_import_preview"

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    csv_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Register-import-only (see class docstring). NULL for every other
    # entity_type. See the module docstring's Arch-I1 write rule before
    # writing to this column — whole-dict reassignment only, never `[key] =`.
    state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        # length() is cross-DB (SQLite + Postgres); char_length is Postgres-only.
        CheckConstraint(
            "length(entity_type) > 0",
            name="ck_csv_import_preview_entity_type_required",
        ),
        # expires_at must strictly exceed created_at — a 0- or negative-
        # TTL row is meaningless. Cross-DB simple column comparison;
        # SQLite + Postgres both enforce this.
        CheckConstraint(
            "expires_at > created_at",
            name="ck_csv_import_preview_expiry_after_creation",
        ),
        Index("ix_csv_import_preview_expires_at", "expires_at"),
    )
