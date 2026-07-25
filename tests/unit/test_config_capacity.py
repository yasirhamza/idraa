"""Guard: ``Settings.capacity_k`` (PR2 capacity-bound epic, D13).

D13 (owner-signed 2026-07-25): the per-distribution loss cap is
``k_capacity * Organization.annual_revenue`` — a single loss component is
not modeled to exceed one year's revenue. ``k=1.0`` ships as the default: a
convention, not a FAIR-grounded constant, and NOT the tightest value that
satisfies D8 (D8 is a one-sided upper gate with no lower bound at all — see
``config.py``'s comment on the field for the full caveat).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idraa.config import Settings


def test_capacity_k_default_is_one() -> None:
    s = Settings(environment="test")
    assert s.capacity_k == 1.0


def test_capacity_k_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPACITY_K", "0.5")
    s = Settings(environment="test")
    assert s.capacity_k == 0.5


def test_capacity_k_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", capacity_k=0.0)


def test_capacity_k_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPACITY_K", "-1.0")
    with pytest.raises(ValidationError):
        Settings(environment="test")
