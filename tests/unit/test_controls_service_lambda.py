"""PR λ service-layer tests for duplicate_control + update_control patch.

update_control patch supersedes the prior PR ι behavior documented in
update_control's docstring ("confirmed_by_user_at is NOT bumped on
update — only confirm_assignment sets it. (OQ4, spec §4.8)") per Q5(a)
brainstorm decision: save = confirm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction
from idraa.schemas.control import ControlForm, ControlFunctionAssignmentDTO
from idraa.services.controls import create_control, duplicate_control, update_control


@pytest.mark.asyncio
async def test_update_control_sets_confirmed_by_user_at_now_for_all_rows(
    db_session, organization, admin_user
):
    """Q5(a) save = confirm: every assignment in the form gets
    confirmed_by_user_at = now() on update, regardless of prior state.
    Supersedes prior PR ι 'OQ4/§4.8' rule.
    """
    db, org, user = db_session, organization, admin_user

    # Create with one assignment that's confirmed long ago
    create_form = ControlForm(
        name="Test Control",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.8,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    control = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Manually set confirmed_by_user_at to "old" timestamp
    old_ts = datetime.now(UTC) - timedelta(days=30)
    for a in control.assignments:
        a.confirmed_by_user_at = old_ts
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Now update via form — same assignments, no field changes
    update_form = ControlForm(
        name=control.name,
        description=control.description or "",
        type=control.type,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=a.sub_function,
                capability_value=a.capability_value,
                coverage=a.coverage,
                reliability=a.reliability,
            )
            for a in control.assignments
        ],
    )
    pre_update_now = datetime.now(UTC)
    await update_control(db, control=control, user_id=user.id, form=update_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Every assignment must have confirmed_by_user_at >= pre_update_now (bumped to now)
    for a in control.assignments:
        assert a.confirmed_by_user_at is not None
        assert a.confirmed_by_user_at >= pre_update_now, (
            f"Q5(a) save = confirm requires confirmed_by_user_at = now() on update; "
            f"got {a.confirmed_by_user_at} (pre-update was {pre_update_now})"
        )


@pytest.mark.asyncio
async def test_update_control_confirms_previously_unconfirmed_rows(
    db_session, organization, admin_user
):
    """Backfilled assignments (confirmed_by_user_at = NULL) get confirmed
    on save. This is the maintenance-flow shortcut: edit the control and
    save → all previously-unconfirmed rows are now confirmed.
    """
    db, org, user = db_session, organization, admin_user

    # Create + manually NULL out confirmed_by_user_at to simulate backfill
    create_form = ControlForm(
        name="Backfilled Control",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    control = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])
    for a in control.assignments:
        a.confirmed_by_user_at = None  # simulate backfill
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Save unchanged via update
    update_form = ControlForm(
        name=control.name,
        description="",
        type=control.type,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=a.sub_function,
                capability_value=a.capability_value,
                coverage=a.coverage,
                reliability=a.reliability,
            )
            for a in control.assignments
        ],
    )
    await update_control(db, control=control, user_id=user.id, form=update_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    for a in control.assignments:
        assert a.confirmed_by_user_at is not None


@pytest.mark.asyncio
async def test_update_control_emits_confirm_audit_row_on_noop_save(
    db_session, organization, admin_user
):
    """No-op save (form identical to DB) still bumps confirmed_by_user_at AND
    emits a control_function_assignment.confirm audit row. Defends against
    Q5(a) silent audit-trail regression.
    """
    db, org, user = db_session, organization, admin_user

    from sqlalchemy import select

    from idraa.models.audit_log import AuditLog

    create_form = ControlForm(
        name="Noop Test",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    control = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Resave identical form
    update_form = ControlForm(**create_form.model_dump())
    await update_control(db, control=control, user_id=user.id, form=update_form)
    await db.commit()

    confirm_rows = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "control_function_assignment",
                    AuditLog.action == "control_function_assignment.confirm",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(confirm_rows) == 1


@pytest.mark.asyncio
async def test_update_control_real_edit_emits_both_update_and_confirm_audit_rows(
    db_session, organization, admin_user
):
    """A real edit (changes capability_value) emits BOTH update AND confirm
    audit rows for the same assignment. Pins the intentional double-audit
    contract from Q5(a) save = confirm interaction with the existing
    update-on-diff pattern (paranoid-review M7).
    """
    db, org, user = db_session, organization, admin_user

    from sqlalchemy import select

    from idraa.models.audit_log import AuditLog

    create_form = ControlForm(
        name="Real Edit Test",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.5,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    control = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(control, attribute_names=["assignments"])

    # Real edit — change capability_value
    update_form = ControlForm(
        name=control.name,
        description="",
        type=control.type,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.9,  # changed
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    await update_control(db, control=control, user_id=user.id, form=update_form)
    await db.commit()

    rows = (
        (
            await db.execute(
                select(AuditLog).where(AuditLog.entity_type == "control_function_assignment")
            )
        )
        .scalars()
        .all()
    )
    actions = {r.action for r in rows}
    # Expected: create (from initial create_control) + update + confirm
    assert "control_function_assignment.update" in actions
    assert "control_function_assignment.confirm" in actions


@pytest.mark.asyncio
async def test_duplicate_clones_all_assignments(db_session, organization, admin_user):
    """Clone preserves all source assignments with new IDs."""
    db, org, user = db_session, organization, admin_user

    create_form = ControlForm(
        name="Source",
        description="orig",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.8,
                coverage=0.9,
                reliability=0.85,
            ),
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_DET_VISIBILITY,
                capability_value=0.6,
                coverage=0.7,
                reliability=0.75,
            ),
        ],
    )
    source = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(source, attribute_names=["assignments"])

    clone = await duplicate_control(db, control=source, user_id=user.id)
    await db.commit()
    await db.refresh(clone, attribute_names=["assignments"])
    await db.refresh(source, attribute_names=["assignments"])

    assert clone.id != source.id
    assert clone.name == "Source (copy)"
    assert clone.status == EntityStatus.DRAFT
    assert len(clone.assignments) == 2
    src_subfns = {a.sub_function for a in source.assignments}
    clone_subfns = {a.sub_function for a in clone.assignments}
    assert src_subfns == clone_subfns
    # New IDs everywhere
    src_ids = {a.id for a in source.assignments}
    clone_ids = {a.id for a in clone.assignments}
    assert src_ids.isdisjoint(clone_ids)


@pytest.mark.asyncio
async def test_duplicate_clears_confirmed_by_user_at(db_session, organization, admin_user):
    """Clone is not an authoring act — confirmed_by_user_at is NULL on clone."""
    db, org, user = db_session, organization, admin_user

    create_form = ControlForm(
        name="Source",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    source = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(source, attribute_names=["assignments"])

    clone = await duplicate_control(db, control=source, user_id=user.id)
    await db.commit()
    await db.refresh(clone, attribute_names=["assignments"])

    for a in clone.assignments:
        assert a.confirmed_by_user_at is None
        assert a.measured_by is None
        assert a.measured_at is None


@pytest.mark.asyncio
async def test_duplicate_writes_audit_rows(db_session, organization, admin_user):
    """Audit: one control.duplicate row + one control_function_assignment.create per assignment."""
    db, org, user = db_session, organization, admin_user

    from sqlalchemy import select

    from idraa.models.audit_log import AuditLog

    create_form = ControlForm(
        name="Source",
        description="",
        type=ControlType.TECHNICAL,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.8,
            )
        ],
    )
    source = await create_control(db, org_id=org.id, user_id=user.id, form=create_form)
    await db.commit()
    await db.refresh(source, attribute_names=["assignments"])

    clone = await duplicate_control(db, control=source, user_id=user.id)
    await db.commit()

    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.entity_id == clone.id))).scalars().all()
    )

    actions = {r.action for r in rows}
    assert "control.duplicate" in actions


def test_duplicate_control_excluded_set_covers_all_structural_columns():
    """Defends against silent-clone-of-new-Control-column drift (column scope).

    duplicate_control's reflection-based copy excludes a hardcoded set
    {id, created_at, updated_at, created_by}. If a future PR adds a new
    structural / auditable Control column (e.g., approval_status,
    last_reviewed_by) without extending the excluded set, that column
    will silently clone onto the draft — leaking state.

    This test pins the current Control column set. When it fails, the
    engineer must explicitly decide: should the new column be cloned
    (extend nothing) OR excluded (add to duplicate_control's exclusion
    set + update this test's expected set + add coverage).
    """
    from sqlalchemy.inspection import inspect as sa_inspect

    from idraa.models.control import Control

    # Pinned set as of PR λ. Update intentionally + audit when columns change.
    expected_columns: frozenset[str] = frozenset(
        {
            # IdMixin / TimestampMixin
            "id",
            "created_at",
            "updated_at",
            # OrgMixin
            "organization_id",
            # Authoring
            "created_by",
            # Domain fields
            "name",
            "description",
            # NOTE: "domain" dropped per issue #90 task 2; the control's
            # domain set is derived from assignments via Control.domains.
            "type",
            "status",
            "version",
            "annual_cost",
            "compliance_mappings",
            "nist_csf_functions",
            "iso_27001_domains",
            "skill_requirements",
            "technology_dependencies",
            "applicable_industries",
            "applicable_org_sizes",
            # P2b — provenance columns. Decision: CLONE (not excluded). A
            # duplicate within the org preserves the source's provenance, so a
            # LIBRARY_DERIVED control's clone stays LIBRARY_DERIVED with the same
            # library_pin (reflection copies both; library_pin deep-copied).
            "source",
            "library_pin",
            # #438 — as-adopted snapshot. Decision: CLONE (not excluded), same
            # provenance semantics as library_pin: the duplicate stays
            # LIBRARY_DERIVED with the same pin, so its re-sync diff needs the
            # same as-adopted baseline (deep-copied like library_pin).
            "adopted_snapshot",
            # #395 — implementation maturity. Decision: CLONE (not excluded). A
            # clone of a planned control stays planned; a clone of an active
            # control stays active (a silent reset to active would re-engage the
            # clone in runs). Pinned by test_duplicate_carries_stage in
            # tests/test_controls_service_stage.py.
            "implementation_stage",
        }
    )
    actual_columns = {col.key for col in sa_inspect(Control).columns}
    diff_added = actual_columns - expected_columns
    diff_removed = expected_columns - actual_columns
    assert not diff_added and not diff_removed, (
        f"Control schema drift detected.\n"
        f"  Added columns (decide: clone or exclude in duplicate_control?): {diff_added}\n"
        f"  Removed columns (update this test's expected set): {diff_removed}\n"
    )


def test_duplicate_control_relationships_set_pinned():
    """Defends against silent-clone-of-new-Control-RELATIONSHIP drift
    (paranoid-review M4).

    inspect(Control).columns does NOT return relationship() declarations.
    A future PR adding e.g. `audit_assignments = relationship(...)` or
    `approval_workflow = relationship(...)` would NOT be caught by the
    column-only test above. Pin relationships explicitly so the engineer
    must decide per-relationship whether to clone.

    duplicate_control currently iterates `control.assignments` explicitly
    (the only relationship that needs cloning post-PR-λ). Any new
    relationship requires either (a) explicit clone code in
    duplicate_control + extend this expected set, or (b) explicit decision
    NOT to clone + extend this expected set with the rationale.
    """
    from sqlalchemy.inspection import inspect as sa_inspect

    from idraa.models.control import Control

    expected_relationships: frozenset[str] = frozenset(
        {
            "assignments",  # cloned explicitly in duplicate_control
        }
    )
    actual_relationships = set(sa_inspect(Control).relationships.keys())
    diff_added = actual_relationships - expected_relationships
    diff_removed = expected_relationships - actual_relationships
    assert not diff_added and not diff_removed, (
        f"Control relationship drift detected.\n"
        f"  Added relationships (decide: clone in duplicate_control or document non-clone?): {diff_added}\n"
        f"  Removed relationships (update this test's expected set): {diff_removed}\n"
    )
