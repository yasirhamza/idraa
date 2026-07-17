"""Coverage-matrix balance validation for data/target_archetypes.json (Epic C-ii-a Task 1).

The archetype list is a dev/curation PLANNING artifact (the INPUT to the C-ii-b
sweep, consumed by C-iii curation), NOT app-runtime seed data. These assertions
encode the spec §3 coverage matrix so the list stays balanced as it is curated.
"""

import json
from collections import Counter
from pathlib import Path

_OT_THREAT_TYPES = {"ot_safety_tampering", "ot_availability", "ot_integrity"}
_CORE_SECTORS = {  # §3 coverage matrix
    "manufacturing",
    "energy_utilities",
    "healthcare",
    "financial_services",
    "retail_ecommerce",
    "technology_saas",
    "government_public",
    "education",
    "professional_services",
    "transportation_logistics",
    "telecom",
    "hospitality",
    "food_agriculture",
}
_NON_OT_THREAT_TYPES_TO_FILL = {
    "data_tampering",
    "physical_tampering",
    "denial_of_service",
    "social_engineering",
    "insider_misuse",
}


def _rows():
    return json.loads(Path("data/target_archetypes.json").read_text(encoding="utf-8"))


def test_size_in_target_band():
    assert 70 <= len(_rows()) <= 90


def test_every_core_sector_has_two_entries_spanning_two_threat_types():
    rows = _rows()
    by_sector = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r["threat_type"])
    for sector in _CORE_SECTORS:
        tt = by_sector.get(sector, [])
        assert len(tt) >= 2, f"{sector} has <2 archetypes"
        assert len(set(tt)) >= 2, f"{sector} archetypes don't span >=2 threat types"


def test_each_non_ot_threat_type_filled():
    seen = Counter(r["threat_type"] for r in _rows())
    for tt in _NON_OT_THREAT_TYPES_TO_FILL:
        assert seen[tt] >= 2, f"non-OT threat type {tt} has <2 archetypes"


def test_competitor_actor_present():
    assert any(r["threat_actor"] == "competitors" for r in _rows())


def test_underused_asset_classes_represented():
    seen = {r["asset_class"] for r in _rows()}
    # spec §3 names BOTH business_process_revenue AND business_process_cost (plan-gate I-1)
    for ac in (
        "people",
        "facilities",
        "business_process_revenue",
        "business_process_cost",
        "cash_or_equivalent",
    ):
        assert ac in seen, f"underused asset class {ac} not represented"


def test_ot_share_within_cap():
    rows = _rows()
    ot = [r for r in rows if r.get("is_ot") or r["threat_type"] in _OT_THREAT_TYPES]
    assert len(ot) / len(rows) <= 0.30, f"OT share {len(ot) / len(rows):.0%} exceeds 30%"


def test_threat_type_and_asset_class_are_valid_enums():
    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory

    tc = {t.value for t in ThreatCategory}
    ac = {a.value for a in AssetClass}
    at = {a.value for a in ThreatActorType}
    for r in _rows():
        assert r["threat_type"] in tc and r["asset_class"] in ac and r["threat_actor"] in at
