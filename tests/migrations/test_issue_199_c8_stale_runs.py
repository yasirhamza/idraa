"""#199 slice 1 — c4d2e8f1a6b9 flags stale every COMPLETED, not-yet-stale run whose OWN
controls_snapshot shows a control carrying vmc_corr_treatment_selection (the C8 engine change
raises its score), falling back to control_ids_used ∩ current carriers (org-keyed) for runs whose
snapshot carries no assignment detail (V1 / empty); one audit_log row per run (user_id NULL, ids
no-hyphen hex, a reason). Rows are seeded through the REAL ORM in a separate asyncio.run() (the
test_epic_f_control_recuration.py probe pattern), so the fixture shape cannot drift from
production; snapshots are built with the real ControlSnapshotV3 pydantic model."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import idraa

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_MIGRATION_PATH = _ROOT / "alembic" / "versions" / "c4d2e8f1a6b9_issue_199_c8_stale_runs.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mod = _load(_MIGRATION_PATH, "_test_mig_c4d2e8f1a6b9")
PRE: str = mod.down_revision
REV: str = mod.revision
COUNTER = "issue-199 c8 stale runs: controls={c} flagged={f}"
NAME_SENTINEL = "ZZ-CONTROL-NAME-SENTINEL"
TS = "vmc_corr_treatment_selection"
IMPL = "vmc_corr_implementation"


def _v3_snapshot(control_id: uuid.UUID, sub_functions: list[str]) -> dict[str, Any]:
    """A real V3 snapshot dict for one control (the shape run_executor.py:1943 persists)."""
    from idraa.models.enums import SUB_FUNCTION_UNITS, FairCamSubFunction
    from idraa.schemas.run_snapshot import ControlFunctionAssignmentSnapshotDTO, ControlSnapshotV3

    return ControlSnapshotV3(
        snapshot_version=3,
        control_id=str(control_id),
        name=NAME_SENTINEL,
        domains=["variance_management"],
        type="technical",
        assignments=[
            ControlFunctionAssignmentSnapshotDTO(
                sub_function=FairCamSubFunction(sf),
                capability_value=None,
                coverage=0.8,
                reliability=0.8,
                unit_type=SUB_FUNCTION_UNITS[FairCamSubFunction(sf)],
            )
            for sf in sub_functions
        ],
    ).model_dump(mode="json")


async def _seed(async_url: str) -> dict[str, str]:
    """Returns hex ids. Controls: ts_control (TS + Impl rows), impl_control (Impl only),
    ts_only_control (TS row only, no Impl), removed_control (TS row, deleted after the run),
    and in a SECOND org foreign_ts (TS row). Runs (all in org 1 unless named otherwise):
      r_ts        COMPLETED, V3 snapshot shows TS (+ Impl)                 -> flagged
      r_impl      COMPLETED, V3 snapshot shows Impl only                    -> not flagged
      r_already   COMPLETED, already stale, snapshot shows TS               -> untouched, not counted
      r_queued / r_running / r_failed  wrong status, snapshot shows TS      -> not flagged
      r_empty     COMPLETED, no controls, empty snapshot                    -> not flagged
      r_v1        COMPLETED, V1-shaped snapshot (no assignments), control_ids_used names ts_control
                                                                           -> flagged (fallback)
      r_foreign   COMPLETED, empty snapshot, control_ids_used names org 2's foreign_ts
                                                                           -> NOT flagged (org-keyed)
      r_removed   COMPLETED, V3 snapshot shows TS on a control whose TS row is deleted after
                  the run                                                   -> flagged (snapshot wins)
      r_ts_only   COMPLETED, V3 snapshot shows TS ONLY (no Impl, no vmc_id_*) -> flagged
                  (SUPERSET, disclosed: its numbers cannot change — E_corr is 0 before and
                  after — but the criterion is sub-function membership; R-DEP-4, M2-I2)
    """
    from idraa.db import _install_sqlite_pragmas, strict_json_dumps
    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import ControlType, FairCamSubFunction, IndustryType, OrganizationSize
    from idraa.models.organization import Organization
    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    engine = create_async_engine(async_url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    out: dict[str, str] = {}
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as db:
            org = Organization(
                name="issue-199 c8 probe org",
                industry_type=IndustryType.INFORMATION,
                organization_size=OrganizationSize.MEDIUM,
            )
            org2 = Organization(
                name="issue-199 c8 probe org 2",
                industry_type=IndustryType.INFORMATION,
                organization_size=OrganizationSize.MEDIUM,
            )
            db.add_all([org, org2])
            await db.flush()

            def _control(owner: Organization) -> Control:
                c = Control(
                    organization_id=owner.id, name=NAME_SENTINEL, type=ControlType.TECHNICAL
                )
                db.add(c)
                return c

            ts = _control(org)
            impl = _control(org)
            ts_only = _control(org)
            removed = _control(org)
            foreign = _control(org2)
            await db.flush()
            rows = [
                (ts, FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION),
                (ts, FairCamSubFunction.VMC_CORR_IMPLEMENTATION),
                (impl, FairCamSubFunction.VMC_CORR_IMPLEMENTATION),
                (ts_only, FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION),
                (removed, FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION),
                (foreign, FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION),
            ]
            for c, sf in rows:
                db.add(
                    ControlFunctionAssignment(
                        control_id=c.id,
                        organization_id=c.organization_id,
                        sub_function=sf,
                        capability_value=None,
                        coverage=0.8,
                        reliability=0.8,
                    )
                )
            await db.flush()
            out["ts_control"], out["impl_control"] = ts.id.hex, impl.id.hex
            out["ts_only_control"] = ts_only.id.hex
            out["removed_control"], out["foreign_control"] = removed.id.hex, foreign.id.hex

            def _run(
                key: str,
                controls: list[Control],
                status: RunStatus,
                *,
                snapshot: list[dict[str, Any]] | None,
                stale: bool = False,
                owner: Organization = org,
            ) -> None:
                r = RiskAnalysisRun(
                    organization_id=owner.id,
                    scenario_id=None,
                    run_type=RunType.AGGREGATE,
                    status=status,
                    mc_iterations=1000,
                    inputs_hash=f"h-{key}",
                    control_ids_used=[str(c.id) for c in controls],
                    is_stale=stale,
                    controls_snapshot=snapshot if snapshot is not None else [],
                )
                db.add(r)
                out[key] = r.id.hex

            snap_ts = [_v3_snapshot(ts.id, [TS, IMPL]), _v3_snapshot(impl.id, [IMPL])]
            snap_impl = [_v3_snapshot(impl.id, [IMPL])]
            v1 = [
                {
                    "snapshot_version": 1,
                    "control_id": str(ts.id),
                    "name": NAME_SENTINEL,
                    "control_strength": 0.5,
                    "control_reliability": 0.8,
                    "control_coverage": 0.8,
                    "domain": "variance_management",
                    "function": "correct",
                    "type": "technical",
                }
            ]
            _run("r_ts", [ts, impl], RunStatus.COMPLETED, snapshot=snap_ts)
            _run("r_impl", [impl], RunStatus.COMPLETED, snapshot=snap_impl)
            _run("r_already", [ts], RunStatus.COMPLETED, snapshot=snap_ts, stale=True)
            _run("r_queued", [ts], RunStatus.QUEUED, snapshot=snap_ts)
            _run("r_running", [ts], RunStatus.RUNNING, snapshot=snap_ts)
            _run("r_failed", [ts], RunStatus.FAILED, snapshot=snap_ts)
            _run("r_empty", [], RunStatus.COMPLETED, snapshot=[])
            _run("r_v1", [ts], RunStatus.COMPLETED, snapshot=v1)
            _run("r_foreign", [foreign], RunStatus.COMPLETED, snapshot=[])
            _run(
                "r_removed",
                [removed],
                RunStatus.COMPLETED,
                snapshot=[_v3_snapshot(removed.id, [TS])],
            )
            _run(
                "r_ts_only",
                [ts_only],
                RunStatus.COMPLETED,
                snapshot=[_v3_snapshot(ts_only.id, [TS])],
            )
            await db.flush()
            # The analyst later removed Treatment Selection from `removed` (a form save deletes
            # missing sub-functions, services/controls.py:208, and never flags runs stale).
            await db.execute(
                sa.delete(ControlFunctionAssignment).where(
                    ControlFunctionAssignment.control_id == removed.id
                )
            )
            await db.commit()
    finally:
        await engine.dispose()
    return out


def _is_stale(engine: Engine, run_hex: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                sa.text("SELECT is_stale FROM risk_analysis_runs WHERE id = :id"), {"id": run_hex}
            ).scalar_one()
        )


def _audit_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                sa.text(
                    "SELECT id, organization_id, entity_type, entity_id, user_id, action, changes "
                    "FROM audit_log WHERE action = 'run.flagged_stale' ORDER BY entity_id"
                )
            )
            .mappings()
            .all()
        ]


@pytest.fixture
def seeded(alembic_runner: MigrationContext, alembic_config: Config) -> dict[str, str]:
    alembic_runner.migrate_up_to(PRE)
    url = alembic_config.get_main_option("sqlalchemy.url")
    assert url is not None
    return asyncio.run(_seed(url))


def test_only_completed_unstale_affected_runs_are_flagged(
    seeded: dict[str, str],
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert _is_stale(alembic_engine, seeded["r_ts"]) is True
    assert _is_stale(alembic_engine, seeded["r_impl"]) is False
    assert _is_stale(alembic_engine, seeded["r_already"]) is True  # was already stale
    for key in ("r_queued", "r_running", "r_failed", "r_empty"):
        assert _is_stale(alembic_engine, seeded[key]) is False, key
    # Three current carriers (ts, ts_only, foreign; `removed` lost its row) -> controls=3;
    # flagged: r_ts, r_v1 (fallback), r_removed (snapshot), r_ts_only (superset) = 4.
    # r_already neither rewritten nor counted.
    assert COUNTER.format(c=3, f=4) in err
    assert NAME_SENTINEL not in err


FLAGGED_KEYS = ("r_ts", "r_v1", "r_removed", "r_ts_only")


def test_ts_only_run_is_flagged_as_a_disclosed_superset(
    seeded: dict[str, str], alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    """R-DEP-4 / R-T-13a (plan-gate M2-I2): a run whose snapshot carries Treatment Selection
    WITHOUT Implementation is flagged although its stored numbers cannot change (E_corr is 0
    before and after). This pins the superset as intended behaviour, so nobody later
    'tightens' the predicate from first principles without reading the disclosure."""
    alembic_runner.migrate_up_to(REV)
    assert _is_stale(alembic_engine, seeded["r_ts_only"]) is True
    assert seeded["r_ts_only"] in {str(r["entity_id"]) for r in _audit_rows(alembic_engine)}


def test_snapshot_membership_survives_a_later_assignment_delete(
    seeded: dict[str, str], alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    """R-T-13a / M-I4: the run's own snapshot carried TS; the current row is gone; still flagged."""
    alembic_runner.migrate_up_to(REV)
    assert _is_stale(alembic_engine, seeded["r_removed"]) is True


def test_v1_snapshot_falls_back_to_current_carriers_in_the_same_org(
    seeded: dict[str, str], alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(REV)
    assert _is_stale(alembic_engine, seeded["r_v1"]) is True


def test_fallback_never_matches_a_foreign_org_carrier(
    seeded: dict[str, str], alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    """Sec-N1: a run in org 1 naming org 2's carrier (only possible with a corrupt
    control_ids_used) is not flagged and gets no audit row in org 1."""
    alembic_runner.migrate_up_to(REV)
    assert _is_stale(alembic_engine, seeded["r_foreign"]) is False
    assert seeded["r_foreign"] not in {str(r["entity_id"]) for r in _audit_rows(alembic_engine)}


def test_one_audit_row_per_flagged_run_with_system_actor_hex_ids_and_reason(
    seeded: dict[str, str], alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(REV)
    rows = _audit_rows(alembic_engine)
    assert {str(r["entity_id"]) for r in rows} == {seeded[k] for k in FLAGGED_KEYS}
    for row in rows:
        assert row["entity_type"] == "risk_analysis_run"
        assert row["user_id"] is None
        assert str(row["id"]) == uuid.UUID(hex=str(row["id"])).hex
        assert json.loads(str(row["changes"])) == {
            "is_stale": [False, True],
            "reason": ["", "issue-199 C8 implementation-anchored correction"],
        }


def test_second_application_is_idempotent(
    seeded: dict[str, str],
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from alembic import command

    alembic_runner.migrate_up_to(REV)
    assert len(_audit_rows(alembic_engine)) == len(FLAGGED_KEYS)
    command.stamp(alembic_config, PRE)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert COUNTER.format(c=3, f=0) in err
    assert len(_audit_rows(alembic_engine)) == len(FLAGGED_KEYS)


@pytest.mark.parametrize("json_text", ["null", "[]"], ids=["json-null", "json-empty-list"])
def test_run_with_json_null_or_empty_columns_is_ignored(
    seeded: dict[str, str], alembic_runner: MigrationContext, alembic_engine: Engine, json_text: str
) -> None:
    """Review Focus 3 (S2-I2 / A2-I2): both columns are NOT NULL (3e5d550fcade:54-55), so the
    reachable 'nothing there' shapes are the JSON texts 'null' (an ORM write of None into a
    JSON column) and '[]'. Neither may crash or flag."""
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE risk_analysis_runs SET control_ids_used = :j, controls_snapshot = :j "
                "WHERE id = :id"
            ),
            {"j": json_text, "id": seeded["r_empty"]},
        )
    alembic_runner.migrate_up_to(REV)
    assert _is_stale(alembic_engine, seeded["r_empty"]) is False


def test_no_carriers_and_no_snapshot_hits_means_nothing_flagged(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    assert COUNTER.format(c=0, f=0) in capsys.readouterr().err
    assert _audit_rows(alembic_engine) == []


def test_downgrade_is_a_documented_noop(
    seeded: dict[str, str], alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(REV)
    assert _is_stale(alembic_engine, seeded["r_ts"]) is True
    alembic_runner.migrate_down_to(PRE)
    assert _is_stale(alembic_engine, seeded["r_ts"]) is True
    assert len(_audit_rows(alembic_engine)) == len(FLAGGED_KEYS)


def test_run_is_affected_is_pure_and_shape_tolerant() -> None:
    """The predicate Task 8's sweep reuses: snapshot detail wins; no detail -> org-keyed fallback."""
    cid, org = uuid.uuid4(), uuid.uuid4()
    carriers = {(org.hex, cid.hex)}
    v3 = json.dumps([{"control_id": str(cid), "assignments": [{"sub_function": TS}]}])
    v3_impl = json.dumps([{"control_id": str(cid), "assignments": [{"sub_function": IMPL}]}])
    v1 = json.dumps([{"snapshot_version": 1, "control_id": str(cid)}])
    ids = json.dumps([str(cid)])
    assert mod.run_is_affected(v3, "[]", org.hex, set()) is True  # snapshot alone decides
    assert mod.run_is_affected(v3_impl, ids, org.hex, carriers) is False  # detail says no TS
    assert mod.run_is_affected(v1, ids, org.hex, carriers) is True  # fallback, same org
    assert mod.run_is_affected(v1, ids, uuid.uuid4().hex, carriers) is False  # foreign org
    assert mod.run_is_affected(None, None, org.hex, carriers) is False
    assert mod.run_is_affected("not json", "not json", org.hex, carriers) is False
    assert (
        mod.run_is_affected("[]", json.dumps([cid.hex]), org.hex, carriers) is True
    )  # hex spelling
