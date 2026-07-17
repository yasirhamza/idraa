"""Boolean group topology — Standard §2.5 + §3-§5 prescribes which sub-functions
compose under which Boolean operator (OR / AND / weak-AND).

Audit doc `docs/reference/fair-cam-standard-alignment.md` §3 table is the
canonical source for both sub-function membership and Boolean composition column.
"""

from fair_cam.models.composition_topology import (
    GROUP_MEMBERSHIP,
    GROUP_TYPE,
    BooleanGroup,
    GroupType,
    sub_function_to_group,
)
from fair_cam.models.sub_function import FairCamSubFunction


def test_boolean_groups_enumerated():
    expected = {
        BooleanGroup.LEC_PREVENTION,
        BooleanGroup.LEC_DETECTION,
        BooleanGroup.LEC_RESPONSE,
        BooleanGroup.LEC_DETECTION_RESPONSE_PAIR,
        BooleanGroup.VMC_VARIANCE_PREVENTION,
        BooleanGroup.VMC_IDENTIFICATION,
        BooleanGroup.VMC_CORRECTION,
        BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR,
        BooleanGroup.DSC_PREVENTION,
        BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR,
    }
    assert set(BooleanGroup) == expected


def test_group_types():
    """Slice 2 (#439) D3 revises VMC_IDENTIFICATION AND->OR and DSC_PREVENTION
    AND->WEAK_AND; see fair_cam/tests/test_composition_topology.py::
    test_meta_group_operators_slice2 for the full grounding/citation table.
    """
    assert GROUP_TYPE[BooleanGroup.LEC_PREVENTION] == GroupType.OR
    assert GROUP_TYPE[BooleanGroup.LEC_DETECTION] == GroupType.AND
    assert GROUP_TYPE[BooleanGroup.LEC_RESPONSE] == GroupType.WEAK_AND
    assert GROUP_TYPE[BooleanGroup.LEC_DETECTION_RESPONSE_PAIR] == GroupType.AND
    assert GROUP_TYPE[BooleanGroup.VMC_VARIANCE_PREVENTION] == GroupType.OR
    assert GROUP_TYPE[BooleanGroup.VMC_IDENTIFICATION] == GroupType.OR  # Slice 2 D3
    assert GROUP_TYPE[BooleanGroup.VMC_CORRECTION] == GroupType.AND
    assert GROUP_TYPE[BooleanGroup.VMC_IDENTIFICATION_CORRECTION_PAIR] == GroupType.AND
    assert GROUP_TYPE[BooleanGroup.DSC_PREVENTION] == GroupType.WEAK_AND  # Slice 2 D3
    assert GROUP_TYPE[BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR] == GroupType.AND


def test_lec_prevention_membership():
    assert GROUP_MEMBERSHIP[BooleanGroup.LEC_PREVENTION] == {
        FairCamSubFunction.LEC_PREV_AVOIDANCE,
        FairCamSubFunction.LEC_PREV_DETERRENCE,
        FairCamSubFunction.LEC_PREV_RESISTANCE,
    }


def test_lec_detection_membership():
    assert GROUP_MEMBERSHIP[BooleanGroup.LEC_DETECTION] == {
        FairCamSubFunction.LEC_DET_VISIBILITY,
        FairCamSubFunction.LEC_DET_MONITORING,
        FairCamSubFunction.LEC_DET_RECOGNITION,
    }


def test_lec_response_membership():
    assert GROUP_MEMBERSHIP[BooleanGroup.LEC_RESPONSE] == {
        FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        FairCamSubFunction.LEC_RESP_RESILIENCE,
        FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
    }


def test_dsc_prevention_membership():
    """All 9 sub-functions of DSC §5.1 are AND-coupled."""
    assert GROUP_MEMBERSHIP[BooleanGroup.DSC_PREVENTION] == {
        FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
        FairCamSubFunction.DSC_PREV_COMMUNICATION,
        FairCamSubFunction.DSC_PREV_SA_DATA_ASSET,
        FairCamSubFunction.DSC_PREV_SA_DATA_THREAT,
        FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS,
        FairCamSubFunction.DSC_PREV_SA_ANALYSIS,
        FairCamSubFunction.DSC_PREV_SA_REPORTING,
        FairCamSubFunction.DSC_PREV_ENSURE_CAPABILITY,
        FairCamSubFunction.DSC_PREV_INCENTIVES,
    }


def test_lookup_helper_round_trip():
    assert (
        sub_function_to_group(FairCamSubFunction.LEC_PREV_AVOIDANCE) == BooleanGroup.LEC_PREVENTION
    )
    assert (
        sub_function_to_group(FairCamSubFunction.LEC_DET_VISIBILITY) == BooleanGroup.LEC_DETECTION
    )
    assert (
        sub_function_to_group(FairCamSubFunction.LEC_RESP_EVENT_TERMINATION)
        == BooleanGroup.LEC_RESPONSE
    )
    assert (
        sub_function_to_group(FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ)
        == BooleanGroup.VMC_VARIANCE_PREVENTION
    )
    assert (
        sub_function_to_group(FairCamSubFunction.DSC_ID_MISALIGNED)
        == BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR
    )
    assert (
        sub_function_to_group(FairCamSubFunction.DSC_CORR_MISALIGNED)
        == BooleanGroup.DSC_IDENTIFICATION_CORRECTION_PAIR
    )


def test_every_subfunction_belongs_to_exactly_one_group():
    """Sanity: each of the 26 sub-functions appears in exactly one
    GROUP_MEMBERSHIP entry. No orphans, no duplicates."""
    appearance_count = dict.fromkeys(FairCamSubFunction, 0)
    for members in GROUP_MEMBERSHIP.values():
        for sf in members:
            appearance_count[sf] += 1
    for sf, count in appearance_count.items():
        assert count == 1, f"{sf.value} appears in {count} groups; expected 1"
