"""ScenarioForm.effect enum-membership validator (PR #451 final-gate security N-1).

Precedent: services/scenario_import.py:153-183 enum-membership check. ScenarioForm
had NO existing field_validators before this — this is the first.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from idraa.models.enums import ScenarioEffect
from idraa.schemas.scenario import ScenarioForm


def _minimal_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "test scenario",
        "threat_category": "ransomware",
        "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        "primary_loss": {"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("effect", [e.value for e in ScenarioEffect])
def test_valid_effect_values_pass(effect: str) -> None:
    form = ScenarioForm.model_validate(_minimal_payload(effect=effect))
    assert form.effect == effect


def test_empty_string_normalizes_to_none() -> None:
    form = ScenarioForm.model_validate(_minimal_payload(effect=""))
    assert form.effect is None


def test_none_stays_none() -> None:
    form = ScenarioForm.model_validate(_minimal_payload(effect=None))
    assert form.effect is None


def test_bogus_effect_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="effect must be one of"):
        ScenarioForm.model_validate(_minimal_payload(effect="bogus"))
