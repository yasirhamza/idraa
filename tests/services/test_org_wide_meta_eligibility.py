"""#299: org_wide_meta eligibility (>=1 inheritable assignment, spec D6), the
org-wide stale sweep and its triggers (spec D9), audit payloads, duplicate
reset, org-scoped repo queries."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlDomain,
    ControlImplementationStage,
    ControlType,
    EntityStatus,
    FairCamSubFunction,
)
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.repositories.control_repo import ControlRepo
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services.control_library import flag_runs_stale_for_org
from idraa.services.controls import (
    OrgWideMetaIneligible,
    create_control,
    duplicate_control,
    has_meta_assignment,
    soft_delete_control,
    update_control,
)
from idraa.services.inherited_controls import inherits_assignment


def _form(
    *sub_fns: str,
    org_wide: bool,
    name: str = "C",
    coverage: float = 0.8,
    capability_value: float | None = 0.5,
    **kw: Any,
) -> ControlForm:
    return ControlForm(
        name=name,
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction(sf),
                capability_value=capability_value,
                coverage=coverage,
                reliability=0.8,
            )
            for sf in sub_fns
        ],
        org_wide_meta=org_wide,
        **kw,
    )


async def _loaded(db: AsyncSession, *controls: Control) -> None:
    """Populate ``assignments`` (lazy="selectin") on controls built by create_control in
    THIS session: the collection of a freshly created control is unloaded, and the
    services read it synchronously (the routes get it from db.get, which selectin-loads)."""
    for c in controls:
        await db.refresh(c, ["assignments"])


async def _audits(db: AsyncSession, action: str, entity_id: uuid.UUID) -> list[AuditLog]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.action == action, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.timestamp, AuditLog.id)  # trigger-order assertions below
    )
    return list((await db.execute(stmt)).scalars().all())


async def _sweep_triggers(db: AsyncSession, control_id: uuid.UUID) -> list[str]:
    """Ordered sweep triggers for a control. A control CREATED org-wide is effective
    at creation (ControlForm.status / implementation_stage default ACTIVE,
    schemas/control.py:209-210) and has ALREADY written a "create" sweep row —
    tests select the row under test by trigger, never by unpacking one row
    (Meth-I8 / SC-I13)."""
    triggers: list[str] = []
    for r in await _audits(db, "control.org_wide_sweep", control_id):
        pair = r.changes["trigger"]
        assert isinstance(pair, list)
        triggers.append(str(pair[1]))
    return triggers


async def _is_stale(db: AsyncSession, run: RiskAnalysisRun) -> bool:
    """Re-read ``is_stale`` through a call so mypy does not narrow the attribute across
    successive asserts (it would mark every later statement unreachable)."""
    await db.refresh(run)
    return bool(run.is_stale)


async def _write_footprint(db: AsyncSession, org_id: uuid.UUID) -> tuple[int, int, int]:
    """(controls, assignments, audit rows) in the org, counted inside the open transaction."""

    async def _count(model: Any) -> int:
        stmt = select(func.count()).select_from(model).where(model.organization_id == org_id)
        return int((await db.execute(stmt)).scalar_one())

    return (
        await _count(Control),
        await _count(ControlFunctionAssignment),
        await _count(AuditLog),
    )


async def _assert_refusal_wrote_nothing(
    db: AsyncSession, org_id: uuid.UUID, before: tuple[int, int, int]
) -> None:
    """Call right after ``pytest.raises`` and BEFORE any rollback. The rollback the
    caller does next would erase a write that happened ahead of the raise, so a
    refusal moved below a mutation or a flush could only be seen here: nothing
    pending in the session, and no flushed control / assignment / audit row."""
    assert not (db.new or db.dirty or db.deleted)
    assert await _write_footprint(db, org_id) == before


def test_inherits_assignment_is_the_single_predicate() -> None:
    assert inherits_assignment("vmc_prev_reduce_variance_prob")
    assert inherits_assignment("vmc_corr_treatment_selection")  # label-only still META
    assert inherits_assignment("dsc_corr_misaligned", "vmc_corr_implementation")  # meta source
    assert not inherits_assignment("dsc_corr_misaligned", "lec_resp_resilience")  # D6: dropped
    assert not inherits_assignment("dsc_corr_misaligned", "lec_resp_event_termination")
    assert not inherits_assignment("lec_prev_resistance")


def test_has_meta_assignment_uses_fair_cam_export() -> None:
    assert has_meta_assignment(["lec_prev_resistance", "vmc_prev_reduce_variance_prob"])
    assert has_meta_assignment(["vmc_corr_treatment_selection"])
    assert not has_meta_assignment(["lec_prev_resistance", "lec_det_monitoring"])
    assert not has_meta_assignment([])


@pytest.mark.asyncio
async def test_create_rejects_org_wide_without_meta_assignment(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any
) -> None:
    before = await _write_footprint(db_session, seed_organization.id)
    with pytest.raises(OrgWideMetaIneligible):
        await create_control(
            db_session,
            org_id=seed_organization.id,
            user_id=seed_user.id,
            form=_form("lec_prev_resistance", org_wide=True),
        )
    await _assert_refusal_wrote_nothing(db_session, seed_organization.id, before)


@pytest.mark.asyncio
async def test_create_rejects_lec_sourced_derived_row_as_only_dsc(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any
) -> None:
    """Review-focus #4 / D6: LEC rows + a derived dsc_corr_misaligned row sourced
    from lec_resp_resilience is NOT inheritable -> ineligible, not run-failing."""
    before = await _write_footprint(db_session, seed_organization.id)
    # The message must not tell the analyst a Decision Support function is enough: the
    # derived link counts only when it is sourced from a Variance Management function.
    with pytest.raises(OrgWideMetaIneligible, match="Correcting Misaligned Decisions link"):
        await create_control(
            db_session,
            org_id=seed_organization.id,
            user_id=seed_user.id,
            form=_form("lec_prev_resistance", "lec_resp_resilience", org_wide=True),
            dsc_corr_source=FairCamSubFunction.LEC_RESP_RESILIENCE,
        )
    await _assert_refusal_wrote_nothing(db_session, seed_organization.id, before)


@pytest.mark.asyncio
async def test_create_accepts_hybrid_org_wide_and_audits_flag_and_sweeps(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any, seed_run_factory: Any
) -> None:
    done = await seed_run_factory(status=RunStatus.COMPLETED)
    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("lec_prev_resistance", "vmc_corr_implementation", org_wide=True),
    )
    await db_session.commit()
    assert c.org_wide_meta is True
    (row,) = await _audits(db_session, "control.create", c.id)
    assert row.changes["org_wide_meta"] == [None, True]
    (sweep,) = await _audits(db_session, "control.org_wide_sweep", c.id)
    assert sweep.changes["trigger"] == [None, "create"]
    assert await _is_stale(db_session, done) is True


@pytest.mark.asyncio
async def test_update_rejects_removing_last_meta_assignment_while_org_wide(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any
) -> None:
    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("lec_prev_resistance", "vmc_corr_implementation", org_wide=True),
    )
    await db_session.commit()
    await _loaded(db_session, c)
    before = await _write_footprint(db_session, seed_organization.id)
    with pytest.raises(OrgWideMetaIneligible):
        await update_control(
            db_session,
            control=c,
            user_id=seed_user.id,
            # The rename makes a check that ran after the control-level diff loop visible
            # as a dirty attribute on `c` (the assignment drop alone would not touch it).
            form=_form("lec_prev_resistance", org_wide=True, name="C renamed"),
        )
    await _assert_refusal_wrote_nothing(db_session, seed_organization.id, before)
    assert c.name == "C"
    assert await _audits(db_session, "control.update", c.id) == []
    await db_session.rollback()
    await db_session.refresh(c)
    assert c.org_wide_meta is True
    assert {a.sub_function.value for a in c.assignments} == {
        "lec_prev_resistance",
        "vmc_corr_implementation",
    }


@pytest.mark.parametrize(
    ("mutate", "trigger"),
    [
        (lambda f: f(org_wide=True), "flag"),
        (lambda f: f(status=EntityStatus.DRAFT), "status"),
        (lambda f: f(implementation_stage=ControlImplementationStage.PLANNED), "stage"),
    ],
)
@pytest.mark.asyncio
async def test_update_sweeps_on_each_effective_set_change(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_run_factory: Any,
    mutate: Callable[[Callable[..., ControlForm]], ControlForm],
    trigger: str,
) -> None:
    """Review-focus #2 / spec D9: flag toggle, status change, stage change on an
    org-wide control each flag the org's COMPLETED runs stale with one audit row."""
    start_org_wide = trigger != "flag"
    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=start_org_wide),
    )
    await db_session.commit()
    await _loaded(db_session, c)
    done = await seed_run_factory(status=RunStatus.COMPLETED)

    def _f(**kw: Any) -> ControlForm:
        base: dict[str, Any] = {"org_wide": start_org_wide}
        base.update(kw)
        return _form("vmc_corr_implementation", **base)

    await update_control(db_session, control=c, user_id=seed_user.id, form=mutate(_f))
    await db_session.commit()
    assert await _is_stale(db_session, done) is True
    # status/stage cases: the control was created org-wide -> ["create", trigger];
    # flag case: created with the flag off -> [trigger] only.
    expected = [trigger] if trigger == "flag" else ["create", trigger]
    assert await _sweep_triggers(db_session, c.id) == expected
    sweep = (await _audits(db_session, "control.org_wide_sweep", c.id))[-1]
    assert sweep.changes["trigger"] == [None, trigger]
    assert sweep.changes["runs_flagged_stale"] == [
        None,
        1,
    ]  # `done` was created AFTER the create sweep
    if trigger == "flag":
        (upd,) = await _audits(db_session, "control.update", c.id)
        assert upd.changes["org_wide_meta"] == [False, True]


@pytest.mark.asyncio
async def test_update_assignment_edit_on_org_wide_sweeps_but_lec_only_control_edit_does_not(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any, seed_run_factory: Any
) -> None:
    ow = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True, name="OW"),
    )
    plain = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("lec_prev_resistance", org_wide=False, name="Plain"),
    )
    await db_session.commit()
    await _loaded(db_session, ow, plain)
    done = await seed_run_factory(status=RunStatus.COMPLETED)
    await update_control(
        db_session,
        control=plain,
        user_id=seed_user.id,
        form=_form("lec_prev_resistance", "lec_det_visibility", org_wide=False, name="Plain"),
    )
    await db_session.commit()
    assert (
        await _is_stale(db_session, done) is False
    )  # per-control sweep only; run didn't use `plain`
    await update_control(
        db_session,
        control=ow,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", "dsc_prev_communication", org_wide=True, name="OW"),
    )
    await db_session.commit()
    assert await _is_stale(db_session, done) is True
    assert await _sweep_triggers(db_session, ow.id) == ["create", "assignments"]
    assert await _sweep_triggers(db_session, plain.id) == []  # never org-wide


@pytest.mark.parametrize(
    ("edited_fns", "form_kw", "cfa_action"),
    [
        pytest.param(
            ("vmc_corr_implementation", "dsc_prev_communication"),
            {"coverage": 0.4},
            "control_function_assignment.update",
            id="update",
        ),
        pytest.param(
            ("vmc_corr_implementation", "dsc_prev_communication"),
            {"capability_value": None},
            "control_function_assignment.clear",
            id="clear",
        ),
        pytest.param(
            ("vmc_corr_implementation",),
            {},
            "control_function_assignment.delete",
            id="delete",
        ),
    ],
)
@pytest.mark.asyncio
async def test_update_each_assignment_edit_kind_on_org_wide_control_sweeps_with_assignments_trigger(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_run_factory: Any,
    edited_fns: tuple[str, ...],
    form_kw: dict[str, Any],
    cfa_action: str,
) -> None:
    """Spec D9: an assignment `.update`, a `.clear` (capability non-NULL -> NULL) and the
    delete of a removed row each change the inherited variant of an effective org-wide
    control, so each flags the org's COMPLETED runs stale with one "assignments" sweep row
    (the insert kind is pinned by the test above). Nothing else on the control changes
    (name / flag / status / stage identical), so only the assignment branch can fire."""
    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", "dsc_prev_communication", org_wide=True),
    )
    await db_session.commit()
    await _loaded(db_session, c)
    done = await seed_run_factory(status=RunStatus.COMPLETED)

    await update_control(
        db_session,
        control=c,
        user_id=seed_user.id,
        form=_form(*edited_fns, org_wide=True, **form_kw),
    )
    await db_session.commit()

    # The CFA audit verb proves the intended branch ran (and not another one).
    cfa_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == cfa_action,
                    AuditLog.organization_id == seed_organization.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert cfa_rows
    assert await _is_stale(db_session, done) is True
    assert await _sweep_triggers(db_session, c.id) == ["create", "assignments"]
    sweep = (await _audits(db_session, "control.org_wide_sweep", c.id))[-1]
    assert sweep.changes["runs_flagged_stale"] == [None, 1]  # `done` post-dates the create sweep


@pytest.mark.asyncio
async def test_update_derived_link_change_alone_sweeps_but_confirm_restamp_does_not(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any, seed_run_factory: Any
) -> None:
    """Sec-I7 / spec D9: adding or clearing the Correcting-Misaligned link on an
    effective org-wide control changes the variant (the VMC-sourced derived row
    is inherited, D6) -> one "assignments" sweep. A save that changes nothing
    (same rows, same flag) only re-stamps `.confirm` rows and must NOT sweep."""
    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True),
    )
    await db_session.commit()
    await _loaded(db_session, c)
    done = await seed_run_factory(status=RunStatus.COMPLETED)
    # no-op re-save: .confirm rows only
    await update_control(
        db_session,
        control=c,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True),
    )
    await db_session.commit()
    assert await _is_stale(db_session, done) is False
    assert await _sweep_triggers(db_session, c.id) == ["create"]
    # link-only change: rows identical, dsc_corr_source None -> VMC_CORR_IMPLEMENTATION
    await update_control(
        db_session,
        control=c,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True),
        dsc_corr_source=FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    )
    await db_session.commit()
    assert await _is_stale(db_session, done) is True
    assert await _sweep_triggers(db_session, c.id) == ["create", "assignments"]
    # and clearing it again sweeps again (the variant shrinks)
    done2 = await seed_run_factory(status=RunStatus.COMPLETED)
    await update_control(
        db_session,
        control=c,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True),
    )
    await db_session.commit()
    assert await _is_stale(db_session, done2) is True
    assert await _sweep_triggers(db_session, c.id) == ["create", "assignments", "assignments"]


@pytest.mark.asyncio
async def test_vmc_sourced_derived_row_control_is_eligible_and_keeps_both_rows(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any
) -> None:
    """Meth-N5: `_check_org_wide_eligible` sees form slugs only (the derived row is
    created separately from dsc_corr_source) — equivalent under D6 because a
    derived row is inheritable only when its source is a meta row that IS in
    the form. Pin the equivalence: eligible, and meta_assignments() keeps 2 rows."""
    from idraa.services.inherited_controls import meta_assignments

    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True),
        dsc_corr_source=FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    )
    await db_session.commit()
    await db_session.refresh(
        c, ["assignments"]
    )  # lazy="selectin" relationship (models/control.py:112-116)
    kept = {a.sub_function.value for a in meta_assignments(c)}
    assert kept == {"vmc_corr_implementation", "dsc_corr_misaligned"}


@pytest.mark.asyncio
async def test_soft_delete_of_org_wide_control_sweeps(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any, seed_run_factory: Any
) -> None:
    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True),
    )
    await db_session.commit()
    done = await seed_run_factory(status=RunStatus.COMPLETED)
    await soft_delete_control(db_session, c, user_id=seed_user.id)
    await db_session.commit()
    assert await _is_stale(db_session, done) is True
    assert await _sweep_triggers(db_session, c.id) == ["create", "delete"]
    sweep = (await _audits(db_session, "control.org_wide_sweep", c.id))[-1]
    assert sweep.changes["runs_flagged_stale"] == [None, 1]


@pytest.mark.asyncio
async def test_duplicate_resets_org_wide_flag(
    db_session: AsyncSession, seed_organization: Any, seed_user: Any
) -> None:
    c = await create_control(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        form=_form("vmc_corr_implementation", org_wide=True),
    )
    await db_session.commit()
    await _loaded(db_session, c)
    clone = await duplicate_control(db_session, control=c, user_id=seed_user.id)
    await db_session.commit()
    assert clone.org_wide_meta is False
    assert clone.status is EntityStatus.DRAFT


@pytest.mark.asyncio
async def test_list_and_count_org_wide_meta_filter_status_stage_and_org(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_organization_factory: Any,
    seed_control_factory: Any,
) -> None:
    other = await seed_organization_factory(name="Other")
    a = await seed_control_factory(name="A", domain=ControlDomain.VARIANCE_MANAGEMENT)
    b = await seed_control_factory(name="B", domain=ControlDomain.VARIANCE_MANAGEMENT)
    c = await seed_control_factory(name="C", domain=ControlDomain.VARIANCE_MANAGEMENT)
    d = await seed_control_factory(name="D", domain=ControlDomain.VARIANCE_MANAGEMENT)
    theirs = await seed_control_factory(
        name="Theirs", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=other.id
    )
    a.org_wide_meta = True
    b.org_wide_meta = True
    b.implementation_stage = ControlImplementationStage.PLANNED
    c.org_wide_meta = True
    c.status = EntityStatus.DELETED
    d.org_wide_meta = False
    theirs.org_wide_meta = True  # spec §9 T1: org-B flag never reaches org A
    await db_session.commit()
    repo = ControlRepo(db_session)
    got = await repo.list_org_wide_meta(seed_organization.id)
    assert [x.id for x in got] == [a.id]
    assert got[0].assignments  # eager-loaded
    assert await repo.count_org_wide_meta(seed_organization.id) == 1
    assert [x.id for x in await repo.list_org_wide_meta(other.id)] == [theirs.id]


@pytest.mark.asyncio
async def test_flag_runs_stale_for_org_is_org_scoped_and_idempotent(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_organization_factory: Any,
    seed_user: Any,
    seed_scenario_factory: Any,
) -> None:
    other_org = await seed_organization_factory(name="Other")
    s = await seed_scenario_factory(name="S")
    s_other = await seed_scenario_factory(name="S other", organization_id=other_org.id)

    def _run(
        org_id: uuid.UUID, sid: uuid.UUID, status: RunStatus, stale: bool = False
    ) -> RiskAnalysisRun:
        return RiskAnalysisRun(
            id=uuid.uuid4(),
            organization_id=org_id,
            scenario_id=sid,
            run_type=RunType.SINGLE,
            status=status,
            mc_iterations=100,
            random_seed=42,
            inputs_hash="x",
            controls_snapshot=[],
            control_ids_used=[],
            created_by=seed_user.id,
            is_stale=stale,
        )

    mine_done = _run(seed_organization.id, s.id, RunStatus.COMPLETED)
    mine_failed = _run(seed_organization.id, s.id, RunStatus.FAILED)
    mine_already = _run(seed_organization.id, s.id, RunStatus.COMPLETED, stale=True)
    theirs = _run(other_org.id, s_other.id, RunStatus.COMPLETED)
    db_session.add_all([mine_done, mine_failed, mine_already, theirs])
    await db_session.commit()

    assert await flag_runs_stale_for_org(db_session, organization_id=seed_organization.id) == 1
    await db_session.commit()
    for r in (mine_done, mine_failed, mine_already, theirs):
        await db_session.refresh(r)
    assert mine_done.is_stale is True
    assert mine_failed.is_stale is False
    assert theirs.is_stale is False
    assert await flag_runs_stale_for_org(db_session, organization_id=seed_organization.id) == 0
