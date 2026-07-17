"""ScenarioService: CRUD + audit emission.

PR pi F12: pin-resolution behavior (calibration_override_pin / overlay_pins
auto-resolution + refresh_calibration) was excised. The tests below cover
the surviving create / update / delete / audit semantics:

- create: validates FAIR distributions, IDOR-guards user vs organization,
  emits ``scenario.create`` audit row with originating IP.
- update: descriptive-fields-only mutation. Optimistic-lock on
  ``expected_row_version: int`` (P9). Bumps ``row_version`` by 1 on
  success and emits ``scenario.update``. No-op edits are silent.
- delete: hard delete with the same optimistic-lock semantics. Audit
  row written BEFORE the row delete so ``entity_id`` points to an
  extant row at flush time.

Audit ``action`` strings follow the project-wide ``<entity>.<verb>``
taxonomy. Sentinel asserts guard against accidental regression to the
legacy bare-verb pattern.

Service tests pass ``ip_address="10.0.0.x"`` and assert the audit row
preserves it — covers the spec §6 ``audit_includes_ip_address``
invariant explicitly at the service layer (the route layer threads
``client_ip(request)`` in E5/E6).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import (
    FAIRCAMValidationError,
    NotFoundError,
    RunBusyError,
    ScenarioInUseError,
)
from idraa.models.audit_log import AuditLog
from idraa.models.enums import (
    IndustryType,
    OrganizationSize,
    ThreatCategory,
    UserRole,
)
from idraa.models.scenario import Scenario
from idraa.schemas.scenario import ScenarioForm
from idraa.services.scenarios import (
    ScenarioService,
    ScenarioVersionConflictError,
)


def _form(
    *,
    name: str = "Ransomware",
    description: str | None = None,
    threat_event_frequency: dict[str, Any] | None = None,
    primary_loss: dict[str, Any] | None = None,
) -> ScenarioForm:
    """Minimal valid ScenarioForm for service tests.

    industry/revenue_tier are no longer ScenarioForm fields (issue #88
    Task 9); the service derives them from the live org row.

    ``threat_event_frequency`` / ``primary_loss`` overrides let Sec-1
    regression tests inject an invalid distribution (non-finite PERT,
    unbounded lognormal) to exercise the edit-path validation gate. The
    distribution fields are ``dict[str, Any]`` so an invalid shape is
    accepted at the Pydantic layer and rejected only at the FAIRCAM
    validator boundary — exactly the path under test.
    """
    return ScenarioForm(
        name=name,
        description=description,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency=threat_event_frequency
        or {
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss=primary_loss
        or {
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
    )


# Type aliases for the conftest-provided callables — keeps test signatures
# concise without losing the fact that these are awaitable factories.
SeedOrgUser = Callable[..., Awaitable[Any]]


# ---- create -----------------------------------------------------------------


async def test_create_emits_audit_in_same_session(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(),
        current_user=user,
    )

    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.entity_id == s.id)))
        .scalars()
        .one()
    )
    assert audit.action == "scenario.create"
    assert audit.entity_type == "scenario"
    assert audit.organization_id == org.id
    assert audit.user_id == user.id
    # Sentinel: must use dotted taxonomy, never bare "create".
    assert audit.action != "create"


async def test_create_audit_includes_ip_address(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Spec §6 invariant: audit row preserves the originating IP
    threaded from the service caller (route layer passes
    ``client_ip(request)``)."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(),
        current_user=user,
        ip_address="10.0.0.42",
    )

    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.entity_id == s.id)))
        .scalars()
        .one()
    )
    assert audit.ip_address == "10.0.0.42"


# ---- update -----------------------------------------------------------------


async def test_update_changes_descriptive_fields(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Update mutates descriptive fields and bumps row_version."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Original"),
        current_user=user,
    )
    row_version_before = s.row_version

    new_form = _form(name="Renamed")
    updated = await service.update(
        organization_id=org.id,
        scenario_id=s.id,
        form=new_form,
        expected_row_version=row_version_before,
        current_user=user,
    )

    assert updated.name == "Renamed"
    assert updated.row_version == row_version_before + 1


async def test_update_optimistic_lock_conflict(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """P9: ``expected_row_version`` mismatch raises 409, named both
    expected and actual values in the message."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="X"),
        current_user=user,
    )

    with pytest.raises(ScenarioVersionConflictError) as exc_info:
        await service.update(
            organization_id=org.id,
            scenario_id=s.id,
            form=_form(name="Y"),
            expected_row_version=999,
            current_user=user,
        )
    msg = str(exc_info.value)
    assert "999" in msg
    assert str(s.row_version) in msg


async def test_update_no_op_does_not_bump_row_version(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Update with form identical to current state is a silent no-op:
    no row_version bump, no audit row."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Stable"),
        current_user=user,
    )
    rv_before = s.row_version

    same_form = _form(name="Stable")
    result = await service.update(
        organization_id=org.id,
        scenario_id=s.id,
        form=same_form,
        expected_row_version=rv_before,
        current_user=user,
    )
    assert result.row_version == rv_before

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == s.id, AuditLog.action == "scenario.update"
                )
            )
        )
        .scalars()
        .all()
    )
    assert audits == []


async def test_update_audit_emitted(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="X"),
        current_user=user,
    )

    await service.update(
        organization_id=org.id,
        scenario_id=s.id,
        form=_form(name="Y"),
        expected_row_version=s.row_version,
        current_user=user,
    )

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_id == s.id).order_by(AuditLog.timestamp)
            )
        )
        .scalars()
        .all()
    )
    actions = [a.action for a in audits]
    assert "scenario.create" in actions
    assert "scenario.update" in actions


async def test_update_audit_includes_ip_address(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Spec §6 invariant: update audit row carries originating IP."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="X"),
        current_user=user,
    )

    await service.update(
        organization_id=org.id,
        scenario_id=s.id,
        form=_form(name="Y"),
        expected_row_version=s.row_version,
        current_user=user,
        ip_address="10.0.0.42",
    )

    update_audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == s.id,
                    AuditLog.action == "scenario.update",
                )
            )
        )
        .scalars()
        .one()
    )
    assert update_audit.ip_address == "10.0.0.42"


async def test_update_rejects_non_finite_pert_does_not_persist(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Sec-1: an edit that would store a non-finite PERT (tef_high=inf)
    raises FAIRCAMValidationError and leaves the stored row unchanged.

    Mirrors the create / _stamp_new_scenario gate — the edit path
    previously bypassed validate_fair_distributions, letting an inf reach
    stored FAIR distributions (Monte Carlo corruption vector).
    """
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Original"),
        current_user=user,
    )
    rv_before = s.row_version
    tef_before = dict(s.threat_event_frequency)

    bad_form = _form(
        name="Renamed",
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": float("inf"),
        },
    )
    with pytest.raises(FAIRCAMValidationError):
        await service.update(
            organization_id=org.id,
            scenario_id=s.id,
            form=bad_form,
            expected_row_version=rv_before,
            current_user=user,
        )

    # Re-fetch from the DB: the bad edit must NOT have persisted.
    await db_session.refresh(s)
    assert s.name == "Original"
    assert s.row_version == rv_before
    assert s.threat_event_frequency == tef_before


async def test_update_rejects_unbounded_lognormal_does_not_persist(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Sec-1: an edit that would store a lognormal PL with sigma>10
    (low=1, high=4e28 → sigma≈20) raises FAIRCAMValidationError and does
    not persist. An extreme-but-finite sigma is a user-controllable
    OOM/DoS path to the engine sampler (Sec-I2); create/import reject it
    and the edit path must too.
    """
    from fair_cam.quantile_pooling import lognormal_from_quantiles

    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Original"),
        current_user=user,
    )
    rv_before = s.row_version
    pl_before = dict(s.primary_loss)

    unbounded_ln = {"distribution": "lognormal", **lognormal_from_quantiles(1.0, 4e28)}
    assert unbounded_ln["sigma"] > 10.0  # guards the fixture intent

    bad_form = _form(name="Renamed", primary_loss=unbounded_ln)
    with pytest.raises(FAIRCAMValidationError):
        await service.update(
            organization_id=org.id,
            scenario_id=s.id,
            form=bad_form,
            expected_row_version=rv_before,
            current_user=user,
        )

    await db_session.refresh(s)
    assert s.name == "Original"
    assert s.row_version == rv_before
    assert s.primary_loss == pl_before


async def test_update_valid_edit_still_succeeds(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Sec-1 non-regression: a valid distribution edit still applies —
    the new validation gate must not over-block legitimate edits."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(name="Original"),
        current_user=user,
    )
    rv_before = s.row_version

    valid_form = _form(
        name="Retuned",
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.6,
            "high": 1.5,
        },
    )
    updated = await service.update(
        organization_id=org.id,
        scenario_id=s.id,
        form=valid_form,
        expected_row_version=rv_before,
        current_user=user,
    )

    assert updated.name == "Retuned"
    assert updated.row_version == rv_before + 1
    assert updated.threat_event_frequency["mode"] == 0.6


async def test_update_idor_safe_when_scenario_in_other_org(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """IDOR: a scenario whose id is known but belongs to another org
    must surface as NotFoundError, not be silently mutated."""
    org_a, user_a = await seed_org_user(db_session)
    org_b, user_b = await seed_org_user(
        db_session,
        org_name="B",
        industry=IndustryType.INFORMATION,
        size=OrganizationSize.SMALL,
        email="b@example.com",
        role=UserRole.ANALYST,
    )

    service = ScenarioService(db_session)
    s_b = await service.create(
        organization_id=org_b.id,
        form=_form(),
        current_user=user_b,
    )

    with pytest.raises(NotFoundError):
        await service.update(
            organization_id=org_a.id,  # WRONG org — must not match
            scenario_id=s_b.id,
            form=_form(name="hijack"),
            expected_row_version=s_b.row_version,
            current_user=user_a,
        )


# ---- delete -----------------------------------------------------------------


async def test_delete_removes_row(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(),
        current_user=user,
    )
    sid = s.id
    rv = s.row_version

    await service.delete(
        organization_id=org.id,
        scenario_id=sid,
        expected_row_version=rv,
        current_user=user,
    )

    fetched = (
        await db_session.execute(select(Scenario).where(Scenario.id == sid))
    ).scalar_one_or_none()
    assert fetched is None


async def test_delete_audit_emitted(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(),
        current_user=user,
    )

    await service.delete(
        organization_id=org.id,
        scenario_id=s.id,
        expected_row_version=s.row_version,
        current_user=user,
    )

    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.delete")))
        .scalars()
        .one()
    )
    assert audit.entity_id == s.id
    # Sentinel: dotted taxonomy, not bare "delete".
    assert audit.action != "delete"


async def test_delete_audit_includes_ip_address(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Spec §6 invariant: delete audit row carries originating IP."""
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(),
        current_user=user,
    )

    await service.delete(
        organization_id=org.id,
        scenario_id=s.id,
        expected_row_version=s.row_version,
        current_user=user,
        ip_address="10.0.0.42",
    )
    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.delete")))
        .scalars()
        .one()
    )
    assert audit.ip_address == "10.0.0.42"


async def test_delete_optimistic_lock_conflict(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    org, user = await seed_org_user(db_session)

    service = ScenarioService(db_session)
    s = await service.create(
        organization_id=org.id,
        form=_form(),
        current_user=user,
    )

    with pytest.raises(ScenarioVersionConflictError):
        await service.delete(
            organization_id=org.id,
            scenario_id=s.id,
            expected_row_version=999,
            current_user=user,
        )


async def test_delete_idor_safe_when_scenario_in_other_org(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """IDOR: a scenario id from another org must NOT be deletable
    via this org's session; surfaces as NotFoundError."""
    org_a, user_a = await seed_org_user(db_session)
    org_b, user_b = await seed_org_user(
        db_session,
        org_name="B",
        industry=IndustryType.INFORMATION,
        size=OrganizationSize.SMALL,
        email="b@example.com",
        role=UserRole.ANALYST,
    )

    service = ScenarioService(db_session)
    s_b = await service.create(
        organization_id=org_b.id,
        form=_form(),
        current_user=user_b,
    )

    with pytest.raises(NotFoundError):
        await service.delete(
            organization_id=org_a.id,
            scenario_id=s_b.id,
            expected_row_version=s_b.row_version,
            current_user=user_a,
        )


# ---- cascade-delete a scenario's SINGLE runs (RESTRICT-FK fix) --------


async def _make_run(
    db: AsyncSession,
    *,
    org_id: Any,
    scenario_id: Any,
    created_by: Any,
    status: Any = None,
    with_samples: bool = False,
) -> Any:
    """Build a minimal schema-valid SINGLE RiskAnalysisRun for cascade tests."""
    import hashlib

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
    from idraa.models.run_samples import RunSamples

    run = RiskAnalysisRun(
        id=__import__("uuid").uuid4(),
        organization_id=org_id,
        scenario_id=scenario_id,
        run_type=RunType.SINGLE,
        status=status or RunStatus.COMPLETED,
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(__import__("uuid").uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        created_by=created_by,
    )
    db.add(run)
    await db.flush()
    if with_samples:
        db.add(
            RunSamples(
                run_id=run.id,
                organization_id=org_id,
                arrays={"ale": [1.0, 2.0, 3.0]},
            )
        )
        await db.flush()
    return run


async def test_delete_with_run_no_cascade_raises_in_use(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """A scenario with >=1 SINGLE run + cascade_runs=False raises
    ScenarioInUseError(run_count=1); scenario AND run remain present."""
    from idraa.models.risk_analysis_run import RiskAnalysisRun

    org, user = await seed_org_user(db_session)
    service = ScenarioService(db_session)
    s = await service.create(organization_id=org.id, form=_form(), current_user=user)
    run = await _make_run(db_session, org_id=org.id, scenario_id=s.id, created_by=user.id)

    with pytest.raises(ScenarioInUseError) as exc_info:
        await service.delete(
            organization_id=org.id,
            scenario_id=s.id,
            expected_row_version=s.row_version,
            current_user=user,
        )
    assert exc_info.value.run_count == 1

    # Nothing deleted.
    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == s.id))
    ).scalar_one_or_none() is not None
    assert (
        await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run.id))
    ).scalar_one_or_none() is not None


async def test_delete_with_cascade_removes_scenario_run_and_samples(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """cascade_runs=True deletes the scenario, its SINGLE run, and the run's
    samples (auto-cascade); audit rows are written for both deletes."""
    from idraa.models.risk_analysis_run import RiskAnalysisRun
    from idraa.models.run_samples import RunSamples

    org, user = await seed_org_user(db_session)
    service = ScenarioService(db_session)
    s = await service.create(organization_id=org.id, form=_form(), current_user=user)
    run = await _make_run(
        db_session,
        org_id=org.id,
        scenario_id=s.id,
        created_by=user.id,
        with_samples=True,
    )
    sid, rid = s.id, run.id

    await service.delete(
        organization_id=org.id,
        scenario_id=sid,
        expected_row_version=s.row_version,
        current_user=user,
        cascade_runs=True,
    )

    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == sid))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == rid))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(RunSamples).where(RunSamples.run_id == rid))
    ).scalar_one_or_none() is None

    audits = (await db_session.execute(select(AuditLog))).scalars().all()
    actions = [a.action for a in audits]
    assert "risk_analysis_run.delete" in actions
    assert "scenario.delete" in actions
    run_delete = next(a for a in audits if a.action == "risk_analysis_run.delete")
    assert run_delete.entity_id == rid


async def test_delete_with_cascade_removes_all_n_runs(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Iteration-contract: cascade deletes ALL N single runs (not just the
    first) — guards against a future [0]/first regression in the cascade loop.
    N=3 per the project's adapter-iteration-contract policy."""
    from idraa.models.risk_analysis_run import RiskAnalysisRun

    org, user = await seed_org_user(db_session)
    service = ScenarioService(db_session)
    s = await service.create(organization_id=org.id, form=_form(), current_user=user)
    rids = [
        (await _make_run(db_session, org_id=org.id, scenario_id=s.id, created_by=user.id)).id
        for _ in range(3)
    ]
    sid = s.id

    await service.delete(
        organization_id=org.id,
        scenario_id=sid,
        expected_row_version=s.row_version,
        current_user=user,
        cascade_runs=True,
    )

    # ALL three runs gone, scenario gone.
    remaining = (
        (await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id.in_(rids))))
        .scalars()
        .all()
    )
    assert remaining == []
    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == sid))
    ).scalar_one_or_none() is None
    # One run-delete audit row per run (3), plus the scenario delete.
    actions = [a.action for a in (await db_session.execute(select(AuditLog))).scalars().all()]
    assert actions.count("risk_analysis_run.delete") == 3
    assert actions.count("scenario.delete") == 1


async def test_delete_cascade_blocked_by_in_flight_run(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """cascade_runs=True with a RUNNING/QUEUED SINGLE run raises RunBusyError;
    nothing is deleted (the guard runs BEFORE any delete)."""
    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus

    org, user = await seed_org_user(db_session)
    service = ScenarioService(db_session)
    s = await service.create(organization_id=org.id, form=_form(), current_user=user)
    run = await _make_run(
        db_session,
        org_id=org.id,
        scenario_id=s.id,
        created_by=user.id,
        status=RunStatus.RUNNING,
    )

    with pytest.raises(RunBusyError):
        await service.delete(
            organization_id=org.id,
            scenario_id=s.id,
            expected_row_version=s.row_version,
            current_user=user,
            cascade_runs=True,
        )

    # Nothing deleted.
    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == s.id))
    ).scalar_one_or_none() is not None
    assert (
        await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run.id))
    ).scalar_one_or_none() is not None


async def test_delete_no_runs_still_works(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Existing behavior preserved: a scenario with NO runs deletes fine
    (no confirmation needed, cascade_runs default False)."""
    org, user = await seed_org_user(db_session)
    service = ScenarioService(db_session)
    s = await service.create(organization_id=org.id, form=_form(), current_user=user)
    sid = s.id

    await service.delete(
        organization_id=org.id,
        scenario_id=sid,
        expected_row_version=s.row_version,
        current_user=user,
    )
    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == sid))
    ).scalar_one_or_none() is None


async def test_delete_not_blocked_by_aggregate_run_membership(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """A scenario referenced ONLY by an AGGREGATE run (scenario_id IS NULL,
    sid in aggregate_scenario_ids) and NO SINGLE run is NOT blocked: the
    scenario deletes and the aggregate run SURVIVES."""
    import hashlib
    import uuid as _uuid

    from idraa.models.risk_analysis_run import (
        RiskAnalysisRun,
        RunStatus,
        RunType,
    )

    org, user = await seed_org_user(db_session)
    service = ScenarioService(db_session)
    s = await service.create(organization_id=org.id, form=_form(name="Member"), current_user=user)
    other = await service.create(
        organization_id=org.id, form=_form(name="Other"), current_user=user
    )

    agg = RiskAnalysisRun(
        id=_uuid.uuid4(),
        organization_id=org.id,
        scenario_id=None,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(_uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        aggregate_scenario_ids=sorted([str(s.id), str(other.id)]),
        created_by=user.id,
    )
    db_session.add(agg)
    await db_session.flush()
    agg_id, sid = agg.id, s.id

    # No SINGLE run → no confirmation needed, deletes straight through.
    await service.delete(
        organization_id=org.id,
        scenario_id=sid,
        expected_row_version=s.row_version,
        current_user=user,
    )
    assert (
        await db_session.execute(select(Scenario).where(Scenario.id == sid))
    ).scalar_one_or_none() is None
    # Aggregate run survives.
    assert (
        await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == agg_id))
    ).scalar_one_or_none() is not None
