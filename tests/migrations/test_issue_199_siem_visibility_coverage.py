"""#199 slice 1 — SIEM lec_det_visibility coverage 0.8 -> 0.3 shipped as a version bump
(b7c1a9e3d2f4). Dirty-then-upgrade pattern of tests/migrations/test_epic_f_control_recuration.py;
alembic logging is read via capsys (alembic/env.py re-runs fileConfig per step). PRE/REV come
from the loaded module; no git calls."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import idraa
from idraa.schemas.control_library import ControlLibraryEntrySeed
from tests.migrations.conftest import add_orm_columns_missing_at_old_revision

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_MIGRATION_PATH = (
    _ROOT / "alembic" / "versions" / "b7c1a9e3d2f4_issue_199_siem_visibility_coverage.py"
)
_SEED_PATH = _ROOT / "data" / "seed_control_library_entries.json"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None  # tests may assert (outside S101 scope)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mod = _load(_MIGRATION_PATH, "_test_mig_b7c1a9e3d2f4")
PRE: str = mod.down_revision
REV: str = mod.revision
SLUG: str = mod._SLUG
SUB_FUNCTION: str = mod._SUB_FUNCTION
OLD: float = mod._OLD_COVERAGE
NEW: float = mod._NEW_COVERAGE
COUNTER = "issue-199 siem visibility coverage: applied={a} already={b} drift={c}"


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_SEED_PATH.read_text(encoding="utf-8")))


def _seed_entry() -> ControlLibraryEntrySeed:
    payload = _payload()
    return next(
        ControlLibraryEntrySeed.model_validate(e) for e in payload["entries"] if e["slug"] == SLUG
    )


def _seed_vis_coverage() -> float:
    return float(
        next(
            a for a in _seed_entry().assignments if a.sub_function.value == SUB_FUNCTION
        ).coverage_default
    )


def _latest(engine: Engine, slug: str = SLUG) -> tuple[str, int]:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT id, version FROM control_library_entries "
                "WHERE slug = :s ORDER BY version DESC LIMIT 1"
            ),
            {"s": slug},
        ).first()
    assert row is not None
    return str(row[0]), int(row[1])


def _assignments(engine: Engine, entry_id: str, version: int) -> dict[str, tuple[Any, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT sub_function, capability_default, coverage_default, reliability_default, "
                "capability_provenance, capability_citations, coverage_provenance, "
                "coverage_citations, reliability_provenance, reliability_citations, id "
                "FROM control_library_entry_assignments "
                "WHERE library_entry_id = :eid AND library_entry_version = :v"
            ),
            {"eid": entry_id, "v": version},
        ).all()
    return {
        str(r[0]): (
            r[1],
            r[2],
            r[3],
            r[4],
            tuple(json.loads(str(r[5]))),
            r[6],
            tuple(json.loads(str(r[7]))),
            r[8],
            tuple(json.loads(str(r[9]))),
        )
        for r in rows
    }


def _expected_from_seed() -> dict[str, tuple[Any, ...]]:
    return {
        a.sub_function.value: (
            a.capability_default,
            a.coverage_default,
            a.reliability_default,
            a.capability_provenance,
            tuple(a.capability_citations),
            a.coverage_provenance,
            tuple(a.coverage_citations),
            a.reliability_provenance,
            tuple(a.reliability_citations),
        )
        for a in _seed_entry().assignments
    }


def _set_stored_coverage(engine: Engine, value: float) -> None:
    """Dirty the stored SIEM lec_det_visibility coverage at the latest version (the
    pre-#199 production state is _OLD_COVERAGE; the fresh seed path already holds the
    seed's value)."""
    entry_id, version = _latest(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE control_library_entry_assignments SET coverage_default = :c "
                "WHERE library_entry_id = :eid AND library_entry_version = :v "
                "AND sub_function = :sf"
            ),
            {"c": value, "eid": entry_id, "v": version, "sf": SUB_FUNCTION},
        )


def _dump_library(engine: Engine) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    with engine.connect() as conn:
        e = conn.execute(
            sa.text("SELECT * FROM control_library_entries ORDER BY id, version")
        ).fetchall()
        a = conn.execute(
            sa.text(
                "SELECT * FROM control_library_entry_assignments "
                "ORDER BY library_entry_id, library_entry_version, sub_function"
            )
        ).fetchall()
    return [tuple(r) for r in e], [tuple(r) for r in a]


def test_dirty_then_upgrade_bumps_and_reinserts_from_json(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _set_stored_coverage(alembic_engine, OLD)
    old_id, old_version = _latest(alembic_engine)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    new_id, new_version = _latest(alembic_engine)
    assert new_id == old_id
    assert new_version == old_version + 1
    assert _assignments(alembic_engine, old_id, old_version) == {}
    got = _assignments(alembic_engine, new_id, new_version)
    assert {k: v[:9] for k, v in got.items()} == _expected_from_seed()
    # Derived from the seed and the module constants, never a retyped literal (A-I1).
    assert abs(float(got[SUB_FUNCTION][1]) - _seed_vis_coverage()) <= 1e-9
    # Seed-coupled by design (A2-N4): the seed was restored to 0.8 (owner ruling 2026-10-21,
    # migration e3a7c5b1d9f2), so a dirty stored OLD (0.8) and the JSON agree and the bump
    # changes no values. Fix THIS test when the seed moves, never the migration.
    assert abs(_seed_vis_coverage() - OLD) <= 1e-9  # true while the seed holds the restored 0.8
    assert COUNTER.format(a=1, b=0, c=0) in err
    with alembic_engine.connect() as conn:
        ids: Sequence[Any] = (
            conn.execute(
                sa.text(
                    "SELECT id FROM control_library_entry_assignments "
                    "WHERE library_entry_id = :eid AND library_entry_version = :v"
                ),
                {"eid": new_id, "v": new_version},
            )
            .scalars()
            .all()
        )
    for rid in ids:
        assert str(rid) == uuid.UUID(hex=str(rid)).hex  # 32 lowercase hex (R-SIEM-10)


def test_fresh_path_applies_a_no_value_change_bump_and_converges(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-SIEM-14. Seed-coupled (A2-N4): with the seed restored to 0.8 (== _OLD_COVERAGE), a
    fresh DB stores 0.8 at this revision, classifies `apply`, and logs `applied=1` with a bump
    that changes no values (the restore migration e3a7c5b1d9f2 then classifies `already`).
    Fix THIS test, never the migration, when the seed moves."""
    alembic_runner.migrate_up_to(PRE)
    entry_id, version = _latest(alembic_engine)
    before = _assignments(alembic_engine, entry_id, version)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert _latest(alembic_engine) == (entry_id, version + 1)
    assert COUNTER.format(a=1, b=0, c=0) in err
    after = _assignments(alembic_engine, entry_id, version + 1)
    assert {k: v[:9] for k, v in after.items()} == _expected_from_seed()
    # the bump changes no values: same cells, new row ids
    assert {k: v[:9] for k, v in after.items()} == {k: v[:9] for k, v in before.items()}


def test_stored_new_coverage_is_already_whatever_the_seed_holds(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seed-independent twin of test_fresh_path_applies_a_no_value_change_bump_and_converges: the stored cell is
    set to the frozen NEW literal (0.3) directly, so the classification is `already` however
    the seed JSON moves — no bump, no write at all."""
    alembic_runner.migrate_up_to(PRE)
    _set_stored_coverage(alembic_engine, NEW)
    entry_id, version = _latest(alembic_engine)
    before = _dump_library(alembic_engine)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert COUNTER.format(a=0, b=1, c=0) in err
    assert _latest(alembic_engine) == (entry_id, version)  # no version change
    assert _dump_library(alembic_engine) == before


def test_drift_value_is_skipped_without_disclosing_it(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _set_stored_coverage(alembic_engine, 0.5)
    entry_id, version = _latest(alembic_engine)
    before = _dump_library(alembic_engine)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert _latest(alembic_engine) == (entry_id, version)
    assert _dump_library(alembic_engine) == before  # no write at all (R-SIEM-9)
    assert COUNTER.format(a=0, b=0, c=1) in err
    # alembic.ini's console formatter is `%(levelname)-5.5s`, so the level prints as
    # "WARNI", never "WARNING" (tests/migrations/test_epic_f_library_curation.py precedent).
    warning = [line for line in err.splitlines() if "drift" in line and "WARNI" in line]
    assert len(warning) == 1
    assert SLUG in warning[0] and SUB_FUNCTION in warning[0]
    assert "0.5" not in warning[0]  # never a value
    assert "Security Information" not in err  # never a name


def test_missing_siem_entry_counts_as_drift(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review Focus 4: a tester DB without the SIEM entry (partial seed)."""
    alembic_runner.migrate_up_to(PRE)
    entry_id, version = _latest(alembic_engine)
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text("DELETE FROM control_library_entry_assignments WHERE library_entry_id = :e"),
            {"e": entry_id},
        )
        conn.execute(sa.text("DELETE FROM control_library_entries WHERE id = :e"), {"e": entry_id})
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert COUNTER.format(a=0, b=0, c=1) in err


def test_downgrade_is_a_documented_noop(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _set_stored_coverage(alembic_engine, OLD)
    alembic_runner.migrate_up_to(REV)
    before = _dump_library(alembic_engine)
    alembic_runner.migrate_down_to(PRE)
    assert _dump_library(alembic_engine) == before


def test_lagging_db_takes_whatever_the_json_holds(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    """R-SIEM-8a / R-T-12a (A-I1): when the seed moves, a
    lagging database on `apply` must converge to the JSON, not RuntimeError at boot. Drive
    the migration's own `_apply(bind, payload)` with a payload whose SIEM coverage is 0.25."""
    alembic_runner.migrate_up_to(PRE)
    _set_stored_coverage(alembic_engine, OLD)
    entry_id, version = _latest(alembic_engine)
    payload = copy.deepcopy(_payload())
    siem = next(e for e in payload["entries"] if e["slug"] == SLUG)
    for a in siem["assignments"]:
        if a["sub_function"] == SUB_FUNCTION:
            a["coverage_default"] = 0.25
    with alembic_engine.begin() as conn:
        applied, already, drift = mod._apply(conn, payload)
    assert (applied, already, drift) == (1, 0, 0)
    assert _latest(alembic_engine) == (entry_id, version + 1)
    got = _assignments(alembic_engine, entry_id, version + 1)
    assert abs(float(got[SUB_FUNCTION][1]) - 0.25) <= 1e-9


def test_apply_requires_exactly_one_siem_entry_in_the_payload(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    payload = copy.deepcopy(_payload())
    payload["entries"] = [e for e in payload["entries"] if e["slug"] != SLUG]
    with alembic_engine.begin() as conn, pytest.raises(RuntimeError, match=SLUG):
        mod._apply(conn, payload)


async def _adopt(async_url: str, entry_id_hex: str, version: int) -> str:
    from idraa.db import _install_sqlite_pragmas, strict_json_dumps
    from idraa.models.enums import IndustryType, OrganizationSize
    from idraa.models.organization import Organization
    from idraa.services.controls import adopt_from_library

    engine = create_async_engine(async_url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as db:
            org = Organization(
                name="issue-199 probe org",
                industry_type=IndustryType.INFORMATION,
                organization_size=OrganizationSize.MEDIUM,
            )
            db.add(org)
            await db.flush()
            control = await adopt_from_library(
                db,
                org_id=org.id,
                user_id=None,
                entry_id=uuid.UUID(hex=entry_id_hex),
                version=version,
            )
            await db.commit()
            return control.id.hex
    finally:
        await engine.dispose()


async def _stale(async_url: str, control_id_hex: str) -> bool:
    from idraa.db import _install_sqlite_pragmas, strict_json_dumps
    from idraa.models.control import Control
    from idraa.services.control_resync import resync_info

    engine = create_async_engine(async_url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as db:
            control = await db.get(Control, uuid.UUID(hex=control_id_hex))
            assert control is not None
            info = await resync_info(db, control)
            assert info is not None
            return info.stale
    finally:
        await engine.dispose()


def _rows(engine: Engine, sql: str, params: dict[str, object]) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(sa.text(sql), params).fetchall()]


def test_adopted_siem_control_survives_unchanged_and_goes_resync_stale(
    alembic_runner: MigrationContext, alembic_engine: Engine, alembic_config: Config
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _set_stored_coverage(alembic_engine, OLD)  # production shape: the clone copies 0.8
    entry_id, version = _latest(alembic_engine)
    url = alembic_config.get_main_option("sqlalchemy.url")
    assert url is not None
    add_orm_columns_missing_at_old_revision(alembic_engine)  # the adopt below writes them
    cid = asyncio.run(_adopt(url, entry_id, version))
    control_before = _rows(alembic_engine, "SELECT * FROM controls WHERE id = :c", {"c": cid})
    cfa_before = _rows(
        alembic_engine,
        "SELECT * FROM control_function_assignments WHERE control_id = :c ORDER BY sub_function",
        {"c": cid},
    )
    assert asyncio.run(_stale(url, cid)) is False
    alembic_runner.migrate_up_to(REV)
    assert (
        _rows(alembic_engine, "SELECT * FROM controls WHERE id = :c", {"c": cid}) == control_before
    )
    assert (
        _rows(
            alembic_engine,
            "SELECT * FROM control_function_assignments WHERE control_id = :c ORDER BY sub_function",
            {"c": cid},
        )
        == cfa_before
    )  # R-SIEM-11
    assert asyncio.run(_stale(url, cid)) is True
