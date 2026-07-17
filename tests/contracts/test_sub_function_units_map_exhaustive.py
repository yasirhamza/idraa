"""Issue #129 T3 — exhaustiveness contract test for SUB_FUNCTION_UNITS.

CRITICAL: the T6 modal's bracket lookup `sub_function_units_map[sf]`
silently degrades under default Jinja config when a key is missing
(returns Undefined → falls through to the else-branch copy, no exception
raised). This contract test is the ACTUAL safety net catching enum-vs-map
drift when a future PR adds a new FairCamSubFunction value without
updating SUB_FUNCTION_UNITS.
"""

from __future__ import annotations

from idraa.models.enums import SUB_FUNCTION_UNITS, FairCamSubFunction


def test_sub_function_units_map_is_exhaustive():
    """Every FairCamSubFunction enum member must appear in SUB_FUNCTION_UNITS.

    If this fails, a new sub-function was added without updating the
    units map; the T6 modal would silently render the generic else-branch
    copy for that sub-function. Update SUB_FUNCTION_UNITS in the same PR
    that adds the enum value.
    """
    missing = set(FairCamSubFunction) - set(SUB_FUNCTION_UNITS.keys())
    assert not missing, (
        f"FairCamSubFunction values missing from SUB_FUNCTION_UNITS: {sorted(m.value for m in missing)}. "
        "Add the unit type (PROBABILITY / PERCENT_REDUCTION / ELAPSED_TIME / CURRENCY) "
        "to SUB_FUNCTION_UNITS in src/idraa/models/enums.py."
    )


def test_sub_function_units_map_has_no_orphan_keys():
    """Inverse: no SUB_FUNCTION_UNITS key should be missing from the enum."""
    orphan = set(SUB_FUNCTION_UNITS.keys()) - set(FairCamSubFunction)
    assert not orphan, f"SUB_FUNCTION_UNITS has keys not in FairCamSubFunction: {orphan}"
