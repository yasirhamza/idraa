"""Adapter iteration contract test for controls_importer (#68).

CLAUDE.md "Data contract enforcement is project-wide policy":
single-CSV-row-with-list-field (col 4 = N sub-function paths) → 1
Control ORM + N ControlFunctionAssignment ORMs. Catches future
flattening regressions where someone takes paths[0] / paths[-1] /
deduplicates by sub_function and silently drops list items.

Uses Counter for cardinality (not set) — set equality collapses
duplicate sub_function values, defeating the kappa-pattern cardinality
guard.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import FairCamSubFunction
from idraa.services.controls_importer import import_csv


def _csv_row(name: str, paths: list[str]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["", "Control", "Description", "FAIR CAM ...", "Type"])  # header
    writer.writerow(["", name, "test desc", "\n\n".join(paths), "technical"])
    return out.getvalue().encode("utf-8")


@pytest.mark.asyncio
async def test_importer_creates_one_assignment_per_recognized_path(
    db_session: AsyncSession, organization, admin_user
) -> None:
    """N=4 recognized sub-function paths → 4 ControlFunctionAssignment rows.

    Counter assertion guards against future de-duplication regressions
    that would silently drop list items.
    """
    db, org, user = db_session, organization, admin_user
    paths = [
        "LEC - Detection - Visibility",
        "LEC - Detection - Monitoring",
        "LEC - Detection - Recognition",
        "LEC - Prevention - Resistance",
    ]
    imported, _ = await import_csv(
        db, org_id=org.id, user_id=user.id, csv_bytes=_csv_row("SIEM-test", paths)
    )
    await db.commit()
    assert imported == 1

    ctrl = (await db.execute(select(Control).where(Control.name == "SIEM-test"))).scalar_one()

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

    assert len(rows) == 4
    assert Counter(row.sub_function for row in rows) == Counter(
        [
            FairCamSubFunction.LEC_DET_VISIBILITY,
            FairCamSubFunction.LEC_DET_MONITORING,
            FairCamSubFunction.LEC_DET_RECOGNITION,
            FairCamSubFunction.LEC_PREV_RESISTANCE,
        ]
    )


@pytest.mark.asyncio
async def test_importer_creates_control_with_zero_assignments_when_all_paths_unknown(
    db_session: AsyncSession, organization, admin_user
) -> None:
    """All unknown paths → Control still imports with zero assignments.
    Q3 decision: skip the assignment, log warning, still import."""
    db, org, user = db_session, organization, admin_user
    paths = ["MADE-UP - Something - Nonexistent", "ALSO-INVALID - Path"]
    imported, _ = await import_csv(
        db, org_id=org.id, user_id=user.id, csv_bytes=_csv_row("UnknownCtrl", paths)
    )
    await db.commit()
    assert imported == 1
    ctrl = (await db.execute(select(Control).where(Control.name == "UnknownCtrl"))).scalar_one()
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
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_importer_creates_control_with_zero_assignments_when_paths_col_empty(
    db_session: AsyncSession, organization, admin_user
) -> None:
    """Empty paths column → Control imports with zero assignments + empty derived domains.

    Issue #90 dropped Control.domain. A control with no assignments has
    ``c.domains == frozenset()`` — the previous LOSS_EVENT column fallback
    no longer applies (the importer simply persists with no rows on the
    assignments side, and the derived property returns an empty set).
    """
    db, org, user = db_session, organization, admin_user
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["", "Control", "Description", "FAIR CAM ...", "Type"])
    writer.writerow(["", "EmptyPathsCtrl", "test", "", "technical"])
    csv_bytes = out.getvalue().encode("utf-8")

    imported, _ = await import_csv(db, org_id=org.id, user_id=user.id, csv_bytes=csv_bytes)
    await db.commit()
    assert imported == 1
    ctrl = (
        await db.execute(
            select(Control)
            .where(Control.name == "EmptyPathsCtrl")
            .options(selectinload(Control.assignments))
        )
    ).scalar_one()
    assert ctrl.domains == frozenset()
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
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_importer_skips_virtual_dsc_corr_misaligned_path(
    db_session: AsyncSession, organization, admin_user
) -> None:
    """DSC_CORR_MISALIGNED is virtual — skip the assignment, still import."""
    db, org, user = db_session, organization, admin_user
    paths = [
        "DSC - Correct Misaligned Decisions",  # virtual — skip
        "LEC - Prevention - Resistance",  # real — keep
    ]
    imported, _ = await import_csv(
        db, org_id=org.id, user_id=user.id, csv_bytes=_csv_row("MixedCtrl", paths)
    )
    await db.commit()
    assert imported == 1
    ctrl = (await db.execute(select(Control).where(Control.name == "MixedCtrl"))).scalar_one()
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
    assert {r.sub_function for r in rows} == {FairCamSubFunction.LEC_PREV_RESISTANCE}


@pytest.mark.asyncio
async def test_importer_audit_changes_includes_sub_function_count(
    db_session: AsyncSession, organization, admin_user
) -> None:
    """Audit payload pins the new ``sub_function_count`` key.

    Guards the audit-payload schema extension #68 introduces — a future
    revert that strips this key would silently weaken the audit trail.
    """
    db, org, user = db_session, organization, admin_user
    paths = ["LEC - Detection - Visibility", "LEC - Detection - Monitoring"]
    await import_csv(db, org_id=org.id, user_id=user.id, csv_bytes=_csv_row("AuditedCtrl", paths))
    await db.commit()

    audit_row = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "control.import",
                    AuditLog.organization_id == org.id,
                )
            )
        )
        .scalars()
        .first()
    )
    assert audit_row is not None
    changes = (
        audit_row.changes if isinstance(audit_row.changes, dict) else json.loads(audit_row.changes)
    )
    assert "sub_function_count" in changes
    assert changes["sub_function_count"] == [None, 2]
