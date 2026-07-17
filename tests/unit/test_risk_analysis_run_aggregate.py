"""Unit tests for nullable scenario_id (PR xi F4)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType


@pytest.mark.asyncio
async def test_aggregate_run_can_have_null_scenario_id(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """DB allows NULL scenario_id when aggregate_scenario_ids is set."""
    run = RiskAnalysisRun(
        organization_id=seed_organization.id,
        run_type=RunType.AGGREGATE,
        scenario_id=None,
        aggregate_scenario_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        control_ids_used=[],
        mc_iterations=10000,
        inputs_hash="abc",
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    assert run.id is not None
    assert run.scenario_id is None


@pytest.mark.asyncio
async def test_validates_aggregate_scenario_ids_minimum_length(
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """@validates rejects aggregate_scenario_ids with len < 2."""
    with pytest.raises(ValueError, match="len>=2"):
        RiskAnalysisRun(
            organization_id=seed_organization.id,
            run_type=RunType.AGGREGATE,
            scenario_id=None,
            aggregate_scenario_ids=[str(uuid.uuid4())],  # len 1, invalid
            control_ids_used=[],
            mc_iterations=10000,
            inputs_hash="abc",
            status=RunStatus.QUEUED,
            created_by=seed_user.id,
        )


@pytest.mark.asyncio
async def test_aggregate_control_ids_per_scenario_default_none(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_with_no_controls: Any,
) -> None:
    """Issue #89: new SINGLE row defaults aggregate_control_ids_per_scenario=None."""
    run = RiskAnalysisRun(
        organization_id=seed_organization.id,
        run_type=RunType.SINGLE,
        scenario_id=seed_scenario_with_no_controls.id,
        control_ids_used=[],
        mc_iterations=10000,
        inputs_hash="abc",
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    assert run.aggregate_control_ids_per_scenario is None


@pytest.mark.asyncio
async def test_aggregate_control_ids_per_scenario_roundtrip(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """Issue #89: dict round-trips through SQLite JSON column."""
    sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
    cid_x, cid_y = str(uuid.uuid4()), str(uuid.uuid4())
    per_scenario = {sid_a: [cid_x, cid_y], sid_b: [cid_y]}
    run = RiskAnalysisRun(
        organization_id=seed_organization.id,
        run_type=RunType.AGGREGATE,
        scenario_id=None,
        aggregate_scenario_ids=[sid_a, sid_b],
        control_ids_used=[cid_x, cid_y],
        aggregate_control_ids_per_scenario=per_scenario,
        mc_iterations=10000,
        inputs_hash="abc",
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.aggregate_control_ids_per_scenario == per_scenario


@pytest.mark.asyncio
async def test_aggregate_control_ids_per_scenario_rejects_non_dict(
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """Defense-in-depth: non-dict shape is rejected by @validates."""
    with pytest.raises(ValueError, match="dict"):
        RiskAnalysisRun(
            organization_id=seed_organization.id,
            run_type=RunType.AGGREGATE,
            scenario_id=None,
            aggregate_scenario_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
            control_ids_used=[],
            aggregate_control_ids_per_scenario=["not", "a", "dict"],  # type: ignore[arg-type]
            mc_iterations=10000,
            inputs_hash="abc",
            status=RunStatus.QUEUED,
            created_by=seed_user.id,
        )


@pytest.mark.asyncio
async def test_aggregate_control_ids_per_scenario_rejects_non_list_value(
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """Defense-in-depth: value must be list[str]."""
    sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
    with pytest.raises(ValueError, match="list\\[str\\]"):
        RiskAnalysisRun(
            organization_id=seed_organization.id,
            run_type=RunType.AGGREGATE,
            scenario_id=None,
            aggregate_scenario_ids=[sid_a, sid_b],
            control_ids_used=[],
            aggregate_control_ids_per_scenario={sid_a: "not_a_list", sid_b: []},  # type: ignore[dict-item]
            mc_iterations=10000,
            inputs_hash="abc",
            status=RunStatus.QUEUED,
            created_by=seed_user.id,
        )


@pytest.mark.asyncio
async def test_aggregate_control_ids_per_scenario_rejects_keys_not_matching_aggregate_scenario_ids(
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """M2: cross-field invariant — keys must equal aggregate_scenario_ids set."""
    sid_a, sid_b, sid_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with pytest.raises(ValueError, match="must equal aggregate_scenario_ids"):
        RiskAnalysisRun(
            organization_id=seed_organization.id,
            run_type=RunType.AGGREGATE,
            scenario_id=None,
            aggregate_scenario_ids=[sid_a, sid_b],
            control_ids_used=[],
            # Wrong: includes sid_c but missing sid_b.
            aggregate_control_ids_per_scenario={sid_a: [], sid_c: []},
            mc_iterations=10000,
            inputs_hash="abc",
            status=RunStatus.QUEUED,
            created_by=seed_user.id,
        )


@pytest.mark.asyncio
async def test_aggregate_control_ids_per_scenario_rejects_values_outside_control_ids_used(
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """M2: cross-field invariant — values must be subsets of control_ids_used."""
    sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
    cid_x = str(uuid.uuid4())
    cid_rogue = str(uuid.uuid4())
    with pytest.raises(ValueError, match="not in control_ids_used"):
        RiskAnalysisRun(
            organization_id=seed_organization.id,
            run_type=RunType.AGGREGATE,
            scenario_id=None,
            aggregate_scenario_ids=[sid_a, sid_b],
            control_ids_used=[cid_x],
            aggregate_control_ids_per_scenario={sid_a: [cid_x], sid_b: [cid_rogue]},
            mc_iterations=10000,
            inputs_hash="abc",
            status=RunStatus.QUEUED,
            created_by=seed_user.id,
        )


@pytest.mark.asyncio
async def test_aggregate_scenario_ids_round_trips_as_list_of_strings(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """JSON column round-trips as list[str] (NOT list[uuid.UUID] or mixed).

    PR xi + future PR pi rely on this contract: list_for_scenario membership
    test does `sid_str in r.aggregate_scenario_ids` on string equality.
    """
    sid_strs = [str(uuid.uuid4()), str(uuid.uuid4())]
    run = RiskAnalysisRun(
        organization_id=seed_organization.id,
        run_type=RunType.AGGREGATE,
        scenario_id=None,
        aggregate_scenario_ids=sid_strs,
        control_ids_used=[],
        mc_iterations=10000,
        inputs_hash="abc",
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.aggregate_scenario_ids == sid_strs
    assert all(isinstance(s, str) for s in run.aggregate_scenario_ids)
