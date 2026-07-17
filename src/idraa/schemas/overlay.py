"""Overlay form DTO.

Validates admin-edited overlay payloads. The shape mirrors
``OverlayDefinition`` minus mixin-owned columns (``id`` /
``organization_id`` / timestamps) and the bookkeeping columns
``version`` / ``is_active`` (set by the service, not the form).

Security-critical validators (post-paranoid-review preamble fold-ins):

- ``model_config = ConfigDict(extra="forbid")`` blocks form-field
  smuggling. Without it, a malicious POST body could include
  ``organization_id`` or ``version`` and have it model_dump'ed onto the
  ORM row alongside the legitimate fields.
- ``frequency_multiplier`` / ``magnitude_multiplier`` reject
  non-finite values (``inf``/``-inf``/``nan``) and values above a
  ``1e6`` sanity cap. ``Field(gt=0)`` alone would let ``float('inf')``
  through and then the FAIR multiplicative compose would NaN/overflow
  silently downstream.
- ``methodology`` is required to be at least 20 chars after stripping —
  matches the DB CHECK constraint
  ``length(trim(methodology)) >= 20`` so a service that bypasses the
  form (e.g. Alembic seed) cannot insert a row that the form would
  later reject on edit.
- ``tag`` is locked to snake_case starting with a lowercase letter so
  that pinned-revision lookups remain stable across catalogs.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from idraa.models._methodology import METHODOLOGY_MIN_LENGTH

# snake_case identifier: starts with a lowercase letter, only [a-z0-9_].
_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# Sanity cap on multipliers. Anything above this is operator error;
# legitimate overlay multipliers live in the 1.0-10.0 range.
_MULTIPLIER_MAX = 1e6


class OverlayForm(BaseModel):
    """Validated overlay payload from the admin edit form."""

    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    frequency_multiplier: float = Field(gt=0)
    magnitude_multiplier: float = Field(gt=0)
    sources: list[str] = Field(default_factory=list)
    methodology: str
    methodology_change_reason: str = Field(min_length=1)

    @field_validator("tag")
    @classmethod
    def _tag_is_snake_case(cls, v: str) -> str:
        if not _TAG_PATTERN.match(v):
            raise ValueError(
                "tag must be snake_case: lowercase letters, digits, underscores; "
                "must start with a letter"
            )
        return v

    @field_validator("frequency_multiplier", "magnitude_multiplier")
    @classmethod
    def _multiplier_is_finite_and_capped(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("multiplier must be finite (no inf/nan)")
        if v > _MULTIPLIER_MAX:
            raise ValueError(f"multiplier exceeds sanity cap of {_MULTIPLIER_MAX:g}")
        return v

    @field_validator("methodology")
    @classmethod
    def _methodology_min_length(cls, v: str) -> str:
        if len(v.strip()) < METHODOLOGY_MIN_LENGTH:
            raise ValueError(
                f"methodology must be at least {METHODOLOGY_MIN_LENGTH} "
                "non-whitespace characters (matches DB CHECK constraint)"
            )
        return v


class OverlayDeactivateForm(BaseModel):
    """Reason field for the deactivate POST route.

    Separate from :class:`OverlayForm` because deactivate has a single
    field (not a full overlay payload). Forbids extras for the same
    form-field-smuggling reason as :class:`OverlayForm`. The 500-char
    cap matches the audit ``changes`` JSON column's expected payload
    size; the 1-char minimum forces an explicit rationale.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
