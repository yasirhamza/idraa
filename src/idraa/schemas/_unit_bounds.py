"""Single source of truth for FAIR-CAM capability unit-bound validation (M1,
spec §7.1). Imported by both ControlFunctionAssignmentDTO (live form) and
ControlLibraryAssignmentSeed (catalog seed) so the bounds can never drift apart.

NULL is permitted (OQ1 sentinel for TIME/CURRENCY-unit backfills). NaN/Inf
rejected for all units. PROBABILITY/PERCENT_REDUCTION ∈ [0,1]; ELAPSED_TIME/
CURRENCY ≥ 0 if provided."""

from __future__ import annotations

import math

from idraa.models.enums import SUB_FUNCTION_UNITS, FairCamSubFunction, UnitType


def validate_capability_unit_bound(
    capability: float | None, sub_function: FairCamSubFunction, *, field_name: str
) -> None:
    """Raise ValueError if `capability` violates the unit bounds for `sub_function`.
    `field_name` is used in error messages (capability_value vs capability_default)."""
    if capability is None:
        return
    if not math.isfinite(capability):
        raise ValueError(
            f"{field_name} must be finite (got {capability}) for "
            f"sub_function={sub_function.value} (M1, spec §7.1)"
        )
    unit = SUB_FUNCTION_UNITS[sub_function]
    if unit in (UnitType.PROBABILITY, UnitType.PERCENT_REDUCTION):
        if not (0.0 <= capability <= 1.0):
            raise ValueError(
                f"{field_name} must be in [0, 1] for {unit.value} unit "
                f"(sub_function={sub_function.value}); got {capability} (M1, spec §7.1)"
            )
    elif unit in (UnitType.ELAPSED_TIME, UnitType.CURRENCY) and capability < 0:
        raise ValueError(
            f"{field_name} must be non-negative for {unit.value} unit "
            f"(sub_function={sub_function.value}); got {capability} (M1, spec §7.1)"
        )
