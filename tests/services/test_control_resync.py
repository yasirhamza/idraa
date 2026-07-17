"""#438 — library re-sync service tests.

Flow under test: adopt from entry v1 (snapshot captured) → simulate a
re-curation (v2 row with changed values, mirroring the recuration-migration
shape: new version of the same entry id) → resync_info reports stale with a
3-way diff separating "library change" from "analyst edit" → apply_resync
overwrites, re-pins, re-snapshots, and flags COMPLETED runs stale via the
#437 plumbing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from idraa.models.control_library import (
    ControlLibraryEntry,
    ControlLibraryEntryAssignment,
)
from idraa.models.enums import ControlType
from idraa.models.enums import FairCamSubFunction as F
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.services.control_resync import apply_resync, resync_info
from idraa.services.controls import adopt_from_library

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
