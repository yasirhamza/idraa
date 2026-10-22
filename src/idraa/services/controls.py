"""Control CRUD service.

Every mutation writes an AuditLog row in the same session as the business
change (atomic via AuditWriter). ip_address is threaded through from the
caller — routes pass client_ip(request); unit tests pass the default None.

Audit action strings follow the project-wide <entity>.<verb> taxonomy:
  control.create / control.update / control.delete
  control_function_assignment.create / control_function_assignment.update
  control_function_assignment.delete / control_function_assignment.confirm
  control_function_assignment.clear -- non-NULL -> NULL capability_value
  transition; engages model-midpoint fallback (#129 T6)

The "import" action in controls_importer.py is preserved unchanged (OQ5 —
rename to control.import deferred to Phase 2 hygiene PR).

Service-layer design decisions (spec §8.1):
  - M5 explicit-unpack: pop assignments from form_data before constructing
    Control ORM — prevents assignments being silently passed as ORM kwargs.
  - OQ3 cap guard: raises ValueError if len(assignments_data) != 1, mirrors
    Pydantic max_length=1 for defense-in-depth.
  - B-NEW3 / Decision 9 (superseded by #268): a client may never supply
    derived_from_assignment_id. Enforcement lives HERE (``validate_dsc_corr_link``,
    not on the DTO) because the DTO is shared with _snapshot_control_v2 and the
    snapshot readers, which legitimately carry a link.
  - confirmed_by_user_at is set on wizard/form-submitted assignments but NOT
    on importer assignments (which surface in the unconfirmed-warning UX).
  - confirm_assignment: captures prior values before mutation so re-confirms
    produce non-misleading audit diffs (M-NEW3, spec §5.3).

Derived Correcting Misaligned Decisions row (#268, FAIR-CAM §5.3, spec §3.4):
  - A ``dsc_corr_misaligned`` assignment exists only as a DERIVED row holding a link
    (``derived_from_assignment_id``) to another row of the SAME control, with
    sentinels capability NULL / coverage 0.0 / reliability 0.0. It is authored through
    the separate ``dsc_corr_source`` argument of ``create_control`` / ``update_control``
    (an idraa ``FairCamSubFunction`` from ``DSC_CORR_LINK_SOURCES``), never through
    the submitted assignments; the link is resolved server-side from that slug.
  - ``validate_dsc_corr_link`` is pure and is the FIRST statement of both writers: a
    refusal leaves the session untouched.
  - Delete ordering: SQLAlchemy sorts one flush's DELETEs by primary key, and with
    ``PRAGMA foreign_keys=ON`` a surviving derived row's ``ON DELETE SET NULL``
    violates ``ck_cfa_virtual_requires_derived``. Every path that deletes assignment
    rows deletes the derived row and flushes FIRST (``_delete_derived_rows_first``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import LibraryEntryNotFoundError
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.control_library import ControlLibraryEntryAssignment
from idraa.models.enums import (
    DSC_CORR_LINK_SOURCES,
    ControlDomain,
    ControlSource,
    EntityStatus,
    FairCamSubFunction,
    sub_functions_for_domain,
)
from idraa.schemas.control import ControlForm
from idraa.services.audit import AuditWriter

logger = logging.getLogger(__name__)

_DSC_CORR = FairCamSubFunction.DSC_CORR_MISALIGNED


async def list_controls(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    include_deleted: bool = False,
    domain: ControlDomain | None = None,
    source: ControlSource | None = None,
) -> list[Control]:
    """List the active controls for ``org_id``.

    Optional ``domain`` filter (issue #90): returns only controls that
    have at least one ControlFunctionAssignment whose sub_function
    decodes to ``domain``. The org-scope predicate is applied BEFORE the
    JOIN so the filter cannot widen org visibility (plan-gate fix
    Sec-I1).

    Optional ``source`` filter (P2b Task 9): returns only controls whose
    provenance matches (CUSTOM vs LIBRARY_DERIVED). Applied as a plain
    column predicate; org-scope still applies first.
    """
    stmt = select(Control).where(Control.organization_id == org_id)
    if not include_deleted:
        stmt = stmt.where(Control.status != EntityStatus.DELETED)
    if source is not None:
        stmt = stmt.where(Control.source == source)
    if domain is not None:
        stmt = (
            stmt.join(ControlFunctionAssignment)
            .where(ControlFunctionAssignment.sub_function.in_(sub_functions_for_domain(domain)))
            .distinct()
        )
    stmt = stmt.order_by(Control.name)
    rows = await db.execute(stmt)
    return list(rows.scalars().all())


async def get_control_for_org(
    db: AsyncSession, *, organization_id: uuid.UUID, control_id: uuid.UUID
) -> Control | None:
    """Org-scoped lookup. Returns None if the row exists in another org or is missing.

    Replaces the former bare ``get_control(db, control_id)`` (issue #260 root
    cause — a plain ``db.get`` with no org filter, caught by
    ``scripts/lint_org_scoped_lookups.py``). All 8 former callers in
    routes/controls.py duplicated this same check inline; the check now lives
    here once, matching the ``get_for_org`` convention used elsewhere
    (``repositories/scenario_repo.py``, ``services/runs.py``).
    """
    control = await db.get(Control, control_id)
    if control is None or control.organization_id != organization_id:
        return None
    return control


# ---------------------------------------------------------------------------
# Derived Correcting Misaligned Decisions row (#268)
# ---------------------------------------------------------------------------


def validate_dsc_corr_link(
    assignments: list[dict[str, Any]], dsc_corr_source: FairCamSubFunction | None
) -> None:
    """Refuse an invalid Correcting Misaligned Decisions authoring request.

    PURE: reads its arguments, mutates nothing, touches no session. It is the first
    statement of ``create_control`` and ``update_control``, so a refusal leaves
    ``db.dirty`` / ``db.new`` / ``db.deleted`` empty and writes no audit row.

    ``assignments`` are the SUBMITTED assignment dicts (``ControlForm.model_dump()``).
    Raises ``ValueError`` (the routes render it as a 422) when
      - a submitted assignment is ``dsc_corr_misaligned`` (the row is derived, never
        client-authored; the DTO only admits one together with a link id, which is
        refused below anyway);
      - a submitted assignment carries ``derived_from_assignment_id`` (no row id is
        ever accepted from the client — the link is resolved server-side from the
        slug on the same control);
      - ``dsc_corr_source`` is not in ``DSC_CORR_LINK_SOURCES``;
      - ``dsc_corr_source`` names a sub-function the submission does not carry
        (source removed while the link still names it).
    """
    submitted: set[FairCamSubFunction] = set()
    for ad in assignments:
        sub_function = FairCamSubFunction(ad["sub_function"])
        if sub_function is _DSC_CORR:
            raise ValueError(
                "Correcting Misaligned Decisions (dsc_corr_misaligned) cannot be submitted as "
                "an assignment; it is derived from a correction or response assignment of "
                "this control. Choose its source instead."
            )
        if ad.get("derived_from_assignment_id") is not None:
            raise ValueError(
                "derived_from_assignment_id cannot be supplied; the Correcting Misaligned "
                "Decisions link is set by choosing its source sub-function."
            )
        submitted.add(sub_function)

    if dsc_corr_source is None:
        return
    source = FairCamSubFunction(dsc_corr_source)
    if source not in DSC_CORR_LINK_SOURCES:
        eligible = ", ".join(sorted(s.value for s in DSC_CORR_LINK_SOURCES))
        raise ValueError(
            f"{source.value} is not an eligible source for Correcting Misaligned Decisions; "
            f"eligible sources are: {eligible}."
        )
    if source not in submitted:
        raise ValueError(
            f"Correcting Misaligned Decisions is linked to {source.value}, which this control "
            "no longer has; choose another source or none."
        )


async def _row_by_sub_function(
    db: AsyncSession,
    control_id: uuid.UUID,
    organization_id: uuid.UUID,
    sub_function: FairCamSubFunction,
) -> ControlFunctionAssignment | None:
    """Explicit, org-scoped lookup of a control's row by sub-function.

    Never goes through the (possibly stale, under async) ``control.assignments``
    collection. ``uq_cfa_control_sub_function`` guarantees at most one row.
    """
    result = await db.execute(
        select(ControlFunctionAssignment).where(
            ControlFunctionAssignment.control_id == control_id,
            ControlFunctionAssignment.organization_id == organization_id,
            ControlFunctionAssignment.sub_function == sub_function,
        )
    )
    return result.scalar_one_or_none()


async def _source_sub_function(
    db: AsyncSession, derived: ControlFunctionAssignment
) -> FairCamSubFunction | None:
    """The sub-function of the row a derived row links to (same control only).

    None when the link is NULL or does not resolve to a row of the same control —
    callers treat that as "no recoverable source" rather than raising, so cleanup
    paths stay total.
    """
    if derived.derived_from_assignment_id is None:
        return None
    result = await db.execute(
        select(ControlFunctionAssignment.sub_function).where(
            ControlFunctionAssignment.id == derived.derived_from_assignment_id,
            ControlFunctionAssignment.control_id == derived.control_id,
            ControlFunctionAssignment.organization_id == derived.organization_id,
        )
    )
    return result.scalar_one_or_none()


async def _delete_derived_rows_first(
    db: AsyncSession,
    control_id: uuid.UUID,
    *,
    organization_id: uuid.UUID,
    audit: AuditWriter,
    user_id: uuid.UUID | None,
    ip_address: str | None,
) -> FairCamSubFunction | None:
    """Delete the control's derived row (if any) and FLUSH it, before any other delete.

    SQLAlchemy sorts the DELETEs of one flush by primary key, not by call order; with
    ``PRAGMA foreign_keys=ON`` a surviving derived row's ``ON DELETE SET NULL`` would
    violate ``ck_cfa_virtual_requires_derived`` whenever its source row is deleted
    first. Every path that deletes assignment rows calls this BEFORE it deletes any.

    Writes the ``.delete`` audit row (``[before, after]`` pairs) before the row leaves
    the session, flushes, and returns the removed row's SOURCE sub-function (None when
    there was no derived row) so the caller can re-link against the new source row.
    """
    derived = await _row_by_sub_function(db, control_id, organization_id, _DSC_CORR)
    if derived is None:
        return None
    source_sf = await _source_sub_function(db, derived)
    await audit.log(
        organization_id=organization_id,
        entity_type="control_function_assignment",
        entity_id=derived.id,
        action="control_function_assignment.delete",
        changes={
            "sub_function": [_DSC_CORR.value, None],
            "derived_from_sub_function": [source_sf.value if source_sf else None, None],
        },
        user_id=user_id,
        ip_address=ip_address,
    )
    await db.delete(derived)
    await db.flush()
    return source_sf


async def _sync_derived_row(
    db: AsyncSession,
    control_id: uuid.UUID,
    organization_id: uuid.UUID,
    source_sf: FairCamSubFunction | None,
    *,
    confirm: bool,
    audit: AuditWriter,
    user_id: uuid.UUID | None,
    ip_address: str | None,
) -> None:
    """Make the control's derived row match ``source_sf`` (create / re-point / keep).

    Called AFTER the assignment merge (or row copy), so the source row is found by an
    explicit ``select`` on (control_id, sub_function). ``source_sf is None`` means "no
    link": any leftover derived row is removed (callers that delete source rows have
    already called ``_delete_derived_rows_first``).

    ``confirm=True`` (every analyst form save): the derived row follows the authoring
    act, so ``confirmed_by_user_at`` / ``measured_by`` / ``measured_at`` = now — the
    "save = confirm" convention every saved row gets — with a ``.confirm`` audit row
    when the row already existed (the ``.create`` payload carries the confirmation
    for a new row). ``confirm=False`` (``duplicate_control``, ``apply_resync``): not an
    authoring act, so the row is written / left unconfirmed like every other row those
    paths write.

    Audit (existing ``[before, after]`` convention): create ->
    ``.create`` ``{sub_function, derived_from_sub_function, confirmed_by_user_at}``;
    re-point -> ``.update`` ``{derived_from_sub_function: [old, new]}``.
    """
    if source_sf is None:
        await _delete_derived_rows_first(
            db,
            control_id,
            organization_id=organization_id,
            audit=audit,
            user_id=user_id,
            ip_address=ip_address,
        )
        return

    source = await _row_by_sub_function(db, control_id, organization_id, source_sf)
    if source is None:
        raise ValueError(
            f"cannot link Correcting Misaligned Decisions: the control has no {source_sf.value} row"
        )
    now = datetime.now(UTC)
    derived = await _row_by_sub_function(db, control_id, organization_id, _DSC_CORR)

    if derived is None:
        derived = ControlFunctionAssignment(
            control_id=control_id,
            organization_id=organization_id,
            sub_function=_DSC_CORR,
            # Fail-closed sentinels: never inputs, never displayed (spec §3.1).
            capability_value=None,
            coverage=0.0,
            reliability=0.0,
            derived_from_assignment_id=source.id,
            confirmed_by_user_at=now if confirm else None,
            measured_by=user_id if confirm else None,
            measured_at=now if confirm else None,
        )
        db.add(derived)
        await db.flush()
        await audit.log(
            organization_id=organization_id,
            entity_type="control_function_assignment",
            entity_id=derived.id,
            action="control_function_assignment.create",
            changes={
                "sub_function": [None, _DSC_CORR.value],
                "derived_from_sub_function": [None, source_sf.value],
                "confirmed_by_user_at": [None, now.isoformat() if confirm else None],
            },
            user_id=user_id,
            ip_address=ip_address,
        )
        return

    if derived.derived_from_assignment_id != source.id:
        old_sf = await _source_sub_function(db, derived)
        derived.derived_from_assignment_id = source.id
        await audit.log(
            organization_id=organization_id,
            entity_type="control_function_assignment",
            entity_id=derived.id,
            action="control_function_assignment.update",
            changes={
                "derived_from_sub_function": [old_sf.value if old_sf else None, source_sf.value]
            },
            user_id=user_id,
            ip_address=ip_address,
        )
    if confirm:
        prev_confirmed = derived.confirmed_by_user_at
        derived.confirmed_by_user_at = now
        derived.measured_by = user_id
        derived.measured_at = now
        await audit.log(
            organization_id=organization_id,
            entity_type="control_function_assignment",
            entity_id=derived.id,
            action="control_function_assignment.confirm",
            changes={
                "confirmed_by_user_at": [
                    prev_confirmed.isoformat() if prev_confirmed else None,
                    now.isoformat(),
                ]
            },
            user_id=user_id,
            ip_address=ip_address,
        )


async def create_control(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    form: ControlForm,
    ip_address: str | None = None,
    dsc_corr_source: FairCamSubFunction | None = None,
) -> Control:
    """Create a Control row + ControlFunctionAssignment rows transactionally.

    Uses M5 explicit-unpack pattern to avoid passing assignments as a Control
    ORM constructor kwarg. PR kappa: multiple assignments now permitted (spec §6.1).
    User-submitted assignments are confirmed (confirmed_by_user_at = now);
    importer-created assignments leave confirmed_by_user_at = NULL. (spec §8.1)

    ``dsc_corr_source`` (#268) names the submitted sub-function the derived
    Correcting Misaligned Decisions row is linked to (``DSC_CORR_LINK_SOURCES``);
    None = no derived row. ``ValueError`` (nothing written) on an invalid request.
    """
    validate_dsc_corr_link([a.model_dump() for a in form.assignments], dsc_corr_source)
    # M5: explicit unpack — never **form.model_dump() on Control constructor
    form_data: dict[str, Any] = form.model_dump()
    assignments_data: list[dict[str, Any]] = form_data.pop("assignments")

    control = Control(organization_id=org_id, created_by=user_id, **form_data)
    db.add(control)
    await db.flush()  # populate control.id

    now = datetime.now(UTC)
    audit = AuditWriter(db)

    for assignment_data in assignments_data:
        # A client-supplied derived_from_assignment_id was already refused by
        # validate_dsc_corr_link (the derived row is written below, server-side).
        # Drop DTO-side measurement fields — service overrides them with
        # current values for user-submitted forms (spec §8.1, §4.8).
        # Without this pop, **assignment_data conflicts with the explicit
        # confirmed_by_user_at / measured_by / measured_at kwargs below.
        assignment_data.pop("confirmed_by_user_at", None)
        assignment_data.pop("measured_by", None)
        assignment_data.pop("measured_at", None)
        assignment = ControlFunctionAssignment(
            control_id=control.id,
            organization_id=org_id,
            # User-submitted forms count as confirmed (spec §8.1, §4.8)
            confirmed_by_user_at=now,
            measured_by=user_id,
            measured_at=now,
            **assignment_data,
        )
        db.add(assignment)
        await db.flush()  # populate assignment.id
        await audit.log(
            organization_id=org_id,
            entity_type="control_function_assignment",
            entity_id=assignment.id,
            action="control_function_assignment.create",
            changes={
                "sub_function": [None, assignment.sub_function.value],
                "capability_value": [None, assignment_data.get("capability_value")],
                "coverage": [None, assignment_data.get("coverage")],
                "reliability": [None, assignment_data.get("reliability")],
            },
            user_id=user_id,
            ip_address=ip_address,
        )

    # Derived Correcting Misaligned Decisions row, after its source row exists (a new
    # control has no derived row to clean up, so "no link" needs no query).
    if dsc_corr_source is not None:
        await _sync_derived_row(
            db,
            control.id,
            org_id,
            dsc_corr_source,
            confirm=True,
            audit=audit,
            user_id=user_id,
            ip_address=ip_address,
        )

    await audit.log(
        organization_id=org_id,
        entity_type="control",
        entity_id=control.id,
        action="control.create",
        # `domain` key removed (issue #90): domain is derived from
        # assignments, not directly authored, so no [before, after] makes
        # sense at the Control level. Assignment-level audit rows
        # (control_function_assignment.{create,update,delete,confirm})
        # remain the source of truth for domain shifts.
        changes={"name": [None, control.name]},
        user_id=user_id,
        ip_address=ip_address,
    )
    return control


async def update_control(
    db: AsyncSession,
    *,
    control: Control,
    user_id: uuid.UUID | None,
    form: ControlForm,
    ip_address: str | None = None,
    dsc_corr_source: FairCamSubFunction | None = None,
) -> Control:
    """Update a Control row and diff-detect changes on assignment fields.

    Performs a three-way merge on the NON-DERIVED assignments:
      - new sub_functions not in DB -> inserted
      - sub_functions in both -> updated if any of (capability_value, coverage,
        reliability) changed
      - sub_functions in DB absent from form -> deleted

    PR λ Q5(a) save = confirm: confirmed_by_user_at is set to now() for every
    assignment in the form (insert OR update branches), regardless of prior
    state. Supersedes the prior PR iota "OQ4/§4.8" rule. /controls/maintenance
    survives as the bulk-confirm-without-editing surface for backfills.

    The derived Correcting Misaligned Decisions row (#268) is NOT part of the merge:
    ``dsc_corr_source`` is the full desired link (None = no link — the DEFAULT, so a
    caller that omits it clears the link). The derived row is deleted and flushed
    BEFORE the merge when it must go (link cleared, or its source row is leaving the
    submission), then ``_sync_derived_row`` creates / re-points / keeps it AFTER the
    merge (and confirms it: save = confirm). ``ValueError`` — raised before any
    mutation — on an invalid request.
    """
    validate_dsc_corr_link([a.model_dump() for a in form.assignments], dsc_corr_source)
    form_data: dict[str, Any] = form.model_dump()
    assignments_data: list[dict[str, Any]] = form_data.pop("assignments")

    # Diff control-level fields
    changes: dict[str, list[object]] = {}
    for k, v in form_data.items():
        prev = getattr(control, k)
        prev_val = prev.value if hasattr(prev, "value") else prev
        new_val = v.value if hasattr(v, "value") else v
        if prev_val != new_val:
            changes[k] = [prev_val, new_val]
            setattr(control, k, v)

    audit = AuditWriter(db)

    if changes:
        await audit.log(
            organization_id=control.organization_id,
            entity_type="control",
            entity_id=control.id,
            action="control.update",
            changes=changes,
            user_id=user_id,
            ip_address=ip_address,
        )

    # Three-way merge on the NON-DERIVED assignments keyed by sub_function; the
    # derived row is handled separately (delete-first below, sync after the merge).
    existing_by_subfn: dict[str, ControlFunctionAssignment] = {
        a.sub_function.value: a
        for a in (control.assignments or [])
        if a.sub_function is not _DSC_CORR
    }
    incoming_subfns: set[str] = set()
    now = datetime.now(UTC)

    # The derived row goes FIRST (and is flushed) when its link is cleared or its
    # source row is leaving the submission — before the merge deletes any other row
    # (primary-key-sorted DELETEs + FK SET NULL vs ck_cfa_virtual_requires_derived).
    # A source that stays is handled by _sync_derived_row (keep / re-point).
    current_derived = await _row_by_sub_function(db, control.id, control.organization_id, _DSC_CORR)
    if current_derived is not None:
        current_source = await _source_sub_function(db, current_derived)
        submitted_sfs = {a.sub_function for a in form.assignments}
        if dsc_corr_source is None or current_source not in submitted_sfs:
            await _delete_derived_rows_first(
                db,
                control.id,
                organization_id=control.organization_id,
                audit=audit,
                user_id=user_id,
                ip_address=ip_address,
            )

    for ad in assignments_data:
        subfn_val: str = (
            ad["sub_function"].value if hasattr(ad["sub_function"], "value") else ad["sub_function"]
        )
        incoming_subfns.add(subfn_val)

        if subfn_val in existing_by_subfn:
            # UPDATE branch — diff and patch field changes
            existing = existing_by_subfn[subfn_val]
            assign_changes: dict[str, list[object]] = {}
            for field in ("capability_value", "coverage", "reliability"):
                old_v = getattr(existing, field)
                new_v = ad.get(field)
                if old_v != new_v:
                    assign_changes[field] = [old_v, new_v]
                    setattr(existing, field, new_v)
            if assign_changes:
                # Issue #129 T6: distinguish the non-NULL -> NULL transition on
                # capability_value with a dedicated `.clear` action verb. NULL
                # engages the model-midpoint fallback (opeff = 0.5 by
                # construction at t = tau * ln(2) for ELAPSED_TIME units;
                # disables the per-event subtractor for CURRENCY), so the audit
                # trail needs a greppable marker for silent ALE-model
                # degradations. NULL -> non-NULL (populate) and other field
                # diffs continue under `.update` unchanged.
                cap_change = assign_changes.get("capability_value")
                if cap_change is not None and cap_change[0] is not None and cap_change[1] is None:
                    cfa_audit_action = "control_function_assignment.clear"
                else:
                    cfa_audit_action = "control_function_assignment.update"
                await audit.log(
                    organization_id=control.organization_id,
                    entity_type="control_function_assignment",
                    entity_id=existing.id,
                    action=cfa_audit_action,
                    changes=assign_changes,
                    user_id=user_id,
                    ip_address=ip_address,
                )
            # Q5(a) save = confirm: bump confirmed_by_user_at on every save.
            # Emit confirm audit row regardless of whether other fields changed.
            # This preserves the audit invariant that EVERY confirmed_by_user_at
            # transition is auditable (matching confirm_assignment's pattern).
            prev_confirmed = existing.confirmed_by_user_at
            existing.confirmed_by_user_at = now
            existing.measured_by = user_id
            existing.measured_at = now
            await audit.log(
                organization_id=control.organization_id,
                entity_type="control_function_assignment",
                entity_id=existing.id,
                action="control_function_assignment.confirm",
                changes={
                    "confirmed_by_user_at": [
                        prev_confirmed.isoformat() if prev_confirmed else None,
                        now.isoformat(),
                    ]
                },
                user_id=user_id,
                ip_address=ip_address,
            )
        else:
            # INSERT branch — new assignment, also confirmed-on-save (Q5a)
            # Pop the 4 server-set fields before constructing (defense-in-depth).
            # PARANOID-REVIEW M5/M6 NOTE: payload INTENTIONALLY EXPANDED beyond
            # the existing create_control single-key {sub_function} pattern to
            # include capability_value/coverage/reliability — for parity with the
            # confirm_assignment audit pattern AND so Q5(a) audit invariant
            # (every confirmed_by_user_at transition is auditable) holds on the
            # INSERT path. Includes confirmed_by_user_at explicitly. PR rho may
            # backport this expansion to create_control for consistency.
            ad.pop("confirmed_by_user_at", None)
            ad.pop("measured_by", None)
            ad.pop("measured_at", None)
            # validate_dsc_corr_link already refused any non-NULL
            # derived_from_assignment_id; defense-in-depth, drop the (None) key here too.
            ad.pop("derived_from_assignment_id", None)
            new_assignment = ControlFunctionAssignment(
                control_id=control.id,
                organization_id=control.organization_id,
                confirmed_by_user_at=now,
                measured_by=user_id,
                measured_at=now,
                **ad,
            )
            db.add(new_assignment)
            await db.flush()
            await audit.log(
                organization_id=control.organization_id,
                entity_type="control_function_assignment",
                entity_id=new_assignment.id,
                action="control_function_assignment.create",
                changes={
                    "sub_function": [None, new_assignment.sub_function.value],
                    "capability_value": [None, new_assignment.capability_value],
                    "coverage": [None, new_assignment.coverage],
                    "reliability": [None, new_assignment.reliability],
                    "confirmed_by_user_at": [None, now.isoformat()],
                },
                user_id=user_id,
                ip_address=ip_address,
            )

    # Delete path: assignments in DB absent from form.
    # Audit log lands BEFORE db.delete so the audit captures the to-be-deleted
    # entity_id while the row is still in the session — same pattern as
    # soft_delete_control above.
    for subfn_val, existing in existing_by_subfn.items():
        if subfn_val not in incoming_subfns:
            await audit.log(
                organization_id=control.organization_id,
                entity_type="control_function_assignment",
                entity_id=existing.id,
                action="control_function_assignment.delete",
                changes={"sub_function": [subfn_val, None]},
                user_id=user_id,
                ip_address=ip_address,
            )
            await db.delete(existing)

    # Derived Correcting Misaligned Decisions row, after the merge (the source row
    # now exists — possibly inserted just above). The helper's first query autoflushes
    # the deletes above. "No link" needs nothing here: a derived row that existed was
    # already deleted (and flushed) before the merge.
    if dsc_corr_source is not None:
        await _sync_derived_row(
            db,
            control.id,
            control.organization_id,
            dsc_corr_source,
            confirm=True,
            audit=audit,
            user_id=user_id,
            ip_address=ip_address,
        )

    return control


async def duplicate_control(
    db: AsyncSession,
    *,
    control: Control,
    user_id: uuid.UUID | None,
    ip_address: str | None = None,
) -> Control:
    """Clone a Control + its assignments into a new draft.

    The clone:
      - gets a new UUID
      - is named "<source.name> (copy)"
      - lands in status = DRAFT (regardless of source.status)
      - clones every assignment with NEW IDs and confirmed_by_user_at = NULL
        (clone is not an authoring act; analyst confirms via subsequent /edit)
      - a derived Correcting Misaligned Decisions row (#268) is NOT copied as a row
        (its link would point at the ORIGINAL's source): it is recreated after the
        copy, linked to the CLONE's row of the same source sub-function, unconfirmed

    Audit:
      - one control.duplicate row keyed to the new control id
      - one control_function_assignment.create row per cloned assignment
        (the recreated derived row's carries ``confirmed_by_user_at: [None, None]``)

    Implementation note: uses inspect(Control).columns to enumerate fields
    so that future Control field additions don't silently drop on clone.
    Fields excluded: structural (id, timestamps, created_by) — these are
    re-set per clone semantics.
    """
    from sqlalchemy.inspection import inspect as sa_inspect

    org_id = control.organization_id
    audit = AuditWriter(db)

    # Reflect Control columns and copy all except structural fields
    excluded = {"id", "created_at", "updated_at", "created_by"}
    base_kwargs: dict[str, Any] = {
        col.key: getattr(control, col.key)
        for col in sa_inspect(Control).columns
        if col.key not in excluded
    }
    # Override per clone semantics
    base_kwargs["name"] = f"{control.name} (copy)"
    base_kwargs["status"] = EntityStatus.DRAFT
    base_kwargs["created_by"] = user_id
    # JSON dict / list columns: deep-copy to avoid alias mutation
    for k in ("compliance_mappings", "library_pin", "adopted_snapshot"):
        if isinstance(base_kwargs.get(k), dict):
            base_kwargs[k] = dict(base_kwargs[k])
    for k in (
        "nist_csf_functions",
        "iso_27001_domains",
        "skill_requirements",
        "technology_dependencies",
        "applicable_industries",
        "applicable_org_sizes",
    ):
        if isinstance(base_kwargs.get(k), list):
            base_kwargs[k] = list(base_kwargs[k])

    new_control = Control(**base_kwargs)
    db.add(new_control)
    await db.flush()

    source_derived_sf: FairCamSubFunction | None = None
    for src_a in control.assignments or []:
        if src_a.sub_function is _DSC_CORR:
            # Derived row: recreated below against the clone's own source row.
            source_derived_sf = await _source_sub_function(db, src_a)
            if source_derived_sf is None:
                logger.warning(
                    "duplicate_control: control %s has a derived %s row whose link does not "
                    "resolve to a row of the same control; the clone gets no derived row",
                    control.id,
                    _DSC_CORR.value,
                )
            continue
        new_assignment = ControlFunctionAssignment(
            control_id=new_control.id,
            organization_id=org_id,
            sub_function=src_a.sub_function,
            capability_value=src_a.capability_value,
            coverage=src_a.coverage,
            reliability=src_a.reliability,
            confirmed_by_user_at=None,  # clone is NOT an authoring act
            measured_by=None,
            measured_at=None,
        )
        db.add(new_assignment)
        await db.flush()
        await audit.log(
            organization_id=org_id,
            entity_type="control_function_assignment",
            entity_id=new_assignment.id,
            action="control_function_assignment.create",
            changes={
                "sub_function": [None, new_assignment.sub_function.value],
                "capability_value": [None, new_assignment.capability_value],
                "coverage": [None, new_assignment.coverage],
                "reliability": [None, new_assignment.reliability],
            },
            user_id=user_id,
            ip_address=ip_address,
        )

    if source_derived_sf is not None:
        await _sync_derived_row(
            db,
            new_control.id,
            org_id,
            source_derived_sf,
            confirm=False,  # clone is NOT an authoring act
            audit=audit,
            user_id=user_id,
            ip_address=ip_address,
        )

    await audit.log(
        organization_id=org_id,
        entity_type="control",
        entity_id=new_control.id,
        action="control.duplicate",
        changes={
            "source_control_id": str(control.id),
            "name": [None, new_control.name],
        },
        user_id=user_id,
        ip_address=ip_address,
    )

    return new_control


def _build_adopted_snapshot(entry: Any, assignments: Any) -> dict[str, Any]:
    """#438 — the verbatim library-entry values cloned by ``adopt_from_library``.

    Field set mirrors EXACTLY what the adopt clone writes onto the Control
    (same D1 dedup rules: cis_safeguards inside compliance_mappings, no shadow
    nist/iso copies) plus the per-assignment defaults, so the re-sync diff can
    compare like-for-like on every cloned field. JSON-safe scalars only.
    """
    return {
        "entry_id": str(entry.id),
        "version": entry.version,
        "name": entry.name,
        "description": entry.description,
        "type": entry.control_type.value,
        "annual_cost": str(
            entry.reference_annual_cost if entry.reference_annual_cost is not None else Decimal("0")
        ),
        "nist_csf_functions": list(entry.nist_csf_subcategories),
        "iso_27001_domains": list(entry.iso_27001_controls),
        "compliance_mappings": {
            **entry.compliance_mappings,
            "cis_safeguards": list(entry.cis_safeguards),
        },
        "applicable_industries": list(entry.applicable_industries),
        "applicable_org_sizes": list(entry.applicable_org_sizes),
        "assignments": [
            {
                "sub_function": a.sub_function.value,
                "capability": float(a.capability_default)
                if a.capability_default is not None
                else None,
                "coverage": float(a.coverage_default) if a.coverage_default is not None else None,
                "reliability": float(a.reliability_default)
                if a.reliability_default is not None
                else None,
            }
            for a in assignments
        ],
    }


async def adopt_from_library(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    entry_id: uuid.UUID,
    version: int | None,
    ip_address: str | None = None,
) -> Control:
    """Clone-snapshot a published ControlLibraryEntry into a new editable org Control.

    The new control gets ``source=LIBRARY_DERIVED`` and a ``library_pin`` recording
    the source entry id + version. Assignments are copied UNCONFIRMED
    (``confirmed_by_user_at=NULL``) — the org reviews/tunes/confirms (the "manual
    mapping"). One ``control.adopt_from_library`` audit row is emitted in lieu of a
    ``control.create`` row (mirrors how ``duplicate_control`` emits ``control.duplicate``);
    no ``control.create`` precedes it for library-derived controls.

    D1 tag mapping (deduped, Arch-I2): ``nist_csf_subcategories`` -> the named
    ``nist_csf_functions`` column, ``iso_27001_controls`` -> the named
    ``iso_27001_domains`` column, and ``cis_safeguards`` (no named Control column)
    stashed into ``compliance_mappings`` alongside the entry's own
    ``compliance_mappings``. nist/iso are NOT re-stashed into ``compliance_mappings``
    (they live in named columns; a shadow copy desyncs on edit).

    TOCTOU note (Arch-I3): no ``with_for_update`` re-fetch — entries are seed-only
    in P2b (no CRUD), so the deprecate-between-resolve-and-insert race cannot occur.
    The seed schema's ``reject_virtual`` already blocks the only ``derived_from``-
    requiring sub_function at seed time, so the ``derived_from_assignment_id`` guard
    (Arch-I1b) is unnecessary here.
    """
    from idraa.services.control_library import ControlLibraryService

    svc = ControlLibraryService(db)
    entry = await svc.get_published(entry_id, version)
    if entry is None:
        # Reuse the existing error; the route maps it (and LibraryEntryStatusError)
        # to a constant 404 so status-vs-existence isn't an oracle (Sec-I1).
        raise LibraryEntryNotFoundError(entry_id)

    # Load the entry's assignments (composite key on id + version).
    assignments = (
        (
            await db.execute(
                select(ControlLibraryEntryAssignment).where(
                    ControlLibraryEntryAssignment.library_entry_id == entry.id,
                    ControlLibraryEntryAssignment.library_entry_version == entry.version,
                )
            )
        )
        .scalars()
        .all()
    )

    control = Control(
        organization_id=org_id,
        created_by=user_id,
        name=entry.name,
        description=entry.description,
        type=entry.control_type,
        annual_cost=(
            entry.reference_annual_cost if entry.reference_annual_cost is not None else Decimal("0")
        ),
        nist_csf_functions=list(entry.nist_csf_subcategories),  # D1 (named column)
        iso_27001_domains=list(entry.iso_27001_controls),  # D1 (named column)
        # D1 deduped (Arch-I2): stash ONLY cis_safeguards (no named Control column)
        # + the entry's own compliance_mappings. Do NOT re-stash nist/iso — they
        # already live in named columns; a shadow copy desyncs on edit.
        compliance_mappings={
            **entry.compliance_mappings,
            "cis_safeguards": list(entry.cis_safeguards),
        },
        applicable_industries=list(entry.applicable_industries),
        applicable_org_sizes=list(entry.applicable_org_sizes),
        source=ControlSource.LIBRARY_DERIVED,
        library_pin={"entry_id": str(entry.id), "version": entry.version},
        # #438: verbatim as-adopted copy (never touched by user edits) so a
        # future re-sync can separate "library changed" from "analyst edited".
        adopted_snapshot=_build_adopted_snapshot(entry, assignments),
    )
    db.add(control)
    await db.flush()

    for a in assignments:
        db.add(
            ControlFunctionAssignment(
                control_id=control.id,
                organization_id=org_id,
                sub_function=a.sub_function,
                capability_value=a.capability_default,
                coverage=a.coverage_default,
                reliability=a.reliability_default,
                confirmed_by_user_at=None,  # org confirms via subsequent /edit
            )
        )
    await db.flush()

    await AuditWriter(db).log(
        organization_id=org_id,
        entity_type="control",
        entity_id=control.id,
        action="control.adopt_from_library",
        changes={
            "entry_id": [None, str(entry.id)],
            "version": [None, entry.version],
            "source": [None, ControlSource.LIBRARY_DERIVED.value],
        },
        user_id=user_id,
        ip_address=ip_address,
    )
    return control


async def soft_delete_control(
    db: AsyncSession,
    control: Control,
    *,
    user_id: uuid.UUID | None,
    ip_address: str | None = None,
) -> None:
    """Soft-delete: set status=DELETED. Assignment rows cascade via FK ON DELETE CASCADE."""
    prev = control.status.value
    control.status = EntityStatus.DELETED
    await AuditWriter(db).log(
        organization_id=control.organization_id,
        entity_type="control",
        entity_id=control.id,
        action="control.delete",
        changes={"status": [prev, EntityStatus.DELETED.value]},
        user_id=user_id,
        ip_address=ip_address,
    )


async def count_unconfirmed_assignments(db: AsyncSession, *, control_id: uuid.UUID) -> int:
    """Return the number of ControlFunctionAssignment rows with confirmed_by_user_at IS NULL.

    Used by list and detail views to surface the backfill warning (Decision 8, spec §4.8).
    Backfilled rows from the migration have confirmed_by_user_at=NULL until an analyst
    explicitly calls POST /controls/{id}/assignments/{assignment_id}/confirm.
    """
    result = await db.execute(
        select(func.count())
        .select_from(ControlFunctionAssignment)
        .where(
            ControlFunctionAssignment.control_id == control_id,
            ControlFunctionAssignment.confirmed_by_user_at.is_(None),
        )
    )
    return result.scalar_one()


async def confirm_assignment(
    db: AsyncSession,
    *,
    assignment: ControlFunctionAssignment,
    user_id: uuid.UUID | None,
    ip_address: str | None = None,
) -> ControlFunctionAssignment:
    """Set confirmed_by_user_at = now(), measured_by = user_id, measured_at = now().

    Per-assignment confirmation path, used by the bulk-confirm-without-editing
    surface at /controls/maintenance. Form save (update_control) ALSO sets
    confirmed_by_user_at per PR lambda Q5(a) save = confirm; the two paths
    coexist.

    Re-confirmation is permitted (e.g., after periodic effectiveness review).
    Captures prior_confirmed and prior_measured_by BEFORE mutation so
    re-confirms produce a non-misleading audit trail — diff shows actual
    previous values rather than [None, now]. (M-NEW3, spec §5.3)

    Idempotent semantics: if already confirmed at the same instant (in tests),
    the audit row still lands with the captured prior values.

    Derived Correcting Misaligned Decisions row (#268): it has no inputs of its own,
    so confirming it directly is a NO-OP (no write, no audit row), and confirming
    its SOURCE row also confirms it (its own ``.confirm`` audit row) — confirming
    every row the maintenance page lists (which never lists a derived row) thus
    leaves a duplicated / re-synced control fully confirmed.
    """
    if assignment.derived_from_assignment_id is not None:
        return assignment
    now = datetime.now(UTC)
    audit = AuditWriter(db)
    await _confirm_row(audit, assignment, user_id=user_id, ip_address=ip_address, now=now)
    derived_rows = (
        (
            await db.execute(
                select(ControlFunctionAssignment).where(
                    ControlFunctionAssignment.derived_from_assignment_id == assignment.id,
                    ControlFunctionAssignment.control_id == assignment.control_id,
                    ControlFunctionAssignment.organization_id == assignment.organization_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for derived in derived_rows:
        await _confirm_row(audit, derived, user_id=user_id, ip_address=ip_address, now=now)
    return assignment


async def _confirm_row(
    audit: AuditWriter,
    assignment: ControlFunctionAssignment,
    *,
    user_id: uuid.UUID | None,
    ip_address: str | None,
    now: datetime,
) -> None:
    """Stamp one row confirmed and write its own ``.confirm`` audit row."""
    prior_confirmed = assignment.confirmed_by_user_at
    prior_measured_by = assignment.measured_by
    assignment.confirmed_by_user_at = now
    assignment.measured_by = user_id
    assignment.measured_at = now
    await audit.log(
        organization_id=assignment.organization_id,
        entity_type="control_function_assignment",
        entity_id=assignment.id,
        action="control_function_assignment.confirm",
        changes={
            "confirmed_by_user_at": [
                prior_confirmed.isoformat() if prior_confirmed else None,
                now.isoformat(),
            ],
            "measured_by": [
                str(prior_measured_by) if prior_measured_by else None,
                str(user_id) if user_id else None,
            ],
        },
        user_id=user_id,
        ip_address=ip_address,
    )
