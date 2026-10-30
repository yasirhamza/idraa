# scripts/bench_inherited_aggregate.py
"""#299 operational check (spec O1): time ONE AGGREGATE run of 10 scenarios
with 5 inherited org-wide meta controls + 3 linked LEC controls each, through
the real create_and_dispatch -> execute_run path at the executor's production
attribution constants (MAX_ATTRIBUTION_CONTROLS / MAX_ATTRIBUTION_TOTAL_EVALS
are module constants — nothing to configure). Prints wall-clock and peak RSS.

Run against a SCRATCH DB only (Settings.database_url has no env prefix, so the
variable is DATABASE_URL; the script refuses any URL that does not contain
``bench299``):

    export DATABASE_URL=sqlite+aiosqlite:////path/to/scratch/bench299.db
    uv run alembic upgrade head
    uv run python scripts/bench_inherited_aggregate.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import ModuleType

from fastapi import BackgroundTasks

from idraa.config import get_settings
from idraa.db import _get_sessionmaker
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    IndustryType,
    OrganizationSize,
    ScenarioType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from idraa.models.threat_community import canonical_threat_community_id
from idraa.services.run_executor import (
    MAX_ATTRIBUTION_CONTROLS,
    MAX_ATTRIBUTION_TOTAL_EVALS,
    execute_run,
)
from idraa.services.runs import RunService

resource: ModuleType | None
try:
    import resource
except ImportError:  # pragma: no cover — POSIX-only
    resource = None

N_SCENARIOS = 10
N_INHERITED = 5
N_LEC_PER_SCENARIO = 3
META_SFS = [
    FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
    FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
    FairCamSubFunction.DSC_PREV_COMMUNICATION,
]
LEC_SFS = [
    FairCamSubFunction.LEC_PREV_RESISTANCE,
    FairCamSubFunction.LEC_DET_VISIBILITY,
    FairCamSubFunction.LEC_RESP_RESILIENCE,
]


def _control(
    org_id: uuid.UUID, name: str, sf: FairCamSubFunction, *, org_wide: bool
) -> tuple[Control, ControlFunctionAssignment]:
    c = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("1000"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        org_wide_meta=org_wide,
    )
    a = ControlFunctionAssignment(
        control_id=c.id,
        organization_id=org_id,
        sub_function=sf,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.85,
        confirmed_by_user_at=datetime.now(UTC),
    )
    return c, a


def _scenario(org_id: uuid.UUID, name: str) -> Scenario:
    return Scenario(
        threat_community_id=canonical_threat_community_id("cybercriminals"),
        threat_community_version=1,
        threat_community_provenance="assigned",
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 400_000, "high": 3_000_000},
        status=EntityStatus.ACTIVE,
        created_by=None,
    )


async def main() -> int:
    url = get_settings().database_url
    if "bench299" not in url and os.environ.get("BENCH299_ALLOW_ANY_DB") != "1":
        print(
            f"refusing to run against {url!r}; point DATABASE_URL at a scratch bench299 DB",
            file=sys.stderr,
        )
        return 2
    sessionmaker = _get_sessionmaker()
    async with sessionmaker() as session:
        org = Organization(
            name=f"bench-299-{uuid.uuid4().hex[:8]}",
            industry_type=IndustryType.MANUFACTURING,
            organization_size=OrganizationSize.MEDIUM,
        )
        session.add(org)
        await session.flush()
        for i, sf in enumerate(META_SFS[:N_INHERITED]):
            c, a = _control(org.id, f"meta-{i}", sf, org_wide=True)
            session.add_all([c, a])
        scenario_ids: list[uuid.UUID] = []
        for s_i in range(N_SCENARIOS):
            s = _scenario(org.id, f"bench-{s_i}")
            session.add(s)
            await session.flush()
            scenario_ids.append(s.id)
            for l_i in range(N_LEC_PER_SCENARIO):
                c, a = _control(
                    org.id, f"lec-{s_i}-{l_i}", LEC_SFS[l_i % len(LEC_SFS)], org_wide=False
                )
                session.add_all([c, a])
                # ScenarioControl has no relationship() to Control, so the unit of work
                # does not order its INSERT after the control's: flush the control first.
                await session.flush()
                session.add(ScenarioControl(scenario_id=s.id, control_id=c.id))
        await session.commit()

        run = await RunService(session).create_and_dispatch(
            organization_id=org.id,
            scenario_ids=scenario_ids,
            mc_iterations_override=get_settings().mc_iterations_default,
            created_by=None,  # type: ignore[arg-type]  # nullable column; no user in a scratch DB
            background_tasks=BackgroundTasks(),  # never executed -> run stays QUEUED
        )
        run_id = run.id

    t0 = time.perf_counter()
    await execute_run(run_id)
    elapsed = time.perf_counter() - t0

    async with sessionmaker() as session:
        from idraa.models.risk_analysis_run import RiskAnalysisRun

        run = await session.get(RiskAnalysisRun, run_id)
        if run is None:
            print(f"run {run_id} vanished after execute_run", file=sys.stderr)
            return 1
        status = run.status
        per_scenario = (run.simulation_results or {}).get("per_scenario", [])
        unavailable = sum(
            1
            for ps in per_scenario
            if any(a.get("shapley_value") is None for a in ps.get("control_adjustments", []))
        )
    rss_mb = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / (1024 * 1024 if sys.platform == "darwin" else 1024)
        if resource
        else 0.0
    )
    print(
        f"status={status.value if isinstance(status, RunStatus) else status} "
        f"scenarios={N_SCENARIOS} inherited={N_INHERITED} lec_per_scenario={N_LEC_PER_SCENARIO} "
        f"mc_iterations={get_settings().mc_iterations_default} "
        f"max_attribution_controls={MAX_ATTRIBUTION_CONTROLS} "
        f"total_eval_budget={MAX_ATTRIBUTION_TOTAL_EVALS} "
        f"elapsed_s={elapsed:.1f} peak_rss_mb={rss_mb:.0f} "
        f"scenarios_with_attribution_unavailable={unavailable}"
    )
    return 0 if status == RunStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
