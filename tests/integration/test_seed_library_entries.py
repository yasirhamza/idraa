# tests/integration/test_seed_library_entries.py
import json
from pathlib import Path


def _all_extension_entries() -> list[dict]:
    return json.loads(
        (Path("data/seed_library_entries_extension.json")).read_text(encoding="utf-8")
    )


def test_third_party_revenue_asset_class_reclassification() -> None:
    """WS3a + domain-expert correction: logistics stays third-party; telecom reverts.

    'Telecom Core-Network DDoS — Service Disruption and SLA Penalties'
        Domain expert correction: the dominant loss is the CARRIER'S OWN revenue
        (SLA penalties on its own service commitments), not a third party's.
        Carrier revenue cannot be third-party revenue.  Reverted to
        ``business_process_revenue``.
    'Transportation & Logistics Operational Disruption'
        Stays ``business_process_third_party_revenue`` — loss driver IS SLA /
        contractual penalties owed to downstream shippers / logistics partners,
        i.e. a THIRD PARTY's revenue.  Unchanged from WS3a.

    Both are in seed_library_entries_extension.json.
    """
    entries = {e["name"]: e for e in _all_extension_entries()}

    telecom_name = "Telecom Core-Network DDoS — Service Disruption and SLA Penalties"
    logistics_name = "Transportation & Logistics Operational Disruption"

    assert telecom_name in entries, (
        f"Entry '{telecom_name}' not found in seed_library_entries_extension.json"
    )
    assert logistics_name in entries, (
        f"Entry '{logistics_name}' not found in seed_library_entries_extension.json"
    )

    # Telecom: reverted to own-carrier revenue (domain expert correction post-WS3a)
    assert entries[telecom_name]["asset_class"] == "business_process_revenue", (
        f"'{telecom_name}' asset_class must be 'business_process_revenue' (carrier's own "
        f"revenue, not third-party), got {entries[telecom_name]['asset_class']!r}"
    )
    # Logistics: stays third-party (shipper SLA penalties)
    assert entries[logistics_name]["asset_class"] == "business_process_third_party_revenue", (
        f"'{logistics_name}' asset_class must be 'business_process_third_party_revenue', "
        f"got {entries[logistics_name]['asset_class']!r}"
    )


def test_seed_library_entries_count_and_ot_ratio() -> None:
    """Spec §3.1: ~30 entries; ≥8 OT/ICS."""
    seed = json.loads((Path("data/seed_library_entries.json")).read_text(encoding="utf-8"))
    assert 25 <= len(seed) <= 35

    ot_sub_sectors = {
        "oil_and_gas",
        "electric_utility",
        "chemical_manufacturing",
        "water_utility",
        "nuclear",
        "pipeline",
        "process_manufacturing",
    }
    ot_count = sum(
        1
        for e in seed
        if e.get("applicable_sub_sectors")
        and any(s in ot_sub_sectors for s in e["applicable_sub_sectors"])
    )
    assert ot_count >= 8, f"OT-relevant entries {ot_count} < 8"


_WS3B_SLUGS = {
    "tolling-plant-ransomware-customer-liability",
    "pipeline-nomination-scada-curtailment-shipper-penalty",
    "energy-settlement-platform-tampering-offtaker-liability",
}


def test_ws3b_energy_third_party_revenue_entries_present() -> None:
    """WS3b: 3 new energy/process-manufacturing third-party-revenue entries exist.

    Asserts:
      - All 3 WS3b slugs are present in the extension JSON.
      - All 3 use asset_class 'business_process_third_party_revenue'.
      - At least 4 entries total use that asset_class (1 WS3a logistics + 3 WS3b).
        Note: the telecom WS3a entry was reverted to 'business_process_revenue'
        by a domain-expert correction — carrier's own revenue cannot be third-party.
    """
    entries = {e["slug"]: e for e in _all_extension_entries()}
    for slug in _WS3B_SLUGS:
        assert slug in entries, f"WS3b entry '{slug}' not found in extension JSON"
        assert entries[slug]["asset_class"] == "business_process_third_party_revenue", (
            f"'{slug}' asset_class must be 'business_process_third_party_revenue', "
            f"got {entries[slug]['asset_class']!r}"
        )

    total_tpr = sum(
        1
        for e in entries.values()
        if e.get("asset_class") == "business_process_third_party_revenue"
    )
    assert total_tpr >= 4, (
        f"Expected >=4 business_process_third_party_revenue entries (1 WS3a logistics + 3 WS3b), "
        f"got {total_tpr}"
    )


def test_seed_library_canonical_fair_gap_non_empty() -> None:
    """Every entry has a non-empty canonical_fair_gap (the value-prop framing)."""
    seed = json.loads((Path("data/seed_library_entries.json")).read_text(encoding="utf-8"))
    for entry in seed:
        assert entry["canonical_fair_gap"].strip(), f"empty canonical_fair_gap in {entry['slug']}"
        assert len(entry["canonical_fair_gap"]) > 20, f"too short in {entry['slug']}"
