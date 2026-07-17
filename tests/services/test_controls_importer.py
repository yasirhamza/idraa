"""Issue #90 Task 1: importer-derived multi-domain control behaviour.

After this task lands, the importer no longer denormalizes the first
recognized sub-function's domain into a (deprecated) `Control.domain`
column read path. The control's domain set is derived from its
assignments. These tests assert the derivation works for a multi-domain
CSV row (LEC + DSC paths in the same control).
"""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlDomain, subfunction_to_domain
from idraa.services.controls_importer import import_csv


def _csv_row(name: str, paths: list[str]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["", "Control", "Description", "FAIR CAM Paths", "Type"])
    writer.writerow(["", name, "test desc", "\n".join(paths), "technical"])
    return out.getvalue().encode("utf-8")


@pytest.mark.asyncio
async def test_importer_multi_domain_control_derives_domains_from_assignments(
    db_session: AsyncSession,
    organization,
    admin_user,
) -> None:
    """Issue #90: a CSV row with LEC + DSC paths produces a control whose
    derived domain set spans both LOSS_EVENT and DECISION_SUPPORT.

    Pre-issue-90 the importer wrote `Control.domain = LOSS_EVENT` (first
    recognized) and silently dropped DSC from any domain-filtered surface.
    Post-issue-90 the assignment list is the source of truth and
    `subfunction_to_domain` over the assignments yields both domains.
    """
    db, org, user = db_session, organization, admin_user
    paths = [
        "LEC - Prevention - Resistance",  # LEC
        "DSC - Prevent Misaligned Decisions - Define Expectations and Objectives",  # DSC
    ]
    imported, _ = await import_csv(
        db,
        org_id=org.id,
        user_id=user.id,
        csv_bytes=_csv_row("Multi-Domain Control", paths),
    )
    await db.commit()
    assert imported == 1

    ctrl = (
        await db.execute(select(Control).where(Control.name == "Multi-Domain Control"))
    ).scalar_one()

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
    assert len(rows) == 2

    derived_domains = {subfunction_to_domain(a.sub_function) for a in rows}
    assert derived_domains == {
        ControlDomain.LOSS_EVENT,
        ControlDomain.DECISION_SUPPORT,
    }, f"importer must let domains derive from all assignments — got {derived_domains!r}"
