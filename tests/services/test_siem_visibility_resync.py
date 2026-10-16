"""src/idraa/services/siem_visibility_resync.py — partition (A/B/C/D/X/current), dry-run
write-freedom via PRAGMA query_only, surgical --apply on class A only, refuse-before-bump.
Built on the REAL ORM/services (adopt_from_library, resync_info) in a per-test SQLite file whose
engine THIS test owns and disposes, so sha256 comparisons are deterministic. Every name below is a
synthetic sentinel."""

from __future__ import annotations

import hashlib
import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import idraa
from idraa.db import _install_sqlite_pragmas, strict_json_dumps
from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.control_library import ControlLibraryEntry, ControlLibraryEntryAssignment
from idraa.models.enums import ControlType, IndustryType, OrganizationSize
from idraa.models.enums import FairCamSubFunction as F
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.services import siem_visibility_resync as svc
from idraa.services.control_resync import resync_info
from idraa.services.controls import adopt_from_library
from tests.conftest import _create_schema

NAME = "ZZ-ENTRY-NAME-SENTINEL"
ORG = "ZZ-ORG-SENTINEL"
VIS = "lec_det_visibility"
MON = "lec_det_monitoring"
SIEM_ASSIGNMENTS: tuple[tuple[F, float | None], ...] = (
    (F.LEC_DET_MONITORING, None),
    (F.LEC_DET_RECOGNITION, 0.7),
    (F.VMC_ID_CONTROL_MONITORING, 0.7),
    (F.LEC_DET_VISIBILITY, 0.7),
)
_MIGRATION = (
    Path(idraa.__file__).resolve().parent.parent.parent
    / "alembic"
    / "versions"
    / "b7c1a9e3d2f4_issue_199_siem_visibility_coverage.py"
)


def _target(vis_cov: float, mon_cov: float = 0.8) -> dict[str, svc.Triple]:
    return {
        MON: (None, mon_cov, 0.8),
        "lec_det_recognition": (0.7, 0.8, 0.8),
        "vmc_id_control_monitoring": (0.7, 0.8, 0.8),
        VIS: (0.7, vis_cov, 0.8),
    }


def _snap(vis_cov: float, mon_cov: float = 0.8, *, with_vis: bool = True) -> dict[str, Any]:
    rows = [
        {"sub_function": MON, "capability": None, "coverage": mon_cov, "reliability": 0.8},
        {
            "sub_function": "lec_det_recognition",
            "capability": 0.7,
            "coverage": 0.8,
            "reliability": 0.8,
        },
        {
            "sub_function": "vmc_id_control_monitoring",
            "capability": 0.7,
            "coverage": 0.8,
            "reliability": 0.8,
        },
    ]
    if with_vis:
        rows.append(
            {"sub_function": VIS, "capability": 0.7, "coverage": vis_cov, "reliability": 0.8}
        )
    return {"version": 1, "assignments": rows}


def _rows(
    vis_cov: float, *, confirmed: bool = False, mon_cov: float = 0.8
) -> list[tuple[str, float, bool]]:
    return [
        (MON, mon_cov, False),
        ("lec_det_recognition", 0.8, False),
        ("vmc_id_control_monitoring", 0.8, False),
        (VIS, vis_cov, confirmed),
    ]


# ---- pure helpers --------------------------------------------------------------------------


def test_constants_match_the_migration_module() -> None:
    """Sec-I2(b): the service's frozen literals equal the migration's — one source of truth each,
    pinned equal rather than imported across the alembic/src boundary."""
    spec = importlib.util.spec_from_file_location("_mig_b7c1", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert svc.OLD_COVERAGE == mod._OLD_COVERAGE
    assert svc.NEW_COVERAGE == mod._NEW_COVERAGE
    assert svc.SUB_FUNCTION == mod._SUB_FUNCTION
    assert svc.SIEM_SLUG == mod._SLUG


def test_classify_siem_control_six_classes() -> None:
    target = _target(0.3)
    snap = _snap(0.8)
    assert svc.classify_siem_control(snap, _rows(0.8), target=target) == "A"
    assert svc.classify_siem_control(snap, _rows(0.8, confirmed=True), target=target) == "B"
    assert svc.classify_siem_control(snap, _rows(0.5), target=target) == "C"  # tuned Visibility
    assert svc.classify_siem_control(snap, _rows(0.8)[:3], target=target) == "C"  # set differs
    assert (
        svc.classify_siem_control(
            snap, [*_rows(0.8), ("lec_prev_avoidance", 0.8, False)], target=target
        )
        == "C"
    )
    # R-SIEM-16b: tuning on ANOTHER member's current row does not demote (library did not change it)
    assert svc.classify_siem_control(snap, _rows(0.8, mon_cov=0.1), target=target) == "A"
    assert svc.classify_siem_control(None, _rows(0.8), target=target) == "D"
    # R-SIEM-16a -> X: the library changed another cell between the adopted and latest versions
    assert svc.classify_siem_control(snap, _rows(0.8), target=_target(0.3, mon_cov=0.7)) == "X"
    # X: snapshot's Visibility coverage is not OLD (a different delta than this slice ships)
    assert svc.classify_siem_control(_snap(0.6), _rows(0.6), target=target) == "X"
    # X: latest entry's Visibility coverage is not NEW (bump not applied / drift)
    assert svc.classify_siem_control(snap, _rows(0.8), target=_target(0.8)) == "X"
    # X: pre-Epic-F snapshot without lec_det_visibility (the library added it later)
    assert (
        svc.classify_siem_control(_snap(0.8, with_vis=False), _rows(0.8)[:3], target=target) == "X"
    )
    # X: malformed snapshot
    assert svc.classify_siem_control({"assignments": "garbage"}, _rows(0.8), target=target) == "X"


def test_entry_fields_match_is_the_consent_page_oracle() -> None:
    """R-SIEM-16a (iv) / plan-gate Sec2-I2: class A also requires every entry-level field the
    consent page diffs (control_resync._entry_field_values keys) to equal the snapshot's;
    `type` is skipped exactly as resync_info skips it. A missing key counts as a mismatch."""
    fields = {
        "name": NAME,
        "description": "d" * 30,
        "type": "technical",
        "annual_cost": "1000.00",
        "nist_csf_functions": ["DE.CM-1"],
        "iso_27001_domains": ["A.12.4.1"],
        "compliance_mappings": {"cis_safeguards": ["8.2"]},
        "applicable_industries": [],
        "applicable_org_sizes": [],
    }
    snap = {**fields, "entry_id": "x", "version": 1, "assignments": []}
    assert svc.entry_fields_match(snap, fields) is True
    assert (
        svc.entry_fields_match({**snap, "type": "administrative"}, fields) is True
    )  # type skipped
    assert svc.entry_fields_match({**snap, "nist_csf_functions": ["ZZ-OLD-TAG"]}, fields) is False
    assert (
        svc.entry_fields_match({k: v for k, v in snap.items() if k != "description"}, fields)
        is False
    )


def test_version_class_post_and_pre_bump() -> None:
    assert svc.version_class(2, 2) == "current"
    assert svc.version_class(3, 2) == "current"
    assert svc.version_class(1, 2) == "classify"
    assert svc.version_class(0, 2) == "X"  # lagging more than one version
    assert svc.version_class(None, 2) == "X"
    assert svc.version_class(1, 1, pre_bump=True) == "classify"  # becomes one behind after the bump
    assert svc.version_class(0, 1, pre_bump=True) == "X"


def test_partition_matches_pin_spelled_hyphenated_or_hex() -> None:
    """Review Focus 2."""
    eid = uuid.uuid4()
    assert svc.pin_matches_entry({"entry_id": str(eid), "version": 1}, eid.hex)
    assert svc.pin_matches_entry({"entry_id": eid.hex, "version": 1}, eid.hex)
    assert svc.pin_matches_entry({"entry_id": str(eid).upper()}, eid.hex)
    assert not svc.pin_matches_entry({"entry_id": "garbage"}, eid.hex)
    assert not svc.pin_matches_entry(None, eid.hex)
    assert not svc.pin_matches_entry("not-a-dict", eid.hex)
    assert svc.pin_version({"version": 2}) == 2
    # A3-N3: a non-integer version is unparsable (-> X) in BOTH modes, never normalised:
    # the guarded UPDATE compares json_extract INTEGER to INTEGER, so normalising "2" here
    # would make the dry run say A and --apply report a spurious conflict.
    assert svc.pin_version({"version": "2"}) is None
    assert svc.pin_version({"version": True}) is None
    assert svc.pin_version({"version": None}) is None
    assert svc.pin_version({}) is None


def test_dry_run_url_transform() -> None:
    """Review Focus 5."""
    assert svc.dry_run_url("sqlite+aiosqlite:////data/riskflow.db") == (
        "sqlite+aiosqlite:///file:/data/riskflow.db?mode=ro&uri=true"
    )
    keep = "sqlite+aiosqlite:///file:/tmp/b.db?immutable=1&uri=true"
    assert svc.dry_run_url(keep) == keep
    with_timeout = "sqlite+aiosqlite:////data/riskflow.db?timeout=30"
    assert svc.dry_run_url(with_timeout) == with_timeout  # unchanged; query_only guards it


# ---- end to end ----------------------------------------------------------------------------


def _entry(version: int, entry_id: uuid.UUID | None = None) -> ControlLibraryEntry:
    return ControlLibraryEntry(
        **({"id": entry_id} if entry_id is not None else {}),
        version=version,
        slug=svc.SIEM_SLUG,
        name=NAME,
        description="d" * 30,
        control_type=ControlType.TECHNICAL,
        reference_annual_cost=1000,
        nist_csf_subcategories=["DE.CM-1"],
        cis_safeguards=["8.2"],
        iso_27001_controls=["A.12.4.1"],
        compliance_mappings={},
        applicable_industries=[],
        applicable_org_sizes=[],
        tags=[],
        source_citations=[],
        status="published",
    )


async def _add_assignments(
    db: AsyncSession, entry: ControlLibraryEntry, vis_coverage: float
) -> None:
    for sf, cap in SIEM_ASSIGNMENTS:
        db.add(
            ControlLibraryEntryAssignment(
                library_entry_id=entry.id,
                library_entry_version=entry.version,
                sub_function=sf,
                capability_default=cap,
                coverage_default=vis_coverage if sf is F.LEC_DET_VISIBILITY else 0.8,
                reliability_default=0.8,
            )
        )
    await db.flush()


async def _vis_row(db: AsyncSession, control_id: uuid.UUID) -> ControlFunctionAssignment:
    return (
        await db.execute(
            select(ControlFunctionAssignment).where(
                ControlFunctionAssignment.control_id == control_id,
                ControlFunctionAssignment.sub_function == F.LEC_DET_VISIBILITY,
            )
        )
    ).scalar_one()


async def _build(
    db_url: str,
    *,
    with_entry: bool = True,
    latest_vis: float = 0.3,
    second_org_class_a: bool = False,
) -> dict[str, str]:
    """One control per class (A untouched, B confirmed, C tuned, D legacy, X_cell = snapshot differs
    from the latest entry on monitoring coverage, X_lag = pinned two versions back, X_field =
    snapshot differs from the latest entry on an ENTRY-LEVEL field — an in-place tag repair after
    adoption, R-SIEM-16a (iv)), a COMPLETED run for A and for C, optionally a SECOND org with its own
    class-A control, its own COMPLETED run and an org-1 run that names org 2's control (R-T-16
    two-org case), then the v2 entry at `latest_vis`. Returns hex ids."""
    engine = create_async_engine(db_url, future=True, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    ids: dict[str, str] = {}
    try:
        await _create_schema(engine)
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as db:
            org = Organization(
                name=ORG,
                industry_type=IndustryType.INFORMATION,
                organization_size=OrganizationSize.MEDIUM,
            )
            db.add(org)
            await db.flush()
            ids["org"] = org.id.hex
            if not with_entry:
                await db.commit()
                return ids
            v1 = _entry(1)
            db.add(v1)
            await db.flush()
            await _add_assignments(db, v1, 0.8)
            # Re-load v1 from the DB before adopting: adopt_from_library resolves the entry through
            # the identity map, and the snapshot's annual_cost is str(entry.reference_annual_cost) —
            # a constructor int (1000) would snapshot "1000" while the CLI's own session reads
            # Numeric(18, 2) back as Decimal("1000.00"). Production always snapshots DB-loaded
            # values, so the fixture must too, or the field oracle (iv) demotes a genuine A to X.
            await db.refresh(v1)
            ids["entry"] = v1.id.hex
            for key in ("A", "B", "C", "D", "X_cell", "X_lag", "X_field"):
                c = await adopt_from_library(
                    db, org_id=org.id, user_id=None, entry_id=v1.id, version=1
                )
                ids[key] = c.id.hex
                if key == "B":
                    (await _vis_row(db, c.id)).confirmed_by_user_at = datetime.now(UTC)
                if key == "C":
                    (await _vis_row(db, c.id)).coverage = 0.5
                if key == "D":
                    c.adopted_snapshot = None
                if key == "X_cell":
                    snap = dict(c.adopted_snapshot or {})
                    snap["assignments"] = [
                        {**a, "coverage": 0.7} if a.get("sub_function") == MON else a
                        for a in snap.get("assignments", [])
                    ]
                    c.adopted_snapshot = snap
                if key == "X_lag":
                    c.library_pin = {"entry_id": str(v1.id), "version": 0}
                if key == "X_field":
                    # The library's NIST tags were repaired in place after this adoption (no version
                    # bump, the f6a2b0d4e8c3 "Effect 2" shape): the snapshot still holds the old tag.
                    snap = dict(c.adopted_snapshot or {})
                    snap["nist_csf_functions"] = ["ZZ-OLD-TAG"]
                    c.adopted_snapshot = snap
            await db.flush()
            for key in ("A", "C"):
                r = RiskAnalysisRun(
                    organization_id=org.id,
                    scenario_id=None,
                    run_type=RunType.AGGREGATE,
                    status=RunStatus.COMPLETED,
                    mc_iterations=1000,
                    inputs_hash=f"h-{key}",
                    control_ids_used=[str(uuid.UUID(hex=ids[key]))],
                )
                db.add(r)
                ids[f"run_{key}"] = r.id.hex
            if second_org_class_a:
                org2 = Organization(
                    name=ORG + "-2",
                    industry_type=IndustryType.INFORMATION,
                    organization_size=OrganizationSize.MEDIUM,
                )
                db.add(org2)
                await db.flush()
                ids["org2"] = org2.id.hex
                a2 = await adopt_from_library(
                    db, org_id=org2.id, user_id=None, entry_id=v1.id, version=1
                )
                ids["A2"] = a2.id.hex
                await db.flush()
                run_a2 = RiskAnalysisRun(
                    organization_id=org2.id,
                    scenario_id=None,
                    run_type=RunType.AGGREGATE,
                    status=RunStatus.COMPLETED,
                    mc_iterations=1000,
                    inputs_hash="h-A2",
                    control_ids_used=[str(a2.id)],
                )
                # An org-1 run that names org 2's control id (only possible with a corrupt
                # control_ids_used): flag_runs_stale_for_control(org2, A2) must NOT touch it.
                run_cross = RiskAnalysisRun(
                    organization_id=org.id,
                    scenario_id=None,
                    run_type=RunType.AGGREGATE,
                    status=RunStatus.COMPLETED,
                    mc_iterations=1000,
                    inputs_hash="h-cross",
                    control_ids_used=[str(a2.id)],
                )
                db.add_all([run_a2, run_cross])
                ids["run_A2"], ids["run_cross"] = run_a2.id.hex, run_cross.id.hex
            v2 = _entry(2, entry_id=v1.id)
            db.add(v2)
            await db.flush()
            await _add_assignments(db, v2, latest_vis)
            await db.commit()
    finally:
        await engine.dispose()
    return ids


def _path(db_url: str) -> Path:
    return Path(db_url.removeprefix("sqlite+aiosqlite:///"))


def _sha(db_url: str) -> str:
    return hashlib.sha256(_path(db_url).read_bytes()).hexdigest()


def _dump(db_url: str, control_hexes: list[str]) -> list[tuple[object, ...]]:
    eng = sa.create_engine(db_url.replace("sqlite+aiosqlite://", "sqlite://"))
    out: list[tuple[object, ...]] = []
    with eng.connect() as conn:
        for h in control_hexes:
            out += [
                tuple(r)
                for r in conn.execute(sa.text("SELECT * FROM controls WHERE id = :i"), {"i": h})
            ]
            out += [
                tuple(r)
                for r in conn.execute(
                    sa.text(
                        "SELECT * FROM control_function_assignments WHERE control_id = :i ORDER BY sub_function"
                    ),
                    {"i": h},
                )
            ]
    eng.dispose()
    return out


async def _open(db_url: str) -> tuple[Any, AsyncSession]:
    engine = create_async_engine(db_url, future=True, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()


EXPECTED_DRY = "classes A=1 B=1 C=1 D=1 X=3 current=0 applied=0 runs_flagged=0 conflicts=0"


@pytest.mark.asyncio
async def test_dry_run_partitions_and_writes_nothing_bare_url(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-T-15 through the BARE URL path (mode=ro rewrite + query_only)."""
    ids = await _build(db_url)
    before = _sha(db_url)
    rc = await svc.run(apply=False, database_url=db_url)
    out = capsys.readouterr().out
    assert rc == 0
    assert _sha(db_url) == before
    assert f"mode=dry-run db=sqlite:{_path(db_url).name} entry_version=2" in out
    for key in ("A", "B", "C", "D"):
        assert f"{ids[key]} {key}" in out
    for key in ("X_cell", "X_lag", "X_field"):
        assert f"{ids[key]} X" in out, key
    assert EXPECTED_DRY in out
    assert (
        NAME not in out and ORG not in out and "0.8" not in out and db_url not in out
    )  # R-SIEM-17


@pytest.mark.asyncio
async def test_dry_run_with_query_string_url_writes_nothing(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review Focus 5 / Sec-N3: a URL that already carries a query string is used as given and
    the read-only guarantee still holds (PRAGMA query_only), checksum unchanged."""
    await _build(db_url)
    before = _sha(db_url)
    rc = await svc.run(apply=False, database_url=f"{db_url}?timeout=30")
    assert rc == 0
    assert EXPECTED_DRY in capsys.readouterr().out
    assert _sha(db_url) == before


@pytest.mark.asyncio
async def test_dry_run_engine_refuses_writes_whatever_the_url(db_url: str) -> None:
    """Sec-N3 end to end: a write through the dry-run engine raises, with a plain URL that has no
    mode=ro at all."""
    await _build(db_url)
    engine = svc.build_engine(f"{db_url}?timeout=5", apply=False)
    try:
        async with engine.connect() as conn:
            with pytest.raises(OperationalError):
                await conn.execute(sa.text("UPDATE organizations SET name = name"))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsupported_dialect_exits_2_without_connecting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await svc.run(apply=False, database_url="postgresql+asyncpg://user:secret@h/db") == 2
    assert await svc.run(apply=True, database_url="postgresql+asyncpg://user:secret@h/db") == 2
    err = capsys.readouterr().err
    assert "sqlite only" in err and "secret" not in err


@pytest.mark.asyncio
async def test_malformed_database_url_prints_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    """R-SIEM-26 / R-T-23 (plan-gate Sec2-N3): setup failures — URL parsing here — are inside
    run()'s catch-all, so the operator sees the exception TYPE only, never a traceback that
    would carry the DSN (a DSN can carry a credential)."""
    assert await svc.run(apply=False, database_url="not a url:secret") == 3
    err = capsys.readouterr().err
    assert err.startswith("internal error: ")
    assert len(err.strip().splitlines()) == 1
    assert "secret" not in err and "Traceback" not in err


@pytest.mark.asyncio
async def test_apply_changes_class_a_only(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    ids = await _build(db_url)
    others = [ids["B"], ids["C"], ids["D"], ids["X_cell"], ids["X_lag"], ids["X_field"]]
    others_before = _dump(db_url, others)
    rc = await svc.run(apply=True, database_url=db_url)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"mode=apply db=sqlite:{_path(db_url).name} entry_version=2" in out
    assert "classes A=1 B=1 C=1 D=1 X=3 current=0 applied=1 runs_flagged=1 conflicts=0" in out
    assert _dump(db_url, others) == others_before  # R-SIEM-20

    engine, db = await _open(db_url)
    try:
        a = await db.get(Control, uuid.UUID(hex=ids["A"]))
        assert a is not None
        vis = await _vis_row(db, a.id)
        assert vis.coverage == pytest.approx(0.3)
        assert vis.confirmed_by_user_at is None
        assert a.library_pin == {"entry_id": str(uuid.UUID(hex=ids["entry"])), "version": 2}
        assert a.adopted_snapshot is not None
        snap_vis = next(x for x in a.adopted_snapshot["assignments"] if x["sub_function"] == VIS)
        assert snap_vis["coverage"] == pytest.approx(0.3)
        assert a.adopted_snapshot["version"] == 2
        run_a = await db.get(RiskAnalysisRun, uuid.UUID(hex=ids["run_A"]))
        run_c = await db.get(RiskAnalysisRun, uuid.UUID(hex=ids["run_C"]))
        assert run_a is not None and run_a.is_stale is True
        assert run_c is not None and run_c.is_stale is False
        audits = (
            (await db.execute(select(AuditLog).where(AuditLog.action == svc.ACTION)))
            .scalars()
            .all()
        )
        assert len(audits) == 1
        assert audits[0].user_id is None
        assert audits[0].entity_id == a.id
        assert audits[0].organization_id.hex == ids["org"]
        assert audits[0].changes == {
            "version": [1, 2],
            "lec_det_visibility.coverage": [0.8, 0.3],
            "runs_flagged_stale": [None, 1],
            "reason": ["", svc.REASON],
        }
        # R-T-17 / R-SIEM-21
        info_a = await resync_info(db, a)
        assert info_a is not None and info_a.stale is False
        for key in ("B", "C", "D", "X_cell", "X_lag", "X_field"):
            c = await db.get(Control, uuid.UUID(hex=ids[key]))
            assert c is not None
            info = await resync_info(db, c)
            assert info is not None and info.stale is True, key
        # X_field is exactly the case the consent page would show as a library_changed FIELD row;
        # re-pinning it would have hidden that row (Sec2-I2). It stays stale and unwritten.
        x_field = await db.get(Control, uuid.UUID(hex=ids["X_field"]))
        assert x_field is not None
        info_x = await resync_info(db, x_field)
        assert info_x is not None and any(
            f.field == "nist_csf_functions" and f.library_changed is True for f in info_x.fields
        )
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_apply_two_orgs_writes_each_control_under_its_own_org(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-T-16 two-org case (plan-gate S2-I5; risk S1): one class-A control per org. Each audit row
    carries ITS control's organization_id; org 2's run is flagged; the org-1 run that names org
    2's control id is NOT (flag_runs_stale_for_control is org-keyed); nothing crosses orgs."""
    ids = await _build(db_url, second_org_class_a=True)
    assert await svc.run(apply=True, database_url=db_url) == 0
    out = capsys.readouterr().out
    assert "classes A=2 B=1 C=1 D=1 X=3 current=0 applied=2 runs_flagged=2 conflicts=0" in out
    engine, db = await _open(db_url)
    try:
        audits = (
            (await db.execute(select(AuditLog).where(AuditLog.action == svc.ACTION)))
            .scalars()
            .all()
        )
        assert {(a.entity_id.hex, a.organization_id.hex) for a in audits} == {
            (ids["A"], ids["org"]),
            (ids["A2"], ids["org2"]),
        }
        for key, expected in (
            ("run_A", True),
            ("run_A2", True),
            ("run_cross", False),
            ("run_C", False),
        ):
            r = await db.get(RiskAnalysisRun, uuid.UUID(hex=ids[key]))
            assert r is not None and r.is_stale is expected, key
        for key in ("A", "A2"):
            c = await db.get(Control, uuid.UUID(hex=ids[key]))
            assert c is not None and c.library_pin is not None and c.library_pin["version"] == 2
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_analyst_edit_between_read_and_write_is_not_clobbered(
    db_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-SIEM-18 / R-T-22 (plan-gate A2-I3, Sec2-N1, A3-I1): pysqlite's deferred BEGIN means
    the per-control SELECTs run in autocommit, so a re-read alone cannot exclude a save that
    lands between read and write. The guarded UPDATE must find rowcount 0, roll the control
    back to class C, keep the analyst's value, write no audit row and flag no run — and the
    run must CONTINUE past the conflict. Two class-A controls (two orgs) and the conflict is
    forced onto whichever sorts FIRST in the service's `cid.hex` iteration order, so at least
    one pinned control is always processed after the rollback: a loop that touches an
    expired ORM object after `db.rollback()` (MissingGreenlet -> exit 3) fails this test
    deterministically, whatever uuid4 hands out."""
    ids = await _build(db_url, second_org_class_a=True)
    first_a, other_a = sorted([ids["A"], ids["A2"]])  # the service iterates sorted by hex
    run_of = {ids["A"]: ids["run_A"], ids["A2"]: ids["run_A2"]}
    sync_url = db_url.replace("sqlite+aiosqlite://", "sqlite://")

    async def _tune_between_read_and_write(control_id: uuid.UUID) -> None:
        if control_id.hex != first_a:
            return
        eng = sa.create_engine(sync_url)  # a second connection: the analyst's form save
        with eng.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE control_function_assignments SET coverage = 0.6 "
                    "WHERE control_id = :c AND sub_function = 'lec_det_visibility'"
                ),
                {"c": first_a},
            )
        eng.dispose()

    monkeypatch.setattr(svc, "_before_write", _tune_between_read_and_write)
    rc = await svc.run(apply=True, database_url=db_url)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"{first_a} C" in out
    assert f"{other_a} A" in out  # processed AFTER the rollback
    assert "classes A=1 B=1 C=2 D=1 X=3 current=0 applied=1 runs_flagged=1 conflicts=1" in out
    engine, db = await _open(db_url)
    try:
        a = await db.get(Control, uuid.UUID(hex=first_a))
        assert a is not None
        vis = await _vis_row(db, a.id)
        assert vis.coverage == pytest.approx(0.6)  # the analyst's value survived
        assert vis.confirmed_by_user_at is None
        assert a.library_pin == {"entry_id": str(uuid.UUID(hex=ids["entry"])), "version": 1}
        assert a.adopted_snapshot is not None and a.adopted_snapshot["version"] == 1
        snap_vis = next(x for x in a.adopted_snapshot["assignments"] if x["sub_function"] == VIS)
        assert snap_vis["coverage"] == pytest.approx(0.8)
        run_first = await db.get(RiskAnalysisRun, uuid.UUID(hex=run_of[first_a]))
        assert run_first is not None and run_first.is_stale is False
        other = await db.get(Control, uuid.UUID(hex=other_a))
        assert (
            other is not None
            and other.library_pin is not None
            and other.library_pin["version"] == 2
        )
        run_other = await db.get(RiskAnalysisRun, uuid.UUID(hex=run_of[other_a]))
        assert run_other is not None and run_other.is_stale is True
        audits = (
            (await db.execute(select(AuditLog).where(AuditLog.action == svc.ACTION)))
            .scalars()
            .all()
        )
        assert [x.entity_id.hex for x in audits] == [other_a]  # none for the conflicted control
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_second_apply_is_a_noop(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    await _build(db_url)
    assert await svc.run(apply=True, database_url=db_url) == 0
    capsys.readouterr()
    assert await svc.run(apply=True, database_url=db_url) == 0
    out = capsys.readouterr().out
    assert "classes A=0 B=1 C=1 D=1 X=3 current=1 applied=0 runs_flagged=0 conflicts=0" in out
    engine, db = await _open(db_url)
    try:
        n = len(
            (await db.execute(select(AuditLog).where(AuditLog.action == svc.ACTION)))
            .scalars()
            .all()
        )
        assert n == 1
    finally:
        await db.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_apply_refuses_before_the_bump_migration(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-SIEM-24: the stored entry's latest Visibility coverage is still 0.8 -> exit 2, no write."""
    await _build(db_url, latest_vis=0.8)
    before = _sha(db_url)
    assert await svc.run(apply=True, database_url=db_url) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "0.8" not in err
    assert _sha(db_url) == before
    # The dry run still classifies: every pinned-one-behind control is X (target not at NEW).
    assert await svc.run(apply=False, database_url=db_url) == 0
    assert (
        "classes A=0 B=0 C=1 D=1 X=5 current=0 applied=0 runs_flagged=0 conflicts=0"
        in capsys.readouterr().out
    )


@pytest.mark.asyncio
async def test_missing_entry_is_a_noop(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Review Focus 4."""
    await _build(db_url, with_entry=False)
    before = _sha(db_url)
    assert await svc.run(apply=True, database_url=db_url) == 0
    assert "entry_missing=1" in capsys.readouterr().out
    assert _sha(db_url) == before
