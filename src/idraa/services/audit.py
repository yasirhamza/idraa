"""Audit log writer — every business-row mutation must go through this.

``changes`` schema convention: values are ``[prev, new]`` pairs. Use
``[None, value]`` for state additions, ``[value, None]`` for removals,
and ``[before, after]`` for true changes. Auxiliary annotations that
don't fit the pair shape (e.g. a deactivation reason) should be encoded
as ``[None, value]`` rather than bare scalars — keeps downstream
consumers (audit viewer, diff renderer) on a single schema so they
never need to special-case shape per field.

JSON-safety contract: ``AuditWriter.log`` coerces any ``Decimal`` /
``UUID`` / ``datetime`` / ``date`` / ``Enum`` value inside ``changes``
(recursing through ``list`` / ``tuple`` / ``dict``) into a JSON-friendly
form before storing — callers may pass native Python values without
pre-coercion. ``Decimal`` is preserved losslessly as ``str(d)``; consumers
re-parse via ``Decimal(s)``. (issue #125)
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


class ExportRateLimitedError(Exception):
    """#357 — the caller exceeded the bulk-export cadence cap.

    Raised by ``log_bulk_export`` BEFORE the audit row is written, so a
    rate-limited request neither egresses data nor bloats audit_log. Mapped
    to HTTP 429 (with ``Retry-After``) by the app-level exception handler —
    a plain Exception rather than HTTPException per the services-layer
    convention (see services/org.py).
    """

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"export cadence cap exceeded; retry after {retry_after_seconds}s")


def _to_json_friendly(obj: Any) -> Any:
    """Recursively coerce ``changes`` values to JSON-serializable types.

    SQLAlchemy's ``JSON`` column uses stdlib ``json.dumps`` by default,
    which rejects ``Decimal`` / ``UUID`` / ``datetime`` / ``Enum`` with
    ``TypeError: Object of type X is not JSON serializable``. That bubbles
    up as ``StatementError`` on flush, returns 500 to the HTTP caller, and
    (when invoked from an HTMX form post) leaves the user staring at a
    save button that "doesn't respond". (issue #125)

    Coercions:
      - ``Decimal``  → ``str(d)`` (round-trip via ``Decimal(s)`` preserves precision)
      - ``datetime`` → ``d.isoformat()``
      - ``date``     → ``d.isoformat()``
      - ``UUID``     → ``str(uuid)``
      - ``Enum``     → ``e.value``
      - ``list`` / ``tuple`` → coerced ``list`` (JSON has no tuple)
      - ``dict``     → coerced ``dict`` (recurse values; keys must already be ``str``)
      - everything else (``str`` / ``int`` / ``float`` / ``bool`` / ``None``) → passthrough

    Idempotent: pre-coerced values (e.g. ``str`` from an earlier
    ``.isoformat()`` call) pass through unchanged.
    """
    if isinstance(obj, bool):
        return obj  # bool subclasses int — branch before int check
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_to_json_friendly(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_json_friendly(v) for k, v in obj.items()}
    return obj


def redact_email(email: str | None) -> str | None:
    """Sec-6 R1 / Sec-13 R3 (T10/11 wizard-step-3): email NEVER raw in audit
    payloads. Render as ``"*****@domain"`` so the audit trail can answer
    "which org's domain did this SME belong to?" without exposing the local
    part. Domain leak is documented accepted risk per Sec-15.

    Behavior:
      - ``None`` → ``None`` (no row to redact).
      - well-formed email ``"jane@example.com"`` → ``"*****@example.com"``.
      - malformed (no ``@``) → ``"*****"`` so the audit row never echoes
        the bare string back into the JSON column.

    ``rsplit("@", 1)`` is intentional: emails with ``@`` in the local part
    (legal per RFC 5321 §4.1.2) split on the LAST ``@`` so the domain is
    preserved correctly.
    """
    if email is None:
        return None
    if "@" not in email:
        return "*****"
    domain = email.rsplit("@", 1)[1]
    return f"*****@{domain}"


def bucket_amount(amount: float) -> str:
    """Sec-6 R1 (T10/11 wizard-step-3): order-of-magnitude bucket for
    low/high financial values in broader audit views. Phase-1 audit-log
    read access is admin-only so per-estimate values are also stored raw
    (per spec §6.5 ``sme_estimate.recorded`` payload), but `low_bucket` /
    `high_bucket` give a redacted summary that future cross-org views can
    surface without leaking SME-specific magnitudes.
    """
    if amount < 10_000:
        return "<$10k"
    if amount < 100_000:
        return "$10k-100k"
    if amount < 1_000_000:
        return "$100k-1M"
    if amount < 10_000_000:
        return "$1M-10M"
    if amount < 100_000_000:
        return "$10M-100M"
    return "$100M+"


class AuditWriter:
    """Writes AuditLog rows in the caller's session. Commit is the caller's responsibility."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        changes: dict[str, Any],
        user_id: uuid.UUID | None,
        ip_address: str | None = None,
    ) -> AuditLog:
        row = AuditLog(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            action=action,
            changes=_to_json_friendly(changes),
            ip_address=ip_address,
        )
        self._session.add(row)
        await self._session.flush()
        return row


async def log_bulk_export(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_type: str,
    fmt: str,
    count: int,
    user_id: uuid.UUID | None,
    ip_address: str | None = None,
    filters: dict[str, Any] | None = None,
) -> AuditLog:
    """#304 — one audit row per bulk-egress download.

    Bulk export of org data (or the shared canonical catalogs) to a file is a
    data-movement event auditors need visibility into, even though it is a
    read. Action is ``<entity_type>.export``; ``changes`` records the row
    count, format, and any honored filters (so the row answers "WHAT subset
    left the building").

    ``entity_id`` convention: the organization id — the exported SET has no
    single entity row, and the org is the egress boundary the row describes.
    Commit remains the caller's responsibility (the ``get_db`` dependency
    auto-commits at request end).

    Fail-closed: callers invoke this BEFORE building the download response,
    so a failed audit flush 500s the export instead of letting data leave
    unaudited. Deliberate — audit_log table health gates bulk egress.
    """
    settings = get_settings()
    limit = settings.export_rate_limit_count
    if limit > 0:
        window = settings.export_rate_limit_window_seconds
        cutoff = now_utc() - timedelta(seconds=window)
        # Scope the cap to the requesting user; system/anonymous paths
        # (user_id None) share one org-wide bucket. Counting prior
        # ``*.export`` audit rows makes the audit table itself the limiter
        # state — correct across processes and restarts by construction.
        # organization_id is ALWAYS in the WHERE (even user-scoped) so the
        # query range-scans ix_audit_log_org_time on (org, timestamp) and
        # applies user/action as residuals — user_id alone has no index and
        # would full-scan the table on the common path (SWE review, #357).
        conditions = [
            AuditLog.organization_id == organization_id,
            AuditLog.action.like("%.export"),
            AuditLog.timestamp >= cutoff,
        ]
        if user_id is not None:
            conditions.append(AuditLog.user_id == user_id)
        recent = await session.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
        if (recent or 0) >= limit:
            # Retry-After carries the FULL window, not the true remaining
            # time (which would need the oldest in-window row's age) —
            # deliberately conservative: never invites an early retry.
            raise ExportRateLimitedError(window)

    watermark = settings.audit_log_watermark_rows
    if watermark > 0:
        total = (await session.scalar(select(func.count()).select_from(AuditLog))) or 0
        if total >= watermark:
            logger.warning(
                "audit_log row count %d has reached the watermark %d — "
                "review retention before the table becomes an operational "
                "burden (#357; tune via AUDIT_LOG_WATERMARK_ROWS, 0 disables)",
                total,
                watermark,
            )

    changes: dict[str, Any] = {
        "count": [None, count],
        "format": [None, fmt],
    }
    if filters:
        changes["filters"] = [None, filters]
    return await AuditWriter(session).log(
        organization_id=organization_id,
        entity_type=entity_type,
        entity_id=organization_id,
        action=f"{entity_type}.export",
        changes=changes,
        user_id=user_id,
        ip_address=ip_address,
    )
