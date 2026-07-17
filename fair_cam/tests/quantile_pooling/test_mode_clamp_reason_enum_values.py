"""Meth-11 R3: typed-enum members serialize to EXACT strings in spec §6.4."""

from fair_cam.quantile_pooling import ModeClampReason


def test_enum_values_match_sidecar_schema_v1() -> None:
    assert (
        ModeClampReason.UNTRUNCATED_MODE_BELOW_MIN_SUPPORT.value
        == "untruncated_mode_below_min_support"
    )
    assert (
        ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT.value
        == "untruncated_mode_above_max_support"
    )
    assert ModeClampReason.MODE_ABOVE_PERT_HIGH.value == "mode_above_pert_high"
    assert ModeClampReason.MODE_BELOW_PERT_LOW.value == "mode_below_pert_low"


def test_enum_has_exactly_four_members() -> None:
    assert len(list(ModeClampReason)) == 4
