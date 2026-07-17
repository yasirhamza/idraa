# tests/integration/test_run_executor_fx_snapshot.py
"""P3: executor pins presentation_fx_snapshot at run calculation time.

Two cases:
  (a) EUR-reporting org with a seeded active EUR rate → COMPLETED run carries
      the snapshot {code, usd_rate, as_of_date, source}.
  (b) USD-reporting org (the default) → snapshot is None.

Run-setup copied from test_run_executor_null_audit.py + seed_completed_run
fixture shape in tests/conftest.py.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.services.fx_rates import FxRateService
from idraa.services.run_executor import execute_run


async def _seed_run(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    scenario_id: uuid.UUID,
    created_by: uuid.UUID,
) -> RiskAnalysisRun:
    """Seed a minimal QUEUED SINGLE RiskAnalysisRun."""
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario_id,
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=created_by,
    )
    db.add(run)
    await db.flush()
    return run


@pytest.mark.asyncio
async def test_eur_reporting_run_has_fx_snapshot(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> None:
    """EUR-reporting org + active EUR rate → COMPLETED run has the snapshot."""
    # Set org to EUR reporting and seed an active EUR rate.
    seed_organization.preferred_currency = "EUR"
    await db_session.flush()

    await FxRateService(db_session).upsert_rate(
        seed_organization.id,
        "EUR",
        Decimal("0.92"),
        dt.date(2026, 6, 15),
        "ECB",
        user_id=seed_user.id,
    )

    scenario = await seed_scenario_factory(name="eur-fx-snapshot-test")

    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
    )
    run_id = run.id
    await db_session.commit()

    await execute_run(run_id)

    stmt = (
        select(RiskAnalysisRun)
        .where(RiskAnalysisRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    refreshed = (await db_session.execute(stmt)).scalar_one_or_none()

    assert refreshed is not None
    assert refreshed.status == RunStatus.COMPLETED, refreshed.error_message

    snap = refreshed.presentation_fx_snapshot
    assert snap is not None, "EUR-reporting completed run must carry presentation_fx_snapshot"
    assert snap["code"] == "EUR"
    # usd_rate is stored as str(Decimal) from the DB; compare via Decimal to
    # tolerate trailing zeros (e.g. "0.92000000" vs "0.92").
    assert Decimal(snap["usd_rate"]) == Decimal("0.92")
    assert snap["as_of_date"] == "2026-06-15"
    assert snap["source"] == "ECB"


@pytest.mark.asyncio
async def test_usd_reporting_run_has_no_fx_snapshot(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> None:
    """USD-reporting org (default) → snapshot is None (no conversion needed)."""
    # seed_organization.preferred_currency defaults to "USD"; no rate seeded.
    assert seed_organization.preferred_currency == "USD"

    scenario = await seed_scenario_factory(name="usd-fx-snapshot-test")

    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
    )
    run_id = run.id
    await db_session.commit()

    await execute_run(run_id)

    stmt = (
        select(RiskAnalysisRun)
        .where(RiskAnalysisRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    refreshed = (await db_session.execute(stmt)).scalar_one_or_none()

    assert refreshed is not None
    assert refreshed.status == RunStatus.COMPLETED, refreshed.error_message
    assert refreshed.presentation_fx_snapshot is None, (
        "USD-reporting run must not carry presentation_fx_snapshot"
    )
