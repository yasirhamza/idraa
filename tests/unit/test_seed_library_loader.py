"""Issue #203: ``LibraryEntrySeed`` rejects industry strings that are not ``IndustryType`` values.

The library browse filter keeps only query values that are ``IndustryType`` members, so a
stray ``applicable_industries`` item or ``calibration_anchor.industry`` would surface as a
facet option that applies no filter. The loader is the shared gate for seed migrations and
library bundle import.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import idraa
from idraa.models.enums import IndustryType
from idraa.services.seed_library_loader import LibraryEntrySeed

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_SEED_FILES = ("seed_library_entries.json", "seed_library_entries_extension.json")
_ALLOWED = sorted(m.value for m in IndustryType)


def _base() -> dict[str, Any]:
    return {
        "slug": "loader-industry-probe",
        "name": "Loader Industry Probe",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_community": "cybercriminals",
        "asset_class": "systems",
        "description": "d" * 25,
        "canonical_fair_gap": "g" * 25,
        "applicable_industries": ["healthcare", "financial"],
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "calibration_anchor": {"industry": "healthcare", "revenue_tier": "100m_to_1b"},
    }


def test_valid_industries_validate() -> None:
    seed = LibraryEntrySeed.model_validate(_base())
    assert seed.applicable_industries == ["healthcare", "financial"]
    assert seed.calibration_anchor["industry"] == "healthcare"


@pytest.mark.parametrize("member", _ALLOWED)
def test_every_industry_type_member_is_accepted_in_both_places(member: str) -> None:
    data = _base()
    data["applicable_industries"] = [member]
    data["calibration_anchor"] = {"industry": member, "revenue_tier": "100m_to_1b"}
    LibraryEntrySeed.model_validate(data)


def test_stray_applicable_industries_item_is_rejected_with_value_and_allowed_set() -> None:
    data = _base()
    data["applicable_industries"] = ["financial", "finance_and_insurance"]
    with pytest.raises(ValidationError) as exc:
        LibraryEntrySeed.model_validate(data)
    msg = str(exc.value)
    assert "applicable_industries item 'finance_and_insurance' is not an IndustryType value" in msg
    assert f"allowed: {_ALLOWED}" in msg


def test_stray_calibration_anchor_industry_is_rejected_with_value_and_allowed_set() -> None:
    data = _base()
    data["calibration_anchor"] = {"industry": "retail_trade", "revenue_tier": "100m_to_1b"}
    with pytest.raises(ValidationError) as exc:
        LibraryEntrySeed.model_validate(data)
    msg = str(exc.value)
    assert "calibration_anchor.industry 'retail_trade' is not an IndustryType value" in msg
    assert f"allowed: {_ALLOWED}" in msg


def test_applicable_industries_none_validates_unchanged() -> None:
    data = _base()
    data["applicable_industries"] = None
    assert LibraryEntrySeed.model_validate(data).applicable_industries is None


def test_applicable_industries_empty_list_validates() -> None:
    data = _base()
    data["applicable_industries"] = []
    assert LibraryEntrySeed.model_validate(data).applicable_industries == []


def test_industry_check_precedes_tier_check_in_the_anchor_validator() -> None:
    data = _base()
    data["calibration_anchor"] = {"industry": "x", "revenue_tier": "bogus"}
    with pytest.raises(ValidationError) as exc:
        LibraryEntrySeed.model_validate(data)
    assert "calibration_anchor.industry 'x' is not an IndustryType value" in str(exc.value)


def test_bad_tier_with_valid_industry_still_hits_the_tier_check() -> None:
    data = _base()
    data["calibration_anchor"] = {"industry": "other", "revenue_tier": "bogus"}
    with pytest.raises(ValidationError) as exc:
        LibraryEntrySeed.model_validate(data)
    assert "calibration_anchor.revenue_tier must be one of" in str(exc.value)


def test_validation_does_not_mutate_its_input() -> None:
    data = _base()
    snapshot = copy.deepcopy(data)
    LibraryEntrySeed.model_validate(data)
    assert data == snapshot


def test_every_seed_entry_validates() -> None:
    n = 0
    for name in _SEED_FILES:
        for row in json.loads((_ROOT / "data" / name).read_text(encoding="utf-8")):
            LibraryEntrySeed.model_validate(row)
            n += 1
    assert n == 102
