"""#438 — library re-sync for adopted controls.

An adopted control pins the library entry version it was cloned from
(``Control.library_pin``). When the entry is re-curated (version bump), the
control is STALE: its values reflect the old curation. This module provides

- ``resync_info``: staleness detection + a field-level diff for the review UI.
  Diff quality is two-tier (owner ruling on #438):
  * ``adopted_snapshot`` present (adoptions from migration c9e4f7a2b8d1 on) →
    clean 3-way diff — for each field: the as-adopted value, the control's
    current value (user edits visible), and the current entry value.
  * snapshot absent (legacy adoptions) → coarse diff, explicitly labeled:
    control-now vs entry-now conflates analyst edits with library changes.
- ``apply_resync``: overwrite the adopt-cloned fields + assignments with the
  current entry values (the diff view is the consent step), re-pin, re-snapshot,
  audit, and flag affected COMPLETED runs stale (``flag_runs_stale_for_control``
  — the #437 plumbing this module finally wires a caller into).

Assignments are REPLACED on re-sync (cloned UNCONFIRMED, same as adoption):
the re-curated entry's function set is the new baseline; per-assignment user
tuning belongs to the pre-sync state shown in the diff.

A derived Correcting Misaligned Decisions row (#268) is not an entry assignment (a
library entry cannot claim ``dsc_corr_misaligned``) and holds only a LINK to another
row of the control, which the replacement invalidates. It is therefore excluded from
the assignment diff and handled separately: ``apply_resync`` deletes it and flushes
it FIRST (before any other delete — SQLAlchemy sorts one flush's DELETEs by primary
key, and a surviving derived row's FK ``SET NULL`` violates
``ck_cfa_virtual_requires_derived``), replaces the entry rows, then re-links a NEW
derived row (unconfirmed) against the new source row if the entry still carries the
source sub-function, else drops it — both audited. ``ResyncInfo.derived_fate``
reports which, so the consent page can say so.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.control_library import ControlLibraryEntry, ControlLibraryEntryAssignment
from idraa.models.enums import FairCamSubFunction
from idraa.services.audit import AuditWriter
from idraa.services.control_library import ControlLibraryService, flag_runs_stale_for_control


@dataclass
class ResyncFieldDiff:
    field: str
    adopted: Any  # as-adopted value (None when no snapshot — coarse mode)
    control_now: Any
    entry_now: Any
    user_modified: bool | None  # None in coarse mode (unknowable)
    library_changed: bool | None  # None in coarse mode


@dataclass
class ResyncInfo:
    pinned_version: int
    current_version: int
    stale: bool
    has_snapshot: bool
    entry: ControlLibraryEntry | None
    fields: list[ResyncFieldDiff] = field(default_factory=list)
    # per-sub-function assignment diff rows, same three-way semantics (derived
    # Correcting Misaligned Decisions rows are excluded — see derived_fate)
    assignments: list[ResyncFieldDiff] = field(default_factory=list)
    # #268: what applying the re-sync does to the control's derived row —
    # "relinked" (the entry still carries its source sub-function: a new derived row
    # is created against the new source row), "dropped" (it does not), or None (the
    # control has no derived row, or the control is not stale).
    derived_fate: Literal["relinked", "dropped"] | None = None


def _entry_field_values(entry: ControlLibraryEntry) -> dict[str, Any]:
    """The adopt-clone field set, shaped exactly like ``_build_adopted_snapshot``."""
    return {
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
    }


def _control_field_values(control: Control) -> dict[str, Any]:
    return {
        "name": control.name,
        "description": control.description,
        "type": control.type.value if hasattr(control.type, "value") else control.type,
        "annual_cost": str(control.annual_cost),
        "nist_csf_functions": list(control.nist_csf_functions or []),
        "iso_27001_domains": list(control.iso_27001_domains or []),
        "compliance_mappings": dict(control.compliance_mappings or {}),
        "applicable_industries": list(control.applicable_industries or []),
        "applicable_org_sizes": list(control.applicable_org_sizes or []),
    }


async def _entry_assignments(
    db: AsyncSession, entry: ControlLibraryEntry
) -> list[ControlLibraryEntryAssignment]:
    return list(
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


async def resync_info(db: AsyncSession, control: Control) -> ResyncInfo | None:
    """Staleness + diff for an adopted control; None for custom controls."""
    pin = control.library_pin
    if not pin:
        return None
    entry_id = uuid.UUID(str(pin["entry_id"]))
    pinned_version = int(pin["version"])

    svc = ControlLibraryService(db)
    entry = await svc.get_published(entry_id)  # latest published version
    if entry is None:
        # Entry unpublished/deleted — not stale in the re-sync sense; nothing
        # to sync TO. Surfaced as not-stale with entry None.
        return ResyncInfo(
            pinned_version=pinned_version,
            current_version=pinned_version,
            stale=False,
            has_snapshot=control.adopted_snapshot is not None,
            entry=None,
        )

    stale = entry.version > pinned_version
    info = ResyncInfo(
        pinned_version=pinned_version,
        current_version=entry.version,
        stale=stale,
        has_snapshot=control.adopted_snapshot is not None,
        entry=entry,
    )
    if not stale:
        return info

    snapshot = control.adopted_snapshot or {}
    entry_vals = _entry_field_values(entry)
    control_vals = _control_field_values(control)
    for key, entry_now in entry_vals.items():
        if key == "type":
            # apply_resync never re-types a control (curation-stable enum) —
            # surfacing a type diff the apply won't make would mislead the
            # review page (SWE review NTH).
            continue
        control_now = control_vals.get(key)
        adopted = snapshot.get(key) if info.has_snapshot else None
        if info.has_snapshot:
            user_modified = control_now != adopted
            library_changed = entry_now != adopted
        else:
            user_modified = None
            library_changed = None
        # Only surface rows where SOMETHING differs from the entry's new state
        # (or the user diverged from the adopted baseline).
        if control_now != entry_now or (info.has_snapshot and user_modified):
            info.fields.append(
                ResyncFieldDiff(
                    field=key,
                    adopted=adopted,
                    control_now=control_now,
                    entry_now=entry_now,
                    user_modified=user_modified,
                    library_changed=library_changed,
                )
            )

    # Assignment diff by sub_function value.
    entry_assign = {
        a.sub_function.value: {
            "capability": float(a.capability_default) if a.capability_default is not None else None,
            "coverage": float(a.coverage_default) if a.coverage_default is not None else None,
            "reliability": float(a.reliability_default)
            if a.reliability_default is not None
            else None,
        }
        for a in await _entry_assignments(db, entry)
    }
    # Explicit query — never rely on the relationship being pre-loaded (an
    # expired instance would lazy-load synchronously and raise MissingGreenlet
    # under the async engine).
    control_assignment_rows = list(
        (
            await db.execute(
                select(ControlFunctionAssignment).where(
                    ControlFunctionAssignment.control_id == control.id
                )
            )
        )
        .scalars()
        .all()
    )
    # The derived row carries only a link + sentinels (no inputs to diff): it is
    # reported through ``derived_fate`` instead of as a phantom assignment row.
    derived_row = next(
        (
            a
            for a in control_assignment_rows
            if a.sub_function is FairCamSubFunction.DSC_CORR_MISALIGNED
        ),
        None,
    )
    control_assign = {
        a.sub_function.value: {
            "capability": float(a.capability_value) if a.capability_value is not None else None,
            "coverage": float(a.coverage) if a.coverage is not None else None,
            "reliability": float(a.reliability) if a.reliability is not None else None,
        }
        for a in control_assignment_rows
        if a is not derived_row
    }
    if derived_row is not None:
        source_row = next(
            (a for a in control_assignment_rows if a.id == derived_row.derived_from_assignment_id),
            None,
        )
        info.derived_fate = (
            "relinked"
            if source_row is not None and source_row.sub_function.value in entry_assign
            else "dropped"
        )
    snap_assign = (
        {
            a["sub_function"]: {k: a.get(k) for k in ("capability", "coverage", "reliability")}
            for a in snapshot.get("assignments", [])
        }
        if info.has_snapshot
        else {}
    )
    for sub_fn in sorted(set(entry_assign) | set(control_assign) | set(snap_assign)):
        entry_now = entry_assign.get(sub_fn)
        control_now = control_assign.get(sub_fn)
        adopted = snap_assign.get(sub_fn) if info.has_snapshot else None
        if info.has_snapshot:
            user_modified = control_now != adopted
            library_changed = entry_now != adopted
        else:
            user_modified = None
            library_changed = None
        if control_now != entry_now or (info.has_snapshot and user_modified):
            info.assignments.append(
                ResyncFieldDiff(
                    field=sub_fn,
                    adopted=adopted,
                    control_now=control_now,
                    entry_now=entry_now,
                    user_modified=user_modified,
                    library_changed=library_changed,
                )
            )
    return info


async def apply_resync(
    db: AsyncSession,
    control: Control,
    *,
    user_id: uuid.UUID | None,
    ip_address: str | None = None,
) -> int:
    """Overwrite the adopt-cloned fields + assignments with the current entry,
    re-pin + re-snapshot, audit, and flag affected COMPLETED runs stale.

    Returns the number of runs flagged. Raises ValueError when the control is
    not adopted or not stale (routes translate to 4xx).
    """
    from idraa.services.controls import (
        _build_adopted_snapshot,
        _delete_derived_rows_first,
        _sync_derived_row,
    )

    info = await resync_info(db, control)
    if info is None or info.entry is None:
        raise ValueError("control is not adopted from the library (nothing to re-sync)")
    if not info.stale:
        raise ValueError("control is already in sync with the library entry")
    entry = info.entry

    old_pin = dict(control.library_pin or {})
    for key, value in _entry_field_values(entry).items():
        if key == "type":
            # control_type enum column: re-typing an adopted control is out of
            # re-sync scope (type is curation-stable; a type change would be a
            # new entry, not a re-curation).
            continue
        if key == "annual_cost":
            control.annual_cost = Decimal(value)
        else:
            setattr(control, key, value)

    # Replace assignments with the re-curated set (UNCONFIRMED, same as adopt).
    entry_assignments = await _entry_assignments(db, entry)
    # #268: the derived row first, flushed, BEFORE any other row is deleted (it links
    # to a row the loop below deletes; see the module docstring). The returned source
    # sub-function decides re-link vs drop once the entry rows are in.
    audit = AuditWriter(db)
    derived_source_sf = await _delete_derived_rows_first(
        db,
        control.id,
        organization_id=control.organization_id,
        audit=audit,
        user_id=user_id,
        ip_address=ip_address,
    )
    existing_rows = (
        (
            await db.execute(
                select(ControlFunctionAssignment).where(
                    ControlFunctionAssignment.control_id == control.id
                )
            )
        )
        .scalars()
        .all()
    )
    for existing in existing_rows:
        await db.delete(existing)
    await db.flush()
    for a in entry_assignments:
        db.add(
            ControlFunctionAssignment(
                control_id=control.id,
                organization_id=control.organization_id,
                sub_function=a.sub_function,
                capability_value=a.capability_default,
                coverage=a.coverage_default,
                reliability=a.reliability_default,
                confirmed_by_user_at=None,
            )
        )
    control.library_pin = {"entry_id": str(entry.id), "version": entry.version}
    control.adopted_snapshot = _build_adopted_snapshot(entry, entry_assignments)
    await db.flush()
    if derived_source_sf is not None and any(
        a.sub_function == derived_source_sf for a in entry_assignments
    ):
        # Re-link: a NEW derived row against the new source row, unconfirmed like
        # every row a re-sync writes (the .delete above + this .create are the audit).
        await _sync_derived_row(
            db,
            control.id,
            control.organization_id,
            derived_source_sf,
            confirm=False,
            audit=audit,
            user_id=user_id,
            ip_address=ip_address,
        )
    # else: the entry no longer carries the source — the derived row stays dropped
    # (its .delete audit row was written by _delete_derived_rows_first).

    flagged = await flag_runs_stale_for_control(
        db, organization_id=control.organization_id, control_id=control.id
    )
    await audit.log(
        organization_id=control.organization_id,
        entity_type="control",
        entity_id=control.id,
        action="control.resync_from_library",
        changes={
            "version": [old_pin.get("version"), entry.version],
            "runs_flagged_stale": [None, flagged],
        },
        user_id=user_id,
        ip_address=ip_address,
    )
    return flagged
