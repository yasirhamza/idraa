"""Issue #268 (spec §3.6): ``meta_tag_notes`` — which VMC/DSC tags the control DETAIL page
mutes, and why.

The helper maps a control's own sub-function slugs to ``{tag code -> reason}`` for the
three cases where a tag does not, on its own, credit loss reduction. Exact strings: they
render in a ``title`` attribute, and the spec fixes their text.
"""

from __future__ import annotations

import pytest

from idraa.services._view_model_helpers import meta_tag_notes

_TS_NOTE = "label-only: Treatment Selection is recorded, never scored (register C8)"
_DSC_ID_NOTE = (
    "identifies misaligned decisions; scores only when a control in the same run corrects them"
)
_DSC_CORR_NOTE = (
    "corrects misaligned decisions; scores only when a control in the same run identifies them"
)


def test_treatment_selection_only_vmc_is_muted() -> None:
    assert meta_tag_notes(["vmc_corr_treatment_selection"]) == {"VMC": _TS_NOTE}


def test_treatment_selection_is_muted_even_beside_non_vmc_slugs() -> None:
    """The mute is about the control's VMC slugs only, not its other channels."""
    assert meta_tag_notes(["vmc_corr_treatment_selection", "lec_prev_avoidance"]) == {
        "VMC": _TS_NOTE
    }


@pytest.mark.parametrize(
    "extra_vmc",
    ["vmc_corr_implementation", "vmc_id_threat_intelligence", "vmc_prev_reduce_change_freq"],
)
def test_treatment_selection_with_another_vmc_slug_is_not_muted(extra_vmc: str) -> None:
    assert meta_tag_notes(["vmc_corr_treatment_selection", extra_vmc]) == {}


def test_dsc_id_misaligned_only_dsc_is_muted() -> None:
    assert meta_tag_notes(["dsc_id_misaligned"]) == {"DSC": _DSC_ID_NOTE}


def test_dsc_corr_misaligned_only_dsc_is_muted() -> None:
    """A derived correcting row counts as the control's DSC slug (spec §3.6)."""
    assert meta_tag_notes(["vmc_corr_implementation", "dsc_corr_misaligned"]) == {
        "DSC": _DSC_CORR_NOTE
    }


def test_dsc_pair_together_is_not_muted() -> None:
    assert meta_tag_notes(["dsc_id_misaligned", "dsc_corr_misaligned"]) == {}


@pytest.mark.parametrize("other", ["dsc_prev_defined_expectations", "dsc_prev_communication"])
def test_dsc_id_misaligned_with_another_dsc_slug_is_not_muted(other: str) -> None:
    assert meta_tag_notes(["dsc_id_misaligned", other]) == {}


def test_both_tags_can_be_muted_at_once() -> None:
    assert meta_tag_notes(["vmc_corr_treatment_selection", "dsc_id_misaligned"]) == {
        "VMC": _TS_NOTE,
        "DSC": _DSC_ID_NOTE,
    }


@pytest.mark.parametrize(
    "slugs",
    [
        [],
        ["lec_prev_avoidance"],
        ["vmc_id_control_monitoring"],
        ["dsc_prev_defined_expectations"],
        ["lec_resp_event_termination", "lec_det_visibility"],
    ],
)
def test_ordinary_slug_sets_have_no_notes(slugs: list[str]) -> None:
    assert meta_tag_notes(slugs) == {}


def test_accepts_any_iterable_and_ignores_order() -> None:
    assert meta_tag_notes(iter(["dsc_id_misaligned"])) == {"DSC": _DSC_ID_NOTE}
    assert meta_tag_notes(("lec_prev_avoidance", "dsc_id_misaligned")) == {"DSC": _DSC_ID_NOTE}
