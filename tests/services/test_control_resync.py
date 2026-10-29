"""#438 — library re-sync service tests.

Flow under test: adopt from entry v1 (snapshot captured) → simulate a
re-curation (v2 row with changed values, mirroring the recuration-migration
shape: new version of the same entry id) → resync_info reports stale with a
3-way diff separating "library change" from "analyst edit" → apply_resync
overwrites, re-pins, re-snapshots, and flags COMPLETED runs stale via the
#437 plumbing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.enums import ControlSource, ControlType
from idraa.models.enums import FairCamSubFunction as F
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services.control_resync import apply_resync, resync_info
from idraa.services.controls import _build_adopted_snapshot, adopt_from_library, update_control

from .test_control_adopt import _published_entry


async def _recurate_to_v2(db: Any, entry: ControlLibraryEntry) -> ControlLibraryEntry:
    """Publish a v2 of the same entry id with changed values (the recuration
    shape: same id, higher version, revised fields + assignment set)."""
    v2 = ControlLibraryEntry(
        id=entry.id,
        version=2,
        slug=entry.slug,
        name=entry.name,
        description="Re-curated description with sharper scope guidance." + "b" * 10,
        control_type=ControlType.TECHNICAL,
        reference_annual_cost=45000,  # was 30000
        nist_csf_subcategories=["PR.AC-7", "PR.AC-1"],  # +PR.AC-1
        cis_safeguards=["6.3"],
        iso_27001_controls=["A.9.4.2"],
        compliance_mappings={"csa_ccm_v4": ["IAM-01"]},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status="published",
    )
    db.add(v2)
    await db.flush()
    for fn, cap in ((F.LEC_PREV_RESISTANCE, 0.8), (F.LEC_DET_VISIBILITY, 0.7)):
        db.add(
            ControlLibraryEntryAssignment(
                library_entry_id=entry.id,
                library_entry_version=2,
                sub_function=fn,
                capability_default=cap,
                coverage_default=0.8,
                reliability_default=0.8,
            )
        )
    await db.flush()
    return v2


@pytest.mark.asyncio
async def test_adopt_captures_snapshot(db_session: Any, seed_org_user: Any) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    snap = control.adopted_snapshot
    assert snap is not None
    assert snap["version"] == 1 and snap["entry_id"] == str(entry.id)
    assert snap["name"] == entry.name
    assert snap["annual_cost"] == "30000"
    assert {a["sub_function"] for a in snap["assignments"]} == {
        "lec_prev_resistance",
        "lec_det_visibility",
        "vmc_id_control_monitoring",
    }
    # Snapshot mirrors the D1 dedup: cis inside compliance_mappings.
    assert snap["compliance_mappings"]["cis_safeguards"] == ["6.3"]


@pytest.mark.asyncio
async def test_resync_info_none_for_custom_and_fresh_for_current(
    db_session: Any, seed_org_user: Any
) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    info = await resync_info(db_session, control)
    assert info is not None and info.stale is False
    assert info.pinned_version == info.current_version == 1

    control.library_pin = None  # custom control shape
    assert await resync_info(db_session, control) is None


@pytest.mark.asyncio
async def test_resync_diff_separates_library_change_from_user_edit(
    db_session: Any, seed_org_user: Any
) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    # Analyst edit AFTER adoption: description only.
    control.description = "our own operating notes for this control" + "c" * 5
    await db_session.flush()
    await _recurate_to_v2(db_session, entry)

    info = await resync_info(db_session, control)
    assert info is not None and info.stale and info.has_snapshot
    by_field = {r.field: r for r in info.fields}
    # description: BOTH changed (user edited; library re-curated it too).
    assert by_field["description"].user_modified is True
    assert by_field["description"].library_changed is True
    # annual_cost: library-only change (30000 -> 45000, user untouched).
    assert by_field["annual_cost"].user_modified is False
    assert by_field["annual_cost"].library_changed is True
    # nist tags: library-only change (+PR.AC-1).
    assert by_field["nist_csf_functions"].library_changed is True
    # Assignment diff: vmc_id_control_monitoring dropped in v2, resistance cap changed.
    by_fn = {r.field: r for r in info.assignments}
    assert by_fn["vmc_id_control_monitoring"].entry_now is None  # removed in v2
    assert by_fn["lec_prev_resistance"].library_changed is True  # 0.7 -> 0.8


@pytest.mark.asyncio
async def test_resync_coarse_mode_without_snapshot(db_session: Any, seed_org_user: Any) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    control.adopted_snapshot = None  # legacy adoption (pre-c9e4f7a2b8d1)
    await _recurate_to_v2(db_session, entry)

    info = await resync_info(db_session, control)
    assert info is not None and info.stale and not info.has_snapshot
    assert all(r.user_modified is None and r.library_changed is None for r in info.fields)


@pytest.mark.asyncio
async def test_apply_resync_overwrites_repins_and_flags_runs(
    db_session: Any, seed_org_user: Any
) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    await db_session.flush()
    # A COMPLETED run that used this control (control_ids_used stores
    # hyphenated str(uuid) — see flag_runs_stale_for_control docstring).
    run = RiskAnalysisRun(
        organization_id=org.id,
        scenario_id=None,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        mc_iterations=1000,
        inputs_hash="resync-test-hash",
        control_ids_used=[str(control.id)],
        created_by=user.id,
    )
    db_session.add(run)
    await db_session.flush()
    await _recurate_to_v2(db_session, entry)

    flagged = await apply_resync(db_session, control, user_id=user.id)
    assert flagged == 1
    await db_session.refresh(run)
    assert run.is_stale is True

    assert control.library_pin == {"entry_id": str(entry.id), "version": 2}
    assert control.annual_cost == Decimal("45000")
    assert sorted(control.nist_csf_functions) == ["PR.AC-1", "PR.AC-7"]
    assert control.adopted_snapshot is not None
    assert control.adopted_snapshot["version"] == 2
    # Assignments replaced with the v2 set, unconfirmed (explicit refresh —
    # the delete/insert cycle expired the relationship).
    await db_session.refresh(control, ["assignments"])
    subs = sorted(a.sub_function.value for a in control.assignments)
    assert subs == ["lec_det_visibility", "lec_prev_resistance"]
    assert all(a.confirmed_by_user_at is None for a in control.assignments)

    # Second apply: already in sync -> ValueError.
    with pytest.raises(ValueError, match="already in sync"):
        await apply_resync(db_session, control, user_id=user.id)


@pytest.mark.asyncio
async def test_apply_resync_rejects_custom_control(db_session: Any, seed_org_user: Any) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    control.library_pin = None
    with pytest.raises(ValueError, match="not adopted"):
        await apply_resync(db_session, control, user_id=user.id)


# ---------------------------------------------------------------------------
# #268 — a derived dsc_corr_misaligned row survives (or is dropped by) a re-sync
# ---------------------------------------------------------------------------
#
# The derived row stores only a link to another row of the SAME control. A re-sync
# deletes ALL of the control's rows and re-adds the entry's, so the link is
# captured first (the derived row is deleted and flushed FIRST — SQLAlchemy sorts
# the DELETEs of one flush by primary key and FK ON DELETE SET NULL on a surviving
# derived row violates ck_cfa_virtual_requires_derived), then re-created against
# the new source row if the entry still carries the source sub-function, or dropped.
# Fixed ids in BOTH orders pin the ordering.

_LOW = uuid.UUID("00000000-0000-4000-8000-000000000001")
_HIGH = uuid.UUID("ffffffff-ffff-4fff-bfff-fffffffffffe")
_MID = uuid.UUID("7fffffff-ffff-4fff-8fff-00000000000a")
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_BOTH_ORDERS = pytest.mark.parametrize(
    ("source_id", "derived_id"),
    [
        pytest.param(_LOW, _HIGH, id="source_id_lt_derived_id"),
        pytest.param(_HIGH, _LOW, id="source_id_gt_derived_id"),
    ],
)

_CAP: dict[F, float] = {
    F.VMC_CORR_IMPLEMENTATION: 3.0,
    F.LEC_RESP_RESILIENCE: 0.7,
    F.LEC_PREV_RESISTANCE: 0.75,
}

_DELETE_PAYLOAD = {
    "sub_function": ["dsc_corr_misaligned", None],
    "derived_from_sub_function": ["vmc_corr_implementation", None],
}


async def _entry_version(
    db: Any, *, funcs: list[F], version: int, entry_id: uuid.UUID | None = None
) -> tuple[ControlLibraryEntry, list[ControlLibraryEntryAssignment]]:
    kwargs: dict[str, Any] = {} if entry_id is None else {"id": entry_id}
    entry = ControlLibraryEntry(
        version=version,
        slug="linked-control",
        name="Linked control",
        description="a" * 25,
        control_type=ControlType.TECHNICAL,
        reference_annual_cost=30000,
        nist_csf_subcategories=["PR.AC-7"],
        cis_safeguards=["6.3"],
        iso_27001_controls=["A.9.4.2"],
        compliance_mappings={"csa_ccm_v4": ["IAM-01"]},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status="published",
        **kwargs,
    )
    db.add(entry)
    await db.flush()
    assignments = [
        ControlLibraryEntryAssignment(
            library_entry_id=entry.id,
            library_entry_version=version,
            sub_function=fn,
            capability_default=_CAP[fn],
            coverage_default=0.8,
            reliability_default=0.8,
        )
        for fn in funcs
    ]
    db.add_all(assignments)
    await db.flush()
    return entry, assignments


async def _adopted_with_derived(
    db: Any,
    seed_org_user: Any,
    *,
    source_id: uuid.UUID,
    derived_id: uuid.UUID,
    v1_funcs: list[F],
) -> tuple[Any, Any, Control, ControlLibraryEntry]:
    """An adopted control (pinned to v1) carrying a derived row, FIXED primary keys.

    ``v1_funcs`` must contain vmc_corr_implementation (the link source); the other
    rows get ``_MID``-range ids.
    """
    org, user = await seed_org_user(db)
    entry, v1 = await _entry_version(db, funcs=v1_funcs, version=1)
    control = Control(
        organization_id=org.id,
        created_by=user.id,
        name=entry.name,
        description=entry.description,
        type=entry.control_type,
        annual_cost=Decimal("30000"),
        source=ControlSource.LIBRARY_DERIVED,
        library_pin={"entry_id": str(entry.id), "version": 1},
        adopted_snapshot=_build_adopted_snapshot(entry, v1),
    )
    db.add(control)
    await db.flush()
    others = [fn for fn in v1_funcs if fn is not F.VMC_CORR_IMPLEMENTATION]
    ids = {F.VMC_CORR_IMPLEMENTATION: source_id}
    ids.update({fn: uuid.UUID(int=_MID.int + i) for i, fn in enumerate(others)})
    for fn, row_id in ids.items():
        db.add(
            ControlFunctionAssignment(
                id=row_id,
                control_id=control.id,
                organization_id=org.id,
                sub_function=fn,
                capability_value=_CAP[fn],
                coverage=0.8,
                reliability=0.8,
                confirmed_by_user_at=_T0,
            )
        )
    await db.flush()
    db.add(
        ControlFunctionAssignment(
            id=derived_id,
            control_id=control.id,
            organization_id=org.id,
            sub_function=F.DSC_CORR_MISALIGNED,
            capability_value=None,
            coverage=0.0,
            reliability=0.0,
            derived_from_assignment_id=source_id,
            confirmed_by_user_at=_T0,
        )
    )
    await db.flush()
    return org, user, control, entry


async def _cfa_rows(db: Any, control_id: uuid.UUID) -> dict[F, ControlFunctionAssignment]:
    result = await db.execute(
        select(ControlFunctionAssignment).where(ControlFunctionAssignment.control_id == control_id)
    )
    return {r.sub_function: r for r in result.scalars().all()}


async def _audits(db: Any, action: str, entity_id: uuid.UUID) -> list[AuditLog]:
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.action == action, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.timestamp, AuditLog.id)
    )
    return list(result.scalars().all())


@_BOTH_ORDERS
@pytest.mark.asyncio
async def test_resync_relinks_derived_row_when_the_entry_still_carries_the_source(
    db_session: Any, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    org, user, control, entry = await _adopted_with_derived(
        db_session,
        seed_org_user,
        source_id=source_id,
        derived_id=derived_id,
        v1_funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_PREV_RESISTANCE],
    )
    await _entry_version(
        db_session,
        funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_RESP_RESILIENCE],
        version=2,
        entry_id=entry.id,
    )

    info = await resync_info(db_session, control)
    assert info is not None and info.stale
    assert info.derived_fate == "relinked"
    # The derived row is excluded from the assignment diff (no sentinel is shown).
    assert "dsc_corr_misaligned" not in {r.field for r in info.assignments}

    await apply_resync(db_session, control, user_id=user.id)

    rows = await _cfa_rows(db_session, control.id)
    assert set(rows) == {F.VMC_CORR_IMPLEMENTATION, F.LEC_RESP_RESILIENCE, F.DSC_CORR_MISALIGNED}
    new = rows[F.DSC_CORR_MISALIGNED]
    assert new.id != derived_id  # a NEW row
    assert new.derived_from_assignment_id == rows[F.VMC_CORR_IMPLEMENTATION].id
    assert new.derived_from_assignment_id != source_id  # the NEW source row
    assert (new.capability_value, new.coverage, new.reliability) == (None, 0.0, 0.0)
    # Resync is not an authoring act: unconfirmed like every row it writes.
    assert (new.confirmed_by_user_at, new.measured_by, new.measured_at) == (None, None, None)

    deletes = await _audits(db_session, "control_function_assignment.delete", derived_id)
    assert [a.changes for a in deletes] == [_DELETE_PAYLOAD]
    creates = await _audits(db_session, "control_function_assignment.create", new.id)
    assert [a.changes for a in creates] == [
        {
            "sub_function": [None, "dsc_corr_misaligned"],
            "derived_from_sub_function": [None, "vmc_corr_implementation"],
            "confirmed_by_user_at": [None, None],
        }
    ]
    await db_session.commit()  # the DB-level CHECK/FK is satisfied at commit too


@_BOTH_ORDERS
@pytest.mark.asyncio
async def test_resync_drops_derived_row_when_the_entry_no_longer_carries_the_source(
    db_session: Any, seed_org_user: Any, source_id: uuid.UUID, derived_id: uuid.UUID
) -> None:
    org, user, control, entry = await _adopted_with_derived(
        db_session,
        seed_org_user,
        source_id=source_id,
        derived_id=derived_id,
        v1_funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_PREV_RESISTANCE],
    )
    await _entry_version(
        db_session,
        funcs=[F.LEC_PREV_RESISTANCE, F.LEC_RESP_RESILIENCE],
        version=2,
        entry_id=entry.id,
    )

    info = await resync_info(db_session, control)
    assert info is not None and info.stale
    assert info.derived_fate == "dropped"
    assert "dsc_corr_misaligned" not in {r.field for r in info.assignments}

    await apply_resync(db_session, control, user_id=user.id)

    rows = await _cfa_rows(db_session, control.id)
    assert set(rows) == {F.LEC_PREV_RESISTANCE, F.LEC_RESP_RESILIENCE}
    deletes = await _audits(db_session, "control_function_assignment.delete", derived_id)
    assert [a.changes for a in deletes] == [_DELETE_PAYLOAD]
    created_dsc = [
        a
        for a in (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "control_function_assignment.create")
            )
        )
        .scalars()
        .all()
        if a.changes.get("sub_function") == [None, "dsc_corr_misaligned"]
    ]
    assert created_dsc == []
    await db_session.commit()


@pytest.mark.asyncio
async def test_resync_info_reports_no_derived_fate_without_a_derived_row_or_staleness(
    db_session: Any, seed_org_user: Any
) -> None:
    org, user = await seed_org_user(db_session)
    entry = await _published_entry(db_session)
    control = await adopt_from_library(
        db_session, org_id=org.id, user_id=user.id, entry_id=entry.id, version=None
    )
    fresh = await resync_info(db_session, control)
    assert fresh is not None and not fresh.stale and fresh.derived_fate is None

    await _recurate_to_v2(db_session, entry)
    stale = await resync_info(db_session, control)
    assert stale is not None and stale.stale and stale.derived_fate is None


@pytest.mark.asyncio
async def test_resync_info_derived_fate_is_none_while_the_control_is_in_sync(
    db_session: Any, seed_org_user: Any
) -> None:
    _, _, control, _ = await _adopted_with_derived(
        db_session,
        seed_org_user,
        source_id=_LOW,
        derived_id=_HIGH,
        v1_funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_PREV_RESISTANCE],
    )
    info = await resync_info(db_session, control)
    assert info is not None and not info.stale and info.derived_fate is None


@pytest.mark.asyncio
async def test_form_save_confirms_the_derived_row_a_resync_left_null(
    db_session: Any, seed_org_user: Any
) -> None:
    org, user, control, entry = await _adopted_with_derived(
        db_session,
        seed_org_user,
        source_id=_LOW,
        derived_id=_HIGH,
        v1_funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_PREV_RESISTANCE],
    )
    await _entry_version(
        db_session,
        funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_RESP_RESILIENCE],
        version=2,
        entry_id=entry.id,
    )
    await apply_resync(db_session, control, user_id=user.id)
    await db_session.refresh(control, ["assignments"])
    derived = (await _cfa_rows(db_session, control.id))[F.DSC_CORR_MISALIGNED]
    derived_id = derived.id
    assert derived.confirmed_by_user_at is None

    form = ControlForm(
        name=control.name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("30000"),
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=fn, capability_value=_CAP[fn], coverage=0.8, reliability=0.8
            )
            for fn in (F.VMC_CORR_IMPLEMENTATION, F.LEC_RESP_RESILIENCE)
        ],
    )
    await update_control(
        db_session,
        control=control,
        user_id=user.id,
        form=form,
        dsc_corr_source=F.VMC_CORR_IMPLEMENTATION,
    )

    after = (await _cfa_rows(db_session, control.id))[F.DSC_CORR_MISALIGNED]
    assert after.id == derived_id
    assert after.confirmed_by_user_at is not None
    assert after.measured_by == user.id
    confirms = await _audits(db_session, "control_function_assignment.confirm", derived_id)
    assert len(confirms) == 1


_SRC_299 = uuid.UUID(int=_MID.int + 100)
_DER_299 = uuid.UUID(int=_MID.int + 101)


@pytest.mark.asyncio
async def test_resync_refuses_to_strip_last_meta_row_from_org_wide_control_before_mutating(
    db_session: Any, seed_org_user: Any
) -> None:
    """#299 spec §3.1 / D9 (Sec-I3, SC-I1): the eligibility check runs on the ENTRY's
    rows FIRST — before the field loop and before any derived-row delete — so a
    refusal leaves the control, its rows and its pin untouched."""
    from idraa.services.controls import OrgWideMetaIneligible

    org, user, control, entry = await _adopted_with_derived(
        db_session,
        seed_org_user,
        source_id=_SRC_299,
        derived_id=_DER_299,
        v1_funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_PREV_RESISTANCE],
    )
    control.org_wide_meta = True
    await db_session.commit()
    # v2 of the same entry drops every meta row (LEC-only) -> stale AND ineligible.
    await _entry_version(db_session, funcs=[F.LEC_PREV_RESISTANCE], version=2, entry_id=entry.id)
    await db_session.commit()
    before = set(await _cfa_rows(db_session, control.id))
    assert before == {F.VMC_CORR_IMPLEMENTATION, F.LEC_PREV_RESISTANCE, F.DSC_CORR_MISALIGNED}
    assert control.library_pin is not None
    pin_before = dict(control.library_pin)
    cost_before = control.annual_cost

    with pytest.raises(OrgWideMetaIneligible):
        await apply_resync(db_session, control, user_id=user.id)
    await db_session.rollback()

    await db_session.refresh(control)  # rollback expired every attribute, assignments included
    assert set(await _cfa_rows(db_session, control.id)) == before
    assert control.library_pin == pin_before and control.library_pin["version"] == 1
    assert (
        control.annual_cost == cost_before
    )  # (v1 and v2 share the cost here; pin + rows are the evidence)
    assert control.org_wide_meta is True
    assert await _audits(db_session, "control.resync_from_library", control.id) == []
    assert await _audits(db_session, "control_function_assignment.delete", _DER_299) == []


@pytest.mark.asyncio
async def test_resync_of_org_wide_control_sweeps_the_whole_org(
    db_session: Any, seed_org_user: Any
) -> None:
    """#299 spec D9 / §6: an accepted re-sync of an EFFECTIVE org-wide control
    flags EVERY completed run in the org (a run that never used the control
    included) and records the count in the existing control.resync_from_library
    row — no separate org_wide_sweep row for resync."""
    org, user, control, entry = await _adopted_with_derived(
        db_session,
        seed_org_user,
        source_id=_SRC_299,
        derived_id=_DER_299,
        v1_funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_PREV_RESISTANCE],
    )
    control.org_wide_meta = True
    unrelated = RiskAnalysisRun(  # COMPLETED, never used `control`
        organization_id=org.id,
        scenario_id=None,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        mc_iterations=1000,
        inputs_hash="resync-299-unrelated",
        control_ids_used=[],
        created_by=user.id,
    )
    db_session.add(unrelated)
    await db_session.commit()
    # v2 keeps the meta row (changes the LEC row) -> stale, still eligible.
    await _entry_version(
        db_session,
        funcs=[F.VMC_CORR_IMPLEMENTATION, F.LEC_RESP_RESILIENCE],
        version=2,
        entry_id=entry.id,
    )
    await db_session.commit()

    flagged = await apply_resync(db_session, control, user_id=user.id)
    await db_session.commit()
    assert flagged == 1
    await db_session.refresh(unrelated)
    assert unrelated.is_stale is True
    (row,) = await _audits(db_session, "control.resync_from_library", control.id)
    assert row.changes["runs_flagged_stale"] == [None, 1]
    assert row.changes["version"] == [1, 2]
    assert await _audits(db_session, "control.org_wide_sweep", control.id) == []
    await db_session.refresh(control, ["assignments"])
    assert control.org_wide_meta is True
    assert F.VMC_CORR_IMPLEMENTATION in set(await _cfa_rows(db_session, control.id))
