"""Tests for random_seed participation in inputs_hash."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar, cast

from idraa.services.run_inputs_hash import (
    ScenarioLike,
    build_aggregate_inputs_hash,
    build_inputs_hash,
)


class _Scn:
    id: ClassVar[uuid.UUID] = uuid.UUID(int=1)
    threat_event_frequency: ClassVar[dict[str, Any]] = {
        "distribution": "pert",
        "low": 1,
        "mode": 2,
        "high": 3,
    }
    vulnerability: ClassVar[dict[str, Any]] = {
        "distribution": "pert",
        "low": 0.1,
        "mode": 0.2,
        "high": 0.3,
    }
    primary_loss: ClassVar[dict[str, Any]] = {
        "distribution": "pert",
        "low": 1,
        "mode": 2,
        "high": 3,
    }
    secondary_loss: ClassVar[None] = None


def test_seed_changes_inputs_hash():
    a = build_inputs_hash(_Scn(), control_ids=[], mc_iterations=10000, random_seed=42)
    b = build_inputs_hash(_Scn(), control_ids=[], mc_iterations=10000, random_seed=43)
    assert a != b


def test_seed_defaults_to_42():
    # omitting random_seed == passing 42 (back-compat for existing callers)
    assert build_inputs_hash(_Scn(), control_ids=[], mc_iterations=10000) == build_inputs_hash(
        _Scn(), control_ids=[], mc_iterations=10000, random_seed=42
    )


def _like(stub: object) -> ScenarioLike:
    """The stubs expose ClassVars; the Protocol wants instance attributes."""
    return cast(ScenarioLike, stub)


def test_inherited_ids_change_hash_and_order_is_irrelevant() -> None:
    scn = _like(_Scn())
    base = build_inputs_hash(scn, control_ids=[], mc_iterations=100)
    a = build_inputs_hash(scn, control_ids=[], mc_iterations=100, inherited_control_ids=["x", "y"])
    b = build_inputs_hash(scn, control_ids=[], mc_iterations=100, inherited_control_ids=["y", "x"])
    assert a == b and a != base


def test_aggregate_hash_without_map_does_not_touch_scenario_id() -> None:
    """Arch-N3: ScenarioLike stubs without `id` keep working when no map is given."""

    class _NoId:
        threat_event_frequency = _Scn.threat_event_frequency
        vulnerability = _Scn.vulnerability
        primary_loss = _Scn.primary_loss
        secondary_loss = None

    assert build_aggregate_inputs_hash(
        [_like(_NoId()), _like(_NoId())], control_ids=[], mc_iterations=100
    )


def test_aggregate_hash_map_is_keyed_by_scenario_id_and_order_independent() -> None:
    """#299: the per-scenario inherited map participates in the AGGREGATE hash,
    keyed by ``str(scenario.id)`` — the same ids on a different scenario change it."""

    class _S2(_Scn):
        # Distinct content as well as id: the aggregate hash sorts per-scenario
        # hashes (content-addressed), so two content-identical scenarios would be
        # indistinguishable and swapping which one inherits would not change it.
        id: ClassVar[uuid.UUID] = uuid.UUID(int=2)
        vulnerability: ClassVar[dict[str, Any]] = {
            "distribution": "pert",
            "low": 0.4,
            "mode": 0.5,
            "high": 0.6,
        }

    s1, s2 = _like(_Scn()), _like(_S2())
    id1, id2 = str(uuid.UUID(int=1)), str(uuid.UUID(int=2))
    base = build_aggregate_inputs_hash([s1, s2], control_ids=[], mc_iterations=100)
    on_s1 = build_aggregate_inputs_hash(
        [s1, s2],
        control_ids=[],
        mc_iterations=100,
        inherited_by_scenario={id1: ["x", "y"], id2: []},
    )
    on_s1_reordered = build_aggregate_inputs_hash(
        [s2, s1],
        control_ids=[],
        mc_iterations=100,
        inherited_by_scenario={id2: [], id1: ["y", "x"]},
    )
    on_s2 = build_aggregate_inputs_hash(
        [s1, s2],
        control_ids=[],
        mc_iterations=100,
        inherited_by_scenario={id1: [], id2: ["x", "y"]},
    )
    assert on_s1 == on_s1_reordered
    assert on_s1 != base
    assert on_s1 != on_s2
