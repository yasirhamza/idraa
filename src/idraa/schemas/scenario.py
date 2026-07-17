"""Scenario Pydantic schemas — form input.

ScenarioForm is the create/update request shape. FK validity (overlays
exist) is checked at the service layer where the DB session is available;
Pydantic validators here cover field-level constraints only.

Security-critical validators (post-paranoid-review preamble fold-ins):

- ``model_config = ConfigDict(extra="forbid")`` blocks form-field
  smuggling. Without it, a malicious POST body could include
  ``organization_id``, ``row_version``, or pin fields and have them
  model_dump'ed onto the ORM row alongside the legitimate fields.

PR pi F12 cleanup notes:
- ``iris_calibration_year`` was dropped from this form (column still
  exists on the ORM model with a hardcoded 2025 default; column drops
  in F14). Per-year revenue-tier validation collapses to the 2025 set.
- ``mc_iterations`` was dropped — it now lives on the run-creation
  form, not the scenario form.
- ``RefreshDiff`` was dropped alongside ``ScenarioService.refresh_calibration``.

Issue #88 Task 9 notes:
- ``industry`` and ``revenue_tier`` were dropped from this form. They are
  now org-level attributes derived by :func:`idraa.services.calibration.
  calibration_context_from_org` and stamped onto the Scenario row by the
  service layer. The Scenario ORM columns remain NOT NULL until Task 12.

``version: str`` is the analyst-chosen descriptive label (e.g. "1.0",
"draft-2"); it is NOT the optimistic-lock primitive — that role
belongs to ``Scenario.row_version: int`` per spec §5.10 + Q9
paranoid-review fix.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from idraa.models.enums import EntityStatus, ScenarioEffect, ScenarioSource, ScenarioType


class ScenarioForm(BaseModel):
    """Validated scenario create/update request payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    scenario_type: ScenarioType = ScenarioType.CUSTOM
    threat_category: str = Field(min_length=1, max_length=64)
    threat_actor_type: str | None = Field(default=None, max_length=64)
    attack_vector: str | None = Field(default=None, max_length=128)
    asset_class: str | None = Field(default=None, max_length=128)
    effect: str | None = Field(default=None, max_length=32)

    @field_validator("effect")
    @classmethod
    def _effect_is_valid_enum_member(cls, v: str | None) -> str | None:
        """PR #451 final-gate security N-1: reject non-enum effect strings at
        the form boundary (defense-in-depth; the ORM column is a plain str)."""
        if v is None or v == "":
            return None
        allowed = {e.value for e in ScenarioEffect}
        if v not in allowed:
            raise ValueError(f"effect must be one of {sorted(allowed)}")
        return v

    threat_event_frequency: dict[str, Any]
    vulnerability: dict[str, Any]
    primary_loss: dict[str, Any]
    secondary_loss: dict[str, Any] | None = None

    source: ScenarioSource = ScenarioSource.EXPERT_JUDGMENT
    status: EntityStatus = EntityStatus.ACTIVE
    version: str = Field(default="1.0", max_length=32)

    # Optional: when set, expert-form create auto-resolves library_pin via
    # ScenarioLibraryService.resolve_for_clone and sets source=LIBRARY_DERIVED.
    # Wizard path passes library_pin directly to create_from_wizard(); this
    # field is unused on that path (library_pin already resolved by step-1).
    library_entry_id: uuid.UUID | None = Field(default=None)
