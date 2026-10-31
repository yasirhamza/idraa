from fair_cam.models.composition_topology import sub_function_to_group
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.group_composition import _META_GROUPS, meta_sub_functions


def test_meta_sub_functions_is_exactly_the_meta_group_members():
    expected = frozenset(
        sf for sf in FairCamSubFunction if sub_function_to_group(sf) in _META_GROUPS
    )
    assert meta_sub_functions() == expected
    assert expected  # non-empty guard


def test_meta_sub_functions_contains_known_members_and_excludes_lec():
    got = meta_sub_functions()
    assert FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB in got
    assert FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS in got
    assert FairCamSubFunction.VMC_CORR_IMPLEMENTATION in got
    assert FairCamSubFunction.DSC_CORR_MISALIGNED in got
    assert FairCamSubFunction.LEC_PREV_RESISTANCE not in got
    assert FairCamSubFunction.LEC_RESP_LOSS_REDUCTION not in got


def test_meta_sub_functions_is_same_object_on_repeat_calls():
    assert meta_sub_functions() is meta_sub_functions()
