"""Asset-class dropdown choices must stay in sync with the AssetClass enum
(regression guard: enum grew 2026-05-25 but hardcoded dropdowns drifted, hiding
cash_or_equivalent + business_process_* from authoring)."""

from __future__ import annotations

from idraa.models.enums import AssetClass
from idraa.routes.scenario_form_helpers import ASSET_CLASS_LABELS, asset_class_choices


def test_every_enum_member_has_a_label():
    for m in AssetClass:
        assert m in ASSET_CLASS_LABELS and ASSET_CLASS_LABELS[m].strip()


def test_choices_cover_all_enum_members():
    choice_values = {v for v, _ in asset_class_choices()}
    assert choice_values == {m.value for m in AssetClass}


def test_choices_in_enum_order():
    assert [v for v, _ in asset_class_choices()] == [m.value for m in AssetClass]
