"""Framework→FAIR-CAM crosswalk models — DB-level ON DELETE CASCADE (P2a).

Proves the link table's FK cascade fires at the DATABASE level (raw SQL DELETE
on the parent FrameworkControl), not just via SQLAlchemy ORM unit-of-work
cascade. Requires ``PRAGMA foreign_keys = ON`` (installed per-connection by the
db_session fixture) — without it SQLite silently ignores ``ON DELETE CASCADE``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from idraa.models.enums import FairCamSubFunction
from idraa.models.framework_crosswalk import (
    FrameworkControl,
    FrameworkControlFairCam,
)


@pytest.mark.asyncio
async def test_link_cascades_on_control_delete(db_session):
    fc = FrameworkControl(
        framework="nist_csf",
        framework_version="1.1",
        code="PR.AC-7",
        title="Users/devices/assets are authenticated",
        description=None,
        asset_type=None,
        security_function=None,
        citation={"source": "FAIR Institute"},
    )
    db_session.add(fc)
    await db_session.flush()
    db_session.add(
        FrameworkControlFairCam(
            framework_control_id=fc.id,
            fair_cam_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        )
    )
    await db_session.flush()
    # Delete via raw SQL (DB CASCADE), NOT db.delete(fc) (ORM cascade) — proves
    # the DB-level ON DELETE CASCADE actually fires (requires foreign_keys=ON).
    # SQLAlchemy's Uuid type stores as 32-char hex on SQLite, so bind fc.id.hex
    # (dashed str(fc.id) would not match the stored column value -> no-op delete).
    await db_session.execute(text("DELETE FROM framework_controls WHERE id = :i"), {"i": fc.id.hex})
    rows = (
        await db_session.execute(
            select(FrameworkControlFairCam).where(
                FrameworkControlFairCam.framework_control_id == fc.id
            )
        )
    ).all()
    assert rows == []  # DB cascade (foreign_keys=ON from prior infra)
