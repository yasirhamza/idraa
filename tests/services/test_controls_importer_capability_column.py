"""Issue #129 T7 — importer 7th capability_value column."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.services.controls_importer import import_csv


async def _assignment_for(db, name: str) -> ControlFunctionAssignment:
    """Fetch the (single) ControlFunctionAssignment for a freshly-imported control."""
    ctrl = (await db.execute(select(Control).where(Control.name == name))).scalar_one()
    rows = (
        (
            await db.execute(
                select(ControlFunctionAssignment).where(
                    ControlFunctionAssignment.control_id == ctrl.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"expected 1 assignment for {name!r}, got {len(rows)}"
    return rows[0]


@pytest.mark.asyncio
async def test_legacy_6_column_csv_imports_unchanged(db_session, organization):
    """Backward-compat: 6-column CSV imports with default capability."""
    csv_bytes = b",Firewall,Network filter,LEC - Prevention - Resistance,preventive,5000\n"
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 1
    assert skipped == 0
    asgn = await _assignment_for(db_session, "Firewall")
    # PROBABILITY unit + no col 7 → existing importer default of 0.7
    assert asgn.capability_value == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_7_column_csv_sets_capability_value(db_session, organization):
    """7th column populates capability_value when valid."""
    csv_bytes = b",MFA,Two-factor auth,LEC - Prevention - Avoidance,preventive,3000,0.85\n"
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 1
    assert skipped == 0
    asgn = await _assignment_for(db_session, "MFA")
    assert asgn.capability_value == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_7_column_elapsed_time_accepts_day_count(db_session, organization):
    """ELAPSED_TIME sub-function accepts day-count value beyond [0,1]."""
    csv_bytes = b",MonitoringTool,SIEM,LEC - Detection - Monitoring,detective,10000,14\n"
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 1
    assert skipped == 0
    asgn = await _assignment_for(db_session, "MonitoringTool")
    assert asgn.capability_value == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_7_column_probability_rejects_out_of_bounds(db_session, organization):
    """PROBABILITY sub-function with capability > 1.0 → skip with warning."""
    csv_bytes = b",MFA,Two-factor,LEC - Prevention - Avoidance,preventive,3000,1.5\n"
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 0
    assert skipped == 1


@pytest.mark.asyncio
async def test_7_column_empty_uses_default(db_session, organization):
    """Empty 7th column behaves identically to absent column."""
    csv_bytes = b",MFA,Two-factor,LEC - Prevention - Avoidance,preventive,3000,\n"
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 1
    assert skipped == 0
    asgn = await _assignment_for(db_session, "MFA")
    # Empty col 7 → existing default (PROBABILITY unit → 0.7)
    assert asgn.capability_value == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_7_column_non_numeric_skips_row(db_session, organization):
    """Non-numeric capability_value → skip with warning."""
    csv_bytes = b",MFA,Two-factor,LEC - Prevention - Avoidance,preventive,3000,not-a-number\n"
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 0
    assert skipped == 1


@pytest.mark.asyncio
async def test_7_column_inf_value_skips_row(db_session, organization):
    """Sec-I1 round-1: 'inf' parses as float but math.isfinite rejects."""
    csv_bytes = b",MFA,Two-factor,LEC - Prevention - Avoidance,preventive,3000,inf\n"
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 0
    assert skipped == 1


@pytest.mark.asyncio
async def test_csv_exceeding_max_rows_breaks_at_cap(db_session, organization):
    """Spec-2-I4 + Sec-I3 round-2: cap-and-break at MAX_CSV_ROWS=10_000."""
    rows = b"".join(
        f",ctl{i},,LEC - Prevention - Avoidance,preventive,1000\n".encode("utf-8")
        for i in range(10_001)
    )
    imported, _skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=rows
    )
    assert imported == 10_000, f"expected 10_000 imported, got {imported}"


@pytest.mark.asyncio
async def test_multi_path_with_capability_value_skips_row(db_session, organization):
    """Spec-2-I4 + Sec-N3 round-2: multi-path control + non-empty capability_value
    → skip with warning. Multi-path single-cap dict deferred to follow-up."""
    csv_bytes = (
        b',MultiPathCtl,,"LEC - Prevention - Avoidance\nLEC - Detection - Monitoring",'
        b"preventive,1000,0.5\n"
    )
    imported, skipped = await import_csv(
        db_session, org_id=organization.id, user_id=None, csv_bytes=csv_bytes
    )
    assert imported == 0
    assert skipped == 1
