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
  - B-NEW3 / Decision 9: raise ValueError if derived_from_assignment_id is
    not None — reserved-but-unused in PR iota. Enforcement lives HERE (not on DTO)
    because the DTO is shared with _snapshot_control_v2 and Phase 2 reads.
  - confirmed_by_user_at is set on wizard/form-submitted assignments but NOT
    on importer assignments (which surface in the unconfirmed-warning UX).
  - confirm_assignment: captures prior values before mutation so re-confirms
    produce non-misleading audit diffs (M-NEW3, spec §5.3).
"""

from __future__ import annotations

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
    ControlDomain,
    ControlSource,
    EntityStatus,
    sub_functions_for_domain,
)
from idraa.schemas.control import ControlForm
from idraa.services.audit import AuditWriter


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


async def get_control(db: AsyncSession, control_id: uuid.UUID) -> Control | None:
    return await db.get(Control, control_id)


async def create_control(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    form: ControlForm,
    ip_address: str | None = None,
) -> Control:
    """Create a Control row + ControlFunctionAssignment rows transactionally.

    Uses M5 explicit-unpack pattern to avoid passing assignments as a Control
    ORM constructor kwarg. PR kappa: multiple assignments now permitted (spec §6.1).
    User-submitted assignments are confirmed (confirmed_by_user_at = now);
    importer-created assignments leave confirmed_by_user_at = NULL. (spec §8.1)
    """
    # M5: explicit unpack — never **form.model_dump() on Control constructor
    form_data: dict[str, Any] = form.model_dump()
    assignments_data: list[dict[str, Any]] = form_data.pop("assignments")

    control = Control(organization_id=org_id, created_by=user_id, **form_data)
    db.add(control)
    await db.flush()  # populate control.id

    now = datetime.now(UTC)
    audit = AuditWriter(db)

    for assignment_data in assignments_data:
        # Decision 9 / B-NEW3: derived_from is reserved-but-unused in PR iota.
        # Enforcement lives here (service layer), not on DTO.
        if assignment_data.get("derived_from_assignment_id") is not None:
            raise ValueError(
                "derived_from_assignment_id is reserved-but-unused in PR iota. "
                "Phase 2 removes this guard when computed-virtual rows are introduced. "
                "(Decision 9, B-NEW3)"
            )
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
) -> Control:
    """Update a Control row and diff-detect changes on assignment fields.

    Performs a three-way merge on assignments:
      - new sub_functions not in DB -> inserted
      - sub_functions in both -> updated if any of (capability_value, coverage,
        reliability) changed
      - sub_functions in DB absent from form -> deleted

    PR λ Q5(a) save = confirm: confirmed_by_user_at is set to now() for every
    assignment in the form (insert OR update branches), regardless of prior
    state. Supersedes the prior PR iota "OQ4/§4.8" rule. /controls/maintenance
    survives as the bulk-confirm-without-editing surface for backfills.
    """
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

    # Three-way merge on assignments keyed by sub_function
    existing_by_subfn: dict[str, ControlFunctionAssignment] = {
        a.sub_function.value: a for a in (control.assignments or [])
    }
    incoming_subfns: set[str] = set()
    now = datetime.now(UTC)

    for ad in assignments_data:
        if ad.get("derived_from_assignment_id") is not None:
            raise ValueError(
                "derived_from_assignment_id is reserved-but-unused in PR iota. (Decision 9)"
            )
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
            # derived_from_assignment_id is reserved-but-unused (Decision 9 / B-NEW3)
            # Service raises ValueError if non-NULL — but defense-in-depth, drop here too.
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

    Audit:
      - one control.duplicate row keyed to the new control id
      - one control_function_assignment.create row per cloned assignment

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

    for src_a in control.assignments or []:
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
    """
    prior_confirmed = assignment.confirmed_by_user_at
    prior_measured_by = assignment.measured_by
    now = datetime.now(UTC)
    assignment.confirmed_by_user_at = now
    assignment.measured_by = user_id
    assignment.measured_at = now
    await AuditWriter(db).log(
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
    return assignment
