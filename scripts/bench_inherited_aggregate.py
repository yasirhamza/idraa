# scripts/bench_inherited_aggregate.py
"""#299 operational check (spec O1): time ONE AGGREGATE run with org-wide
(inherited) meta controls plus explicitly linked LEC controls per scenario,
through the real create_and_dispatch -> execute_run path at the executor's
production attribution constants (MAX_ATTRIBUTION_CONTROLS /
MAX_ATTRIBUTION_TOTAL_EVALS / MAX_COMPOSITION_CACHE_ENTRIES are module
constants). It MEASURES the persisted run rather than echoing its inputs: the
inherited ids and ``:meta`` adjustment rows actually present per scenario,
players per scenario, how many Shapley values are non-null, and the ensemble
state and draw count. It prints wall-clock and peak RSS and exits non-zero if
inheritance is missing or (unless --allow-degraded) attribution or the ensemble
degraded.

The default run is the plan's point: 10 scenarios x (5 inherited + 3 linked).
Heavier O1 points degrade by design (sampled Shapley above 12 players,
run-wide evaluation budget, ensemble draws), so run them with --allow-degraded.

Run against a SCRATCH DB only (Settings.database_url has no env prefix, so the
variable is DATABASE_URL; the script refuses any URL that does not contain
``bench299``; there is no override, because against a real DB the bench would add
a second Organization and sole-org resolution takes whichever row comes first).
Settings refuses to boot with the default SESSION_SECRET, so set one for both
commands:

    export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    export DATABASE_URL=sqlite+aiosqlite:////path/to/scratch/bench299.db
    uv run alembic upgrade head
    uv run python scripts/bench_inherited_aggregate.py
    # 13 players per scenario (sampled Shapley, ensemble degrades):
    uv run python scripts/bench_inherited_aggregate.py --linked 8 --allow-degraded
    # production-scale shape; --cache-cap none = unbounded composition caches
    # (before/after comparison only):
    uv run python scripts/bench_inherited_aggregate.py --scenarios 400 --linked 7 \
        --allow-degraded [--cache-cap none]

``elapsed_s`` is wall-clock around ``execute_run``. Run-row timestamps are not a
substitute: ``completed_at`` is stamped before attribution, so a Fly timing must
come from this wall-clock, not from the run row.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from collections.abc import Sequence
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
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from idraa.models.threat_community import canonical_threat_community_id
from idraa.services import run_executor
from idraa.services.run_executor import MAX_ATTRIBUTION_CONTROLS, MAX_ATTRIBUTION_TOTAL_EVALS
from idraa.services.runs import RunService

resource: ModuleType | None
try:
    import resource
except ImportError:  # pragma: no cover — POSIX-only
    resource = None

# Defaults = the plan's point: 10 scenarios x (5 inherited + 3 linked) = 8 players.
DEFAULT_SCENARIOS = 10
DEFAULT_INHERITED = 5
DEFAULT_LINKED = 3
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


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument(
        "--scenarios", type=int, default=DEFAULT_SCENARIOS, help="AGGREGATE size (>= 2)"
    )
    ap.add_argument(
        "--inherited",
        type=int,
        default=DEFAULT_INHERITED,
        help=f"org-wide META controls inherited by every scenario (0..{len(META_SFS)})",
    )
    ap.add_argument(
        "--linked",
        type=int,
        default=DEFAULT_LINKED,
        help="explicitly linked LEC controls per scenario, distinct per scenario",
    )
    ap.add_argument(
        "--iterations", type=int, default=None, help="default: Settings.mc_iterations_default"
    )
    ap.add_argument(
        "--cache-cap",
        default="default",
        help="composition-cache entry cap: 'default' (the executor constant), 'none' "
        "(unbounded; before/after comparison only) or an integer",
    )
    ap.add_argument(
        "--allow-degraded",
        action="store_true",
        help="report, do not fail on, unavailable attribution or a degraded ensemble",
    )
    args = ap.parse_args(argv)
    if args.scenarios < 2:
        ap.error("--scenarios must be >= 2 (a single scenario is a SINGLE run, not AGGREGATE)")
    if not 0 <= args.inherited <= len(META_SFS):
        ap.error(f"--inherited must be in 0..{len(META_SFS)}")
    if args.linked < 1:
        ap.error("--linked must be >= 1")
    if args.cache_cap not in ("default", "none") and not args.cache_cap.isdigit():
        ap.error("--cache-cap must be 'default', 'none' or a non-negative integer")
    return args


async def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    n_scenarios, n_inherited, n_linked = args.scenarios, args.inherited, args.linked
    iterations = args.iterations or get_settings().mc_iterations_default
    if args.cache_cap == "none":
        run_executor.MAX_COMPOSITION_CACHE_ENTRIES = None
    elif args.cache_cap != "default":
        run_executor.MAX_COMPOSITION_CACHE_ENTRIES = int(args.cache_cap)
    cache_cap = run_executor.MAX_COMPOSITION_CACHE_ENTRIES

    url = get_settings().database_url
    if "bench299" not in url:  # no override: a real DB would gain a second Organization
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
        for i, sf in enumerate(META_SFS[:n_inherited]):
            c, a = _control(org.id, f"meta-{i}", sf, org_wide=True)
            session.add_all([c, a])
        scenario_ids: list[uuid.UUID] = []
        for s_i in range(n_scenarios):
            s = _scenario(org.id, f"bench-{s_i}")
            session.add(s)
            linked: list[Control] = []
            for l_i in range(n_linked):
                c, a = _control(
                    org.id, f"lec-{s_i}-{l_i}", LEC_SFS[l_i % len(LEC_SFS)], org_wide=False
                )
                session.add_all([c, a])
                linked.append(c)
            # ScenarioControl has no relationship() to Scenario/Control, so the unit of
            # work does not order its INSERT after theirs: flush them first.
            await session.flush()
            scenario_ids.append(s.id)
            session.add_all([ScenarioControl(scenario_id=s.id, control_id=c.id) for c in linked])
        await session.commit()

        run = await RunService(session).create_and_dispatch(
            organization_id=org.id,
            scenario_ids=scenario_ids,
            mc_iterations_override=iterations,
            created_by=None,  # type: ignore[arg-type]  # nullable column; no user in a scratch DB
            background_tasks=BackgroundTasks(),  # never executed -> run stays QUEUED
        )
        run_id = run.id

    t0 = time.perf_counter()
    await run_executor.execute_run(run_id)
    elapsed = time.perf_counter() - t0

    # Everything below is read back from the persisted run, not from the inputs above.
    async with sessionmaker() as session:
        run = await session.get(RiskAnalysisRun, run_id)
        if run is None:
            print(f"run {run_id} vanished after execute_run", file=sys.stderr)
            return 1
        status = run.status
        inherited_map = run.inherited_control_ids_per_scenario or {}
        per_scenario = (run.simulation_results or {}).get("per_scenario", [])
        ensemble = run.weight_robustness or {}
        sids = list(run.aggregate_scenario_ids or [])
    inherited_counts = {len(inherited_map.get(sid, [])) for sid in sids}
    meta_rows, players = set(), set()
    adjustments = non_null = unavailable = 0
    for ps in per_scenario:
        adj = ps.get("control_adjustments", [])
        meta_rows.add(sum(1 for a in adj if str(a.get("control_id", "")).endswith(":meta")))
        players.add(len(adj))
        values = [a.get("shapley_value") for a in adj]
        adjustments += len(values)
        non_null += sum(v is not None for v in values)
        unavailable += any(v is None for v in values)
    draws_used = ensemble.get("draws_used")
    draws_configured = (ensemble.get("band") or {}).get("draws")
    ensemble_state = ensemble.get("state")

    failures: list[str] = []
    if status != RunStatus.COMPLETED:
        failures.append(f"run status is {status!r}, not completed")
    if len(sids) != n_scenarios or len(per_scenario) != n_scenarios:
        failures.append(
            f"expected {n_scenarios} scenarios, run has {len(sids)}/{len(per_scenario)}"
        )
    if inherited_counts != {n_inherited}:
        failures.append(
            f"inheritance missing: inherited ids per scenario {sorted(inherited_counts)}, "
            f"expected [{n_inherited}]"
        )
    if meta_rows != {n_inherited}:
        failures.append(
            f":meta adjustment rows per scenario {sorted(meta_rows)}, expected [{n_inherited}]"
        )
    if players != {n_inherited + n_linked}:
        failures.append(
            f"players per scenario {sorted(players)}, expected [{n_inherited + n_linked}]"
        )
    if not args.allow_degraded:
        if unavailable:
            failures.append(f"attribution unavailable for {unavailable} scenario(s)")
        if ensemble_state != "ok" or ensemble.get("degraded") or draws_used != draws_configured:
            failures.append(
                f"ensemble degraded: state={ensemble_state!r} draws={draws_used}/{draws_configured}"
            )

    rss_mb = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / (1024 * 1024 if sys.platform == "darwin" else 1024)
        if resource
        else 0.0
    )
    print(
        f"status={status.value if isinstance(status, RunStatus) else status} "
        f"scenarios={len(sids)} mc_iterations={iterations} "
        f"inherited_ids_per_scenario={sorted(inherited_counts)} "
        f"meta_rows_per_scenario={sorted(meta_rows)} players_per_scenario={sorted(players)} "
        f"shapley_non_null={non_null}/{adjustments} "
        f"scenarios_with_attribution_unavailable={unavailable} "
        f"ensemble_state={ensemble_state} ensemble_draws={draws_used}/{draws_configured} "
        f"max_attribution_controls={MAX_ATTRIBUTION_CONTROLS} "
        f"total_eval_budget={MAX_ATTRIBUTION_TOTAL_EVALS} composition_cache_cap={cache_cap} "
        f"elapsed_s={elapsed:.2f} peak_rss_mb={rss_mb:.0f}"
    )
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
