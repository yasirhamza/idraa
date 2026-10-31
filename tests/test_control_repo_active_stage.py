"""ControlRepo.list_for_org excludes non-active implementation_stage (#395).

The plan's placeholder ``sole_org`` does not exist in this repo's fixture set;
the real org fixture is ``organization`` (tests/conftest.py), mirroring the
prior Task 4 test ``tests/test_controls_service_stage.py``.
"""

from __future__ import annotations

from itertools import product

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import ControlImplementationStage, ControlType, EntityStatus
from idraa.models.organization import Organization
from idraa.repositories.control_repo import ControlRepo
from idraa.services import controls as svc
from idraa.services.controls import _org_wide_effective


@pytest.mark.asyncio
async def test_list_for_org_excludes_non_active_stage(
    db_session: AsyncSession, organization: Organization
) -> None:
    active = Control(
        organization_id=organization.id,
        name="A active",
        type=ControlType.TECHNICAL,
        implementation_stage=ControlImplementationStage.ACTIVE,
    )
    planned = Control(
        organization_id=organization.id,
        name="B planned",
        type=ControlType.TECHNICAL,
        implementation_stage=ControlImplementationStage.PLANNED,
    )
    db_session.add_all([active, planned])
    await db_session.flush()

    picker = await ControlRepo(db_session).list_for_org(organization.id)
    names = {c.name for c in picker}
    assert "A active" in names
    assert "B planned" not in names

    # The controls LIBRARY list (management view) must still show the planned
    # control so the operator can manage it.
    library = await svc.list_controls(db_session, org_id=organization.id)
    assert {"A active", "B planned"} <= {c.name for c in library}


@pytest.mark.asyncio
async def test_repo_filter_matches_predicate(
    db_session: AsyncSession, organization: Organization
) -> None:
    # Plan-gate Arch-P1: the repo uses a raw `== ACTIVE` column comparison (a
    # @property can't be called in SQL). Lock it to the predicate so the two
    # expressions of "only active composes" can't drift if a 5th stage is added.
    #
    # #299: every (status, stage) pair is built, all flagged org-wide, so the SQL filters of
    # list_for_org / list_org_wide_meta / count_org_wide_meta AND the service predicate
    # services.controls._org_wide_effective (which decides the stale sweeps) are checked
    # against one independent expectation — a freeze that inherits a control the sweep
    # does not consider effective (or the reverse) would present stale results as current.
    made = []
    for i, (status, stage) in enumerate(product(EntityStatus, ControlImplementationStage)):
        c = Control(
            organization_id=organization.id,
            name=f"c{i}",
            type=ControlType.TECHNICAL,
            status=status,
            implementation_stage=stage,
            org_wide_meta=True,
        )
        made.append(c)
    db_session.add_all(made)
    await db_session.flush()

    predicate_ids = {
        c.id
        for c in made
        if c.status is EntityStatus.ACTIVE and c.implementation_stage.contributes_to_composition
    }
    assert predicate_ids and len(predicate_ids) < len(made)  # the filter is non-trivial
    picker_ids = {c.id for c in await ControlRepo(db_session).list_for_org(organization.id)}
    assert picker_ids == predicate_ids

    repo = ControlRepo(db_session)
    org_wide_ids = {c.id for c in await repo.list_org_wide_meta(organization.id)}
    assert org_wide_ids == predicate_ids  # #299: same predicate as list_for_org
    assert await repo.count_org_wide_meta(organization.id) == len(predicate_ids)
    assert {c.id for c in made if _org_wide_effective(c)} == org_wide_ids
