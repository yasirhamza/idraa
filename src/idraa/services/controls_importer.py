"""CSV import for the FAIR-CAM controls library (#68 per-path multi-assignment).

Accepts the project's curated CSV at docs/reference/fair-cam-controls-library.csv.
Row shape:
  [<blank>, <name>, <description>, <fair-cam-paths>, <type>, <annual_cost>,
   <capability_value?>]
The first real row is a header we skip.

Column 7 (``capability_value``) is OPTIONAL — added in PR μ.1b (issue #129).
6-column CSVs continue to import unchanged (backward-compatible). When
present and non-empty, it overrides the per-unit default capability for
SINGLE-path rows only; multi-path rows with a non-empty col 7 are
skipped with a logged warning (single-cap dict semantics for multi-path
rows are deferred to a follow-up). Values are validated against the
sub-function's UnitType bounds: PROBABILITY / PERCENT_REDUCTION → [0, 1];
ELAPSED_TIME / CURRENCY → [0, 1e10] (matching the PR μ.1 DB CHECK
constraint at models/control_function_assignment.py:73). ELAPSED_TIME
capability_value is interpreted in **days** (NOT hours) — it is plugged
directly into the engine's exp(-t/τ) decay where τ is in days (IBM CODB
MTTI/MTTC, DBIR KEV survival; see fair_cam/calibration/elapsed_time_taus.py)
and the edit form renders a "days" widget. For a 24-hour MTTD/SLA, use
``1`` (= 1 day), not ``24``. Issue #204. Non-numeric,
non-finite (inf/nan), out-of-range, or negative values → row skipped
with logged warning.

Column 4 carries one or more FAIR-CAM sub-function path strings separated
by newlines. Each path is normalized (whitespace+case) and looked up in
``controls_importer_lookup.PATH_TO_SUB_FUNCTION`` — one
``ControlFunctionAssignment`` is created per recognized non-virtual path.
Unknown / virtual (``DSC_CORR_MISALIGNED``) paths skip the assignment
and log a warning; the Control still imports. The Control's domain set
is derived at read time from its assignments via ``Control.domains``
(issue #90 dropped the denormalized ``Control.domain`` column).

The Type column (col 5) carries one of {technical, administrative, physical}.
Missing / blank / unrecognised values fall back to ``ControlType.ADMINISTRATIVE``.

The Annual cost (USD) column (col 6) is parsed as a non-negative Decimal.
Empty / missing / non-numeric → Decimal('0'); negative → row skipped with
a warning. The form DTO enforces ge=0 already but the importer bypasses it,
so this is the importer-side equivalent. See issue #65.

Importer-created assignments uniformly use:
  - capability_value = 0.7 for PROBABILITY / PERCENT_REDUCTION units;
    NULL for ELAPSED_TIME / CURRENCY (fair_cam filters those at compose time).
  - coverage = 0.8, reliability = 0.8.
  - confirmed_by_user_at = NULL — operator confirms via the UI.
  - Audit action ``"control.import"`` with ``changes.sub_function_count``.

ip_address is threaded through to AuditLog for every imported row —
same 1.1.6.a I2 invariant as the rest of the services (see services/controls.py).
"""

from __future__ import annotations

import csv
import io
import logging
import math
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    SUB_FUNCTION_UNITS,
    ControlImplementationStage,
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    UnitType,
)
from idraa.schemas.organization import _MONEY_MAX
from idraa.services.audit import AuditWriter
from idraa.services.controls_importer_lookup import (
    PATH_TO_SUB_FUNCTION,
    VirtualRejectSentinel,
    normalize_path,
)

logger = logging.getLogger(__name__)

# Default effectiveness values for importer-created assignments.
_DEFAULT_CAPABILITY = 0.7
_DEFAULT_COVERAGE = 0.8
_DEFAULT_RELIABILITY = 0.8

# Cap user-supplied CSV strings before sending to logger.warning so a
# multi-megabyte CSV cell can't blow up the log handler / journald.
_LOG_FIELD_CAP = 200

# Sec-I3 (issue #129 plan-gate-round-1): bound the importer's row count.
# The byte cap upstream (5 MB) doesn't bound row count — a pathological
# 700K-row CSV that fits the byte cap would otherwise cost minutes of
# per-row DB roundtrips. Cap-and-break (not raise) — partial imports
# preserve already-flushed rows; remaining rows are skipped with one
# logged warning.
MAX_CSV_ROWS = 10_000

# PR μ.1 CHECK constraint upper bound on capability_value (see
# models/control_function_assignment.py:73 + 1297897c44f5_pr_mu_1_*
# alembic revision). ELAPSED_TIME / CURRENCY units accept any non-negative
# value up to this cap; PROBABILITY / PERCENT_REDUCTION units are tighter
# at 1.0.
_CAPABILITY_VALUE_MAX = 1e10


async def _existing_names(db: AsyncSession, org_id: uuid.UUID) -> set[str]:
    rows = await db.execute(
        select(Control.name).where(
            Control.organization_id == org_id,
            Control.status
            != EntityStatus.DELETED,  # paranoid-review fix: soft-deleted don't block re-import
        )
    )
    return {n.lower() for (n,) in rows.all()}


async def import_csv(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    csv_bytes: bytes,
    ip_address: str | None = None,
) -> tuple[int, int]:
    """Returns (imported, skipped). Skips blanks, duplicates, header row.

    Each imported Control row receives one ControlFunctionAssignment with
    confirmed_by_user_at=NULL (Decision 8 — importer assignments are unconfirmed
    until a human reviews them via POST /controls/{id}/assignments/{aid}/confirm).
    """
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    existing = await _existing_names(db, org_id)

    imported = 0
    skipped = 0
    audit = AuditWriter(db)

    row_count = 0
    for raw_row in reader:
        # Sec-I3 (issue #129): cap-and-break on MAX_CSV_ROWS. Must remain
        # the very first statements in the loop body so the bound is
        # enforced before any per-row work (parsing / DB writes).
        row_count += 1
        if row_count > MAX_CSV_ROWS:
            logger.warning(
                "controls_importer: CSV exceeds MAX_CSV_ROWS (%d); aborting at row %d. "
                "Remaining rows skipped.",
                MAX_CSV_ROWS,
                row_count,
            )
            break

        # 7-column padding (T7): col index 6 = optional capability_value.
        row = raw_row + [""] * max(0, 7 - len(raw_row))
        name = (row[1] or "").strip()
        description: str | None = (row[2] or "").strip() or None
        paths_col = row[3] or ""
        type_text = (row[4] or "").strip().lower()
        cost_text = (row[5] or "").strip()
        capability_text = (row[6] or "").strip()
        if not name or name.lower() == "control" or name.lower() in existing:
            skipped += 1
            continue

        # Parse annual_cost (col 6). Empty / non-numeric → 0; negative or
        # non-finite or > _MONEY_MAX → skip with warning. The form DTO at
        # schemas/control.py:178 enforces the same bounds; this is the
        # importer-side mirror since the importer bypasses the form.
        # Decimal("Infinity") doesn't raise on construction and compares
        # >= 0, so explicit is_finite() is required.
        if cost_text:
            try:
                annual_cost = Decimal(cost_text)
            except (ArithmeticError, ValueError):
                logger.warning(
                    "controls_importer: unparseable annual_cost %r on control %r — defaulting to 0",
                    cost_text[:_LOG_FIELD_CAP],
                    name[:_LOG_FIELD_CAP],
                )
                annual_cost = Decimal("0")
            else:
                if not annual_cost.is_finite() or annual_cost < 0 or annual_cost > _MONEY_MAX:
                    logger.warning(
                        "controls_importer: out-of-range annual_cost %r on control %r — skipping row",
                        cost_text[:_LOG_FIELD_CAP],
                        name[:_LOG_FIELD_CAP],
                    )
                    skipped += 1
                    continue
        else:
            annual_cost = Decimal("0")

        # Split col 4 on newlines, normalize each, strict lookup.
        # Unknown / virtual paths skip the assignment + log warning;
        # the Control still imports (Q2). Args truncated to 200 chars to
        # prevent log-volume amplification from large CSV cells.
        recognized_subfns: list[FairCamSubFunction] = []
        for raw_line in paths_col.split("\n"):
            stripped = raw_line.strip()
            if not stripped:
                continue
            mapped = PATH_TO_SUB_FUNCTION.get(normalize_path(stripped))
            if mapped is None:
                logger.warning(
                    "controls_importer: unrecognized sub-function path %r on control %r — skipping assignment",
                    stripped[:_LOG_FIELD_CAP],
                    name[:_LOG_FIELD_CAP],
                )
                continue
            if isinstance(mapped, VirtualRejectSentinel):
                logger.warning(
                    "controls_importer: virtual sub-function path %r on control %r — skipping assignment",
                    stripped[:_LOG_FIELD_CAP],
                    name[:_LOG_FIELD_CAP],
                )
                continue
            recognized_subfns.append(mapped)

        # Issue #90: the Q4 denormalized `Control.domain` field is gone —
        # domains derive from assignments at read time via Control.domains.
        # The importer no longer needs to compute / write a primary domain.

        # T7 (issue #129): parse + validate optional col 7 capability_value.
        # Empty / absent → user_capability_value=None and the per-assignment
        # default-by-unit logic below applies. Non-empty → validated against
        # the resolved sub-function's UnitType-derived bounds and replaces
        # the default for that single assignment.
        #
        # Multi-path constraint (Sec-N3 plan-gate-round-1): if a row maps
        # to multiple sub-functions AND col 7 is non-empty, skip the row
        # — μ.1b only supports single-assignment capability_value per row.
        # Multi-path single-cap dict semantics are deferred to a follow-up.
        user_capability_value: float | None = None
        if capability_text:
            if len(recognized_subfns) != 1:
                logger.warning(
                    "controls_importer: capability_value %r provided for control %r "
                    "but row resolved to %d sub-function(s) — skipping row "
                    "(multi-path / no-path single-cap deferred).",
                    capability_text[:_LOG_FIELD_CAP],
                    name[:_LOG_FIELD_CAP],
                    len(recognized_subfns),
                )
                skipped += 1
                continue
            try:
                cap = float(capability_text)
            except (ValueError, ArithmeticError):
                logger.warning(
                    "controls_importer: unparseable capability_value %r on control %r — skipping row",
                    capability_text[:_LOG_FIELD_CAP],
                    name[:_LOG_FIELD_CAP],
                )
                skipped += 1
                continue
            # Sec-I1 (issue #129 plan-gate-round-1): explicit guard for
            # inf / nan / "1e500" tokens. ``float("inf")``, ``float("nan")``,
            # ``float("1e500")`` (→inf) all parse cleanly above; isfinite()
            # rejects them here so they never reach the DB CHECK constraint.
            if not math.isfinite(cap) or cap < 0:
                logger.warning(
                    "controls_importer: out-of-range (non-finite or negative) "
                    "capability_value %r on control %r — skipping row",
                    capability_text[:_LOG_FIELD_CAP],
                    name[:_LOG_FIELD_CAP],
                )
                skipped += 1
                continue
            # Unit-derived upper bound. Multi-path rows (len != 1) are rejected
            # with a warning above; control reaches here only when exactly one
            # path was resolved, so [0] is the sole element by construction.
            sf_enum = recognized_subfns[0]  # adapter-iter: ok — single-path enforced above
            unit = SUB_FUNCTION_UNITS[sf_enum]
            if unit in (UnitType.PROBABILITY, UnitType.PERCENT_REDUCTION) and cap > 1.0:
                logger.warning(
                    "controls_importer: capability_value %r > 1.0 for %s sub-function %r "
                    "on control %r — skipping row",
                    cap,
                    unit.value,
                    sf_enum.value,
                    name[:_LOG_FIELD_CAP],
                )
                skipped += 1
                continue
            if cap > _CAPABILITY_VALUE_MAX:
                logger.warning(
                    "controls_importer: capability_value %r exceeds %g cap "
                    "on control %r — skipping row",
                    cap,
                    _CAPABILITY_VALUE_MAX,
                    name[:_LOG_FIELD_CAP],
                )
                skipped += 1
                continue
            user_capability_value = cap

        # Read Type from col 4. Falls back to ADMINISTRATIVE for blank /
        # unrecognised values.
        try:
            ctrl_type = ControlType(type_text) if type_text else ControlType.ADMINISTRATIVE
        except ValueError:
            ctrl_type = ControlType.ADMINISTRATIVE

        c = Control(
            organization_id=org_id,
            created_by=user_id,
            name=name,
            description=description,
            type=ctrl_type,
            annual_cost=annual_cost,
            status=EntityStatus.ACTIVE,
            implementation_stage=ControlImplementationStage.ACTIVE,  # #395: imports are active
            version="1.0",
        )
        db.add(c)
        await db.flush()  # populate c.id

        # Q5: uniform defaults per unit type. PROBABILITY / PERCENT_REDUCTION
        # get capability=0.7; ELAPSED_TIME / CURRENCY get NULL (fair_cam's
        # compose_group_effectiveness filters those at composition time).
        # T7 (issue #129): when col 7 provided a user_capability_value and
        # the row is single-path, override the default for that assignment.
        # The user value was already validated against the sub-function's
        # unit bounds above.
        for subfn in recognized_subfns:
            unit = SUB_FUNCTION_UNITS[subfn]
            cap_val: float | None
            if user_capability_value is not None and len(recognized_subfns) == 1:
                cap_val = user_capability_value
            elif unit in (UnitType.PROBABILITY, UnitType.PERCENT_REDUCTION):
                cap_val = _DEFAULT_CAPABILITY
            else:
                cap_val = None
            db.add(
                ControlFunctionAssignment(
                    control_id=c.id,
                    organization_id=org_id,
                    sub_function=subfn,
                    capability_value=cap_val,
                    coverage=_DEFAULT_COVERAGE,
                    reliability=_DEFAULT_RELIABILITY,
                    confirmed_by_user_at=None,  # importer = unconfirmed
                    measured_by=None,
                    measured_at=None,
                    derived_from_assignment_id=None,
                )
            )
        await db.flush()

        # Audit action follows the project-wide <entity>.<verb> taxonomy.
        # sub_function_count makes the audit trail self-describing for
        # multi-assignment imports (#68).
        await audit.log(
            organization_id=org_id,
            entity_type="control",
            entity_id=c.id,
            action="control.import",
            changes={
                "name": [None, c.name],
                "sub_function_count": [None, len(recognized_subfns)],
            },
            user_id=user_id,
            ip_address=ip_address,
        )
        existing.add(name.lower())
        imported += 1

    return imported, skipped
