"""#299 Meth-I3 / Arch-I3 / SC-I8: for runs WITHOUT inheritance the plan reproduces
today's id lists exactly (fetch order, no dict for SINGLE); M5 strips the suffix."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from idraa.models.risk_analysis_run import RunType
from idraa.services.inherited_controls import variant_id
from idraa.services.run_executor import _build_active_control_plan, _stale_per_scenario_ids


def _ctrl(name: str) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), name=name, assignments=[])


def test_single_without_inheritance_keeps_fetch_order_and_no_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idraa.services.run_executor as rx

    monkeypatch.setattr(
        rx,
        "_snapshot_control_v3",
        lambda c, **kw: SimpleNamespace(control_id=str(c.id), name=c.name),
    )
    c1, c2, c3 = _ctrl("c"), _ctrl("a"), _ctrl("b")
    fetched = [c2, c3, c1]  # fetch order differs from control_ids_used order
    sid = uuid.uuid4()
    run = SimpleNamespace(
        run_type=RunType.SINGLE,
        scenario_id=sid,
        control_ids_used=[str(c1.id), str(c2.id), str(c3.id)],
        aggregate_control_ids_per_scenario=None,
        inherited_control_ids_per_scenario={str(sid): []},
    )
    plan = _build_active_control_plan(run, fetched)  # type: ignore[arg-type]
    today = [str(c.id) for c in fetched]
    assert plan.explicit_ids == today and plan.universe_ids == today
    assert plan.per_scenario_ids == {str(sid): today}
    assert plan.has_inheritance is False
    assert plan.variant_dtos == [] and plan.variant_snapshots == []


def test_aggregate_universe_is_union_of_per_scenario_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import idraa.services.run_executor as rx

    monkeypatch.setattr(
        rx,
        "_snapshot_control_v3",
        lambda c, **kw: SimpleNamespace(
            control_id=variant_id(str(c.id)) if kw.get("meta_only") else str(c.id), name=c.name
        ),
    )
    monkeypatch.setattr(
        rx,
        "_v3_to_fair_cam_control",
        lambda c, **kw: SimpleNamespace(
            control_id=variant_id(str(c.id)), name=c.name + " (org-wide)"
        ),
    )
    edr, pm = _ctrl("EDR"), _ctrl("PM")
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    run = SimpleNamespace(
        run_type=RunType.AGGREGATE,
        scenario_id=None,
        control_ids_used=[str(edr.id), str(pm.id)],
        aggregate_control_ids_per_scenario={a: [str(edr.id)], b: [str(edr.id)]},
        inherited_control_ids_per_scenario={a: [str(pm.id)], b: [str(pm.id)]},
    )
    plan = _build_active_control_plan(run, [edr, pm])  # type: ignore[arg-type]
    assert plan.explicit_ids == [str(edr.id)]  # pm linked nowhere -> no plain row
    assert plan.universe_ids == [str(edr.id), variant_id(str(pm.id))]
    assert set(plan.universe_ids) == set().union(*plan.per_scenario_ids.values())
    assert [s.control_id for s in plan.plain_snapshots] == [str(edr.id)]
    assert plan.has_inheritance is True


def test_three_inherited_controls_are_all_preserved_across_scenarios_with_one_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arch-I10 / CLAUDE.md data-contract rule: the inherited list[Control] ->
    variant_dtos / variant_snapshots / per-scenario lists bridge must keep all N
    (N = 3 here) so a [0] / [-1] / [first] regression cannot pass. P1 is linked on
    A (superseded there) and inherited on B; P2 and P3 are inherited everywhere."""
    import idraa.services.run_executor as rx

    monkeypatch.setattr(
        rx,
        "_snapshot_control_v3",
        lambda c, **kw: SimpleNamespace(
            control_id=variant_id(str(c.id)) if kw.get("meta_only") else str(c.id),
            name=c.name + " (org-wide)" if kw.get("meta_only") else c.name,
        ),
    )
    monkeypatch.setattr(
        rx,
        "_v3_to_fair_cam_control",
        lambda c, **kw: SimpleNamespace(
            control_id=variant_id(str(c.id)), name=c.name + " (org-wide)"
        ),
    )
    edr, p1, p2, p3 = _ctrl("EDR"), _ctrl("P1"), _ctrl("P2"), _ctrl("P3")
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    run = SimpleNamespace(
        run_type=RunType.AGGREGATE,
        scenario_id=None,
        control_ids_used=[str(edr.id), str(p1.id), str(p2.id), str(p3.id)],
        aggregate_control_ids_per_scenario={a: [str(edr.id), str(p1.id)], b: [str(edr.id)]},
        inherited_control_ids_per_scenario={
            a: [str(p2.id), str(p3.id)],
            b: [str(p1.id), str(p2.id), str(p3.id)],
        },
    )
    plan = _build_active_control_plan(run, [edr, p1, p2, p3])  # type: ignore[arg-type]
    v1, v2, v3 = (variant_id(str(p.id)) for p in (p1, p2, p3))
    assert plan.explicit_ids == [str(edr.id), str(p1.id)]
    assert len(plan.variant_dtos) == len(plan.variant_snapshots) == 3
    assert sorted(d.control_id for d in plan.variant_dtos) == sorted([v1, v2, v3])
    assert sorted(s.control_id for s in plan.variant_snapshots) == sorted([v1, v2, v3])
    assert [s.control_id for s in plan.plain_snapshots] == [str(edr.id), str(p1.id)]
    assert plan.per_scenario_ids[a] == [str(edr.id), str(p1.id), v2, v3]  # p1 explicit on A
    assert plan.per_scenario_ids[b] == [str(edr.id), v1, v2, v3]
    assert set(plan.universe_ids) == set().union(*plan.per_scenario_ids.values())
    assert len(plan.universe_ids) == 5 and plan.has_inheritance is True
    assert all(
        name.endswith(" (org-wide)") for cid, name in plan.names.items() if cid in {v1, v2, v3}
    )


def test_stale_per_scenario_ids_strips_variant_suffix() -> None:
    u = {"x", "y"}
    assert _stale_per_scenario_ids({"s": ["x", variant_id("y")]}, u) == {}
    assert _stale_per_scenario_ids({"s": ["x", variant_id("z")]}, u) == {"s": ["z"]}
