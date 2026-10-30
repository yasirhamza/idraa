"""#299 adapter contract: the meta-only variant keeps EVERY inheritable row
(meta rows incl. a derived dsc_corr_misaligned row with a META source) and
drops EVERY LEC row AND an LEC-sourced derived row (spec D6). N >= 3 on both
sides so a [0]/[-1] regression cannot pass. Also pins the app-built variant
against the Task 1 hand-math anchor."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fair_cam.models.composition_topology import BooleanGroup
from fair_cam.risk_engine.control_aware import _group_comp_to_node_multipliers
from fair_cam.risk_engine.group_composition import compose_groups, precompose_parts

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.schemas.run_snapshot import ControlSnapshotV3
from idraa.services.inherited_controls import (
    INHERITED_SUFFIX,
    base_control_id,
    is_variant_id,
    meta_assignments,
    variant_id,
)
from idraa.services.run_executor import _snapshot_control_v3, _v3_to_fair_cam_control

META = [
    FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
    FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    FairCamSubFunction.DSC_ID_MISALIGNED,
]
LEC = [
    FairCamSubFunction.LEC_PREV_RESISTANCE,
    FairCamSubFunction.LEC_DET_VISIBILITY,
    FairCamSubFunction.LEC_RESP_RESILIENCE,
]
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _row(
    c: Control,
    sf: FairCamSubFunction,
    cap: float = 0.6,
    cov: float = 0.9,
    rel: float = 0.8,
    *,
    confirmed: datetime | None = _T0,
    **kw: Any,
) -> ControlFunctionAssignment:
    # `confirmed` is an explicit parameter (not a **kw key) so the derived row below can
    # pass None without colliding with the default — Meth-I7 / SC-I15.
    return ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=c.id,
        sub_function=sf,
        capability_value=cap,
        coverage=cov,
        reliability=rel,
        confirmed_by_user_at=confirmed,
        **kw,
    )


def _control(name: str = "Patch Management") -> Control:
    return Control(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=0,
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        version="1.0",
    )


def _hybrid(
    derived_source: FairCamSubFunction = FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
) -> Control:
    """META + LEC rows plus a derived Correcting-Misaligned-Decisions row (#268)
    linked to ``derived_source`` (a DERIVABLE_SOURCES member)."""
    c = _control()
    rows = [_row(c, sf) for sf in META + LEC]
    src = next(r for r in rows if r.sub_function == derived_source)
    rows.append(
        _row(
            c,
            FairCamSubFunction.DSC_CORR_MISALIGNED,
            cap=0.0,
            cov=0.0,
            rel=0.0,
            confirmed=None,
            derived_from_assignment_id=src.id,
        )
    )
    c.assignments = rows
    return c


def test_id_helpers_roundtrip():
    cid = str(uuid.uuid4())
    assert variant_id(cid) == cid + INHERITED_SUFFIX
    assert is_variant_id(variant_id(cid)) and not is_variant_id(cid)
    assert base_control_id(variant_id(cid)) == cid
    assert base_control_id(cid) == cid


def test_meta_assignments_keeps_all_meta_rows_and_vmc_sourced_derived_row():
    c = _hybrid()
    kept = {a.sub_function for a in meta_assignments(c)}
    assert kept == set(META) | {FairCamSubFunction.DSC_CORR_MISALIGNED}
    assert not (kept & set(LEC))


def test_meta_assignments_drops_lec_sourced_derived_row_with_its_source():
    """Review-focus #4 / spec D6 / register C12."""
    c = _hybrid(derived_source=FairCamSubFunction.LEC_RESP_RESILIENCE)
    kept = {a.sub_function for a in meta_assignments(c)}
    assert kept == set(META)
    var = _v3_to_fair_cam_control(c, meta_only=True)  # resolver sees no stranded link
    assert {a.sub_function.value for a in var.assignments} == {sf.value for sf in META}
    full = _v3_to_fair_cam_control(c)  # plain path still resolves the LEC-sourced link
    assert len(full.assignments) == len(META) + len(LEC) + 1


def test_meta_assignments_raises_on_lec_only_control():
    c = _hybrid()
    c.assignments = [a for a in c.assignments if a.sub_function in LEC]
    with pytest.raises(ValueError):
        meta_assignments(c)


def test_mapper_meta_only_variant():
    c = _hybrid()
    full = _v3_to_fair_cam_control(c)
    var = _v3_to_fair_cam_control(c, meta_only=True)
    assert full.control_id == str(c.id)
    assert var.control_id == variant_id(str(c.id))
    assert var.name == "Patch Management (org-wide)"
    var_sfs = {a.sub_function.value for a in var.assignments}
    assert var_sfs == {sf.value for sf in META} | {"dsc_corr_misaligned"}
    assert len(var.assignments) == len(META) + 1
    assert len(full.assignments) == len(META) + len(LEC) + 1
    assert var.cost_model.annual_cost == full.cost_model.annual_cost  # spec D7


def test_snapshot_meta_only_variant_and_plain_domains_unchanged():
    c = _hybrid()
    snap = _snapshot_control_v3(c, meta_only=True)
    assert isinstance(snap, ControlSnapshotV3)
    assert snap.control_id == variant_id(str(c.id))
    assert snap.inherited_variant_of == str(c.id)
    assert snap.name == "Patch Management (org-wide)"
    assert {a.sub_function.value for a in snap.assignments} == {sf.value for sf in META} | {
        "dsc_corr_misaligned"
    }
    assert set(snap.domains) == {"variance_management", "decision_support"}
    plain = _snapshot_control_v3(c)
    assert plain.inherited_variant_of is None
    assert len(plain.assignments) == len(META) + len(LEC) + 1
    # Meth-I4: plain mode keeps Control.domains (which includes derived rows)
    assert plain.domains == sorted(d.value for d in c.domains)
    assert set(plain.domains) == {"loss_event", "variance_management", "decision_support"}


def test_variant_composes_without_raising_when_only_label_only_meta_row():
    """Review-focus #1: vmc_corr_treatment_selection alone is META (eligible)
    and composes to E_meta == 0 without error."""
    c = _control()
    c.assignments = [
        _row(c, FairCamSubFunction.LEC_PREV_RESISTANCE),
        _row(c, FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION, cap=0.7, cov=1.0, rel=1.0),
    ]
    var = _v3_to_fair_cam_control(c, meta_only=True)
    assert precompose_parts([var]).e_meta == 0.0


def test_app_built_variant_matches_task1_hand_math_anchor():
    """Cross-layer anchor: ORM rows -> mapper(meta_only) -> fair_cam reproduce the
    Task 1 constants (expected column) — the actual column is this run."""
    edr = _control("EDR")
    edr.assignments = [_row(edr, FairCamSubFunction.LEC_PREV_RESISTANCE, cap=0.7, cov=0.8, rel=0.8)]
    pm = _control("PM")
    pm.assignments = [
        _row(pm, FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB, cap=0.7, cov=0.8, rel=0.85),
        _row(pm, FairCamSubFunction.LEC_PREV_RESISTANCE, cap=0.9, cov=1.0, rel=1.0),
    ]
    comp = compose_groups(
        [_v3_to_fair_cam_control(edr), _v3_to_fair_cam_control(pm, meta_only=True)]
    )
    assert comp.meta_strength == pytest.approx(0.476, rel=1e-12)
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(
        0.474656, rel=1e-12
    )
    m = _group_comp_to_node_multipliers(comp)
    assert m["threat_event_frequency"] == pytest.approx(0.6202752, rel=1e-12)
    assert m["vulnerability"] == pytest.approx(0.5728096, rel=1e-12)
    linked = compose_groups([_v3_to_fair_cam_control(edr), _v3_to_fair_cam_control(pm)])
    assert linked.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(
        0.9474656, rel=1e-12
    )
