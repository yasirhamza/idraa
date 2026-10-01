"""No math path reads the community: identical engine output across community flips (spec §2, §6)."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RiskAnalysisRun, RunType
from idraa.models.run_samples import RunSamples
from idraa.models.threat_community import canonical_threat_community_id
from idraa.services.runs import RunService
from idraa.services.sample_export import samples_row_to_arrays

FLIPS = [
    ("cybercriminals", "nation_state"),
    ("cybercriminals", "insider_accidental"),
    ("cybercriminals", None),
]


def _set(scenario, slug):
    scenario.threat_community_id = canonical_threat_community_id(slug) if slug else None
    scenario.threat_community_version = 1 if slug else None
    scenario.threat_community_provenance = "assigned" if slug else "unassigned"


def _strip(snapshot):
    return {
        **snapshot,
        "scenarios": [
            {
                k: v
                for k, v in sc.items()
                if k not in ("threat_community", "threat_community_provenance")
            }
            for sc in snapshot["scenarios"]
        ],
    }


def _slug_in_snapshot(run, scenario_id) -> str | None:
    sc = next(
        x for x in run.scenario_inputs_snapshot["scenarios"] if x["scenario_id"] == str(scenario_id)
    )
    return (sc["threat_community"] or {}).get("slug")


async def _run(db_session, org_id, user_id, scenario_ids):
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=scenario_ids,
        mc_iterations_override=200,
        random_seed=1234,
        created_by=user_id,
        background_tasks=BackgroundTasks(),
    )  # inline in tests: wire_executor_to_test_db
    await db_session.refresh(run)
    return run


async def _samples(db_session: AsyncSession, run: RiskAnalysisRun) -> list[np.ndarray]:
    """Decoded per-iteration arrays in a stable (sorted-key) order.

    Accessor choice: ``services.sample_export.samples_row_to_arrays`` -- the
    same helper the raw-samples CSV export route (GET /runs/{id}/samples.csv.gz,
    #109) uses to turn a ``RunSamples`` row into ``dict[str, np.ndarray]``,
    preferring the binary codec and falling back to the legacy JSON store.
    Implemented as an async helper (not the bare ``def`` the brief's skeleton
    shows) because it must load the ``RunSamples`` row via the AsyncSession --
    a second, independently-committing session wrote it (``wire_executor_to_test_db``)
    so it cannot be reached via an already-loaded relationship without an
    await; ``_assert_identical`` below is async for the same reason.
    """
    row = await db_session.get(RunSamples, run.id)
    assert row is not None, f"no RunSamples row for run {run.id}"
    arrays = samples_row_to_arrays(row)
    return [arrays[k] for k in sorted(arrays)]


async def _assert_identical(
    db_session: AsyncSession, run_a: RiskAnalysisRun, run_b: RiskAnalysisRun
) -> None:
    assert (
        run_a.simulation_results == run_b.simulation_results
    )  # exact; a mismatch is investigated, never loosened
    assert run_a.inputs_hash == run_b.inputs_hash
    samples_a, samples_b = await _samples(db_session, run_a), await _samples(db_session, run_b)
    # Methodology N5: an empty samples list on BOTH sides would make zip(strict=True) iterate
    # zero times -- the loop body below would never run and this function would report a
    # false PASS without comparing a single array. Fail loud instead.
    assert samples_a, "run_a produced no decoded sample arrays"
    assert samples_b, "run_b produced no decoded sample arrays"
    for arr_a, arr_b in zip(samples_a, samples_b, strict=True):
        assert np.array_equal(arr_a, arr_b)
    assert _strip(run_a.scenario_inputs_snapshot) == _strip(run_b.scenario_inputs_snapshot)


@pytest.mark.parametrize(("before", "after"), FLIPS)
async def test_single_run_identical_across_flip(
    db_session,
    wire_executor_to_test_db,
    seed_scenario_with_controls,
    seed_organization,
    seed_user,
    before,
    after,
):
    s = seed_scenario_with_controls
    _set(s, before)
    await db_session.commit()
    run_a = await _run(db_session, seed_organization.id, seed_user.id, [s.id])
    _set(s, after)
    await db_session.commit()
    run_b = await _run(db_session, seed_organization.id, seed_user.id, [s.id])
    assert (
        _slug_in_snapshot(run_a, s.id) == before and _slug_in_snapshot(run_b, s.id) == after
    )  # non-vacuity: the flip reached the executor
    assert (
        run_a.simulation_results["residual_risk"]["annualized_loss_expectancy"] > 0
    )  # non-degenerate output
    await _assert_identical(db_session, run_a, run_b)


async def test_aggregate_run_identical_across_flip(
    db_session,
    wire_executor_to_test_db,
    seed_scenario_with_controls,
    scenario_factory,
    seed_organization,
    seed_user,
):
    s1 = seed_scenario_with_controls
    s2 = await scenario_factory(organization_id=seed_organization.id)
    _set(s1, "cybercriminals")
    _set(s2, "nation_state")
    await db_session.commit()
    run_a = await _run(db_session, seed_organization.id, seed_user.id, [s1.id, s2.id])
    assert run_a.run_type == RunType.AGGREGATE
    _set(s1, "hacktivists")
    await db_session.commit()
    run_b = await _run(db_session, seed_organization.id, seed_user.id, [s1.id, s2.id])
    assert (
        _slug_in_snapshot(run_a, s1.id) == "cybercriminals"
        and _slug_in_snapshot(run_b, s1.id) == "hacktivists"
    )
    assert (
        run_a.simulation_results["aggregate_with_controls"]["annualized_loss_expectancy"] > 0
    )  # non-degenerate output
    await _assert_identical(db_session, run_a, run_b)


async def test_executor_snapshot_carries_community_and_provenance(
    db_session,
    wire_executor_to_test_db,
    seed_scenario_with_controls,
    seed_organization,
    seed_user,
    seed_threat_communities,
):
    """run_executor._build_scenario_inputs_snapshot carries the four-key
    threat_community dict + provenance for an assigned scenario, and None +
    'unassigned' for a NULL-community one."""
    s = seed_scenario_with_controls
    _set(s, "cybercriminals")
    await db_session.commit()
    run = await _run(db_session, seed_organization.id, seed_user.id, [s.id])
    sc = next(x for x in run.scenario_inputs_snapshot["scenarios"] if x["scenario_id"] == str(s.id))
    row = seed_threat_communities["cybercriminals"]
    assert sc["threat_community"] == {
        "id": str(row.id),
        "version": row.version,
        "slug": "cybercriminals",
        "name": row.name,
    }
    assert sc["threat_community_provenance"] == "assigned"

    _set(s, None)
    await db_session.commit()
    run2 = await _run(db_session, seed_organization.id, seed_user.id, [s.id])
    sc2 = next(
        x for x in run2.scenario_inputs_snapshot["scenarios"] if x["scenario_id"] == str(s.id)
    )
    assert sc2["threat_community"] is None
    assert sc2["threat_community_provenance"] == "unassigned"
