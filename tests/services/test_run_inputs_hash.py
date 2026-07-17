"""Tests for random_seed participation in inputs_hash."""

from __future__ import annotations

from typing import Any, ClassVar

from idraa.services.run_inputs_hash import build_inputs_hash


class _Scn:
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
