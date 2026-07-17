"""v3↔fair_cam parity for SUB_FUNCTION_UNITS table.

Audit doc §3 is the canonical source; both v3 and fair_cam mirror it.
This test fails loudly if either side drifts. Cross-import lives v3-side
to honor dependency direction (paranoid-review fix A1) — fair_cam must
not import from idraa.
"""

from fair_cam.models.sub_function import SUB_FUNCTION_UNITS as FAIR_CAM_TABLE
from fair_cam.models.sub_function import FairCamSubFunction as FairCamSubFunc
from fair_cam.models.sub_function import UnitType as FairCamUnitType

from idraa.models.enums import SUB_FUNCTION_UNITS as V3_TABLE
from idraa.models.enums import FairCamSubFunction as V3SubFunc
from idraa.models.enums import UnitType as V3UnitType


def test_enum_value_set_parity():
    assert {sf.value for sf in FairCamSubFunc} == {sf.value for sf in V3SubFunc}


def test_unit_type_value_set_parity():
    assert {ut.value for ut in FairCamUnitType} == {ut.value for ut in V3UnitType}


def test_sub_function_units_table_parity():
    """For every sub-function in the union, both tables must agree on UnitType."""
    fair_cam_pairs = {sf.value: ut.value for sf, ut in FAIR_CAM_TABLE.items()}
    v3_pairs = {sf.value: ut.value for sf, ut in V3_TABLE.items()}
    assert fair_cam_pairs == v3_pairs, (
        f"v3↔fair_cam unit-type drift: "
        f"only-in-fair_cam={set(fair_cam_pairs.items()) - set(v3_pairs.items())}, "
        f"only-in-v3={set(v3_pairs.items()) - set(fair_cam_pairs.items())}"
    )
