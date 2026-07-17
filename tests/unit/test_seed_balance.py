"""Seed-level coverage-matrix balance assertions (Task 6 Step 2).

Counts ``status == "published"`` entries only across BOTH seed files:
  - ``data/seed_library_entries.json``          (31 base entries)
  - ``data/seed_library_entries_extension.json`` (51 entries = 13 original + 38 new)

Tests are gated to run over the FULL post-T5 82-entry state.  A prove-the-
test-bites fixture demonstrates that the pre-T3 44-entry state (base 31 + the
original 13) FAILS several of the assertions, proving the tests actually
constrain the content additions.

Predicate notes (MB-I1 — exact):
  OT predicate uses ``asset_class in {"ot_systems", "safety_systems"}``.
  Do NOT use ``threat_event_type.startswith("ot_")`` — it undercounts by 3:
    - ransomware-on-historian          (ot_systems, ransomware)
    - it-ot-bridge-compromise          (ot_systems, malware)
    - nation-state-ics-supply-chain    (ot_systems, supply_chain)
  These are OT by asset_class with generic threat types and would be missed.

Sector predicate (SECTOR_PREDICATES dict):
  ``applicable_industries`` is the primary signal.  The telecom sector uses
  ``'telecom' in tags`` as a supplementary signal because 4 of 5 telecom
  entries carry ``applicable_industries=['information']`` (the NAICS
  Information sector) alongside a ``'telecom'`` tag rather than a separate
  ``telecom`` industry value.  The technology_saas predicate excludes entries
  that have ``'telecom'`` in tags so those 4 entries are not double-counted in
  the technology_saas cell.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import idraa

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent


def _load_all_published() -> list[dict]:
    """Load both seed files and return published entries only."""
    base = json.loads((_ROOT / "data" / "seed_library_entries.json").read_text())
    ext = json.loads((_ROOT / "data" / "seed_library_entries_extension.json").read_text())
    return [e for e in (base + ext) if e["status"] == "published"]


def _load_pre_t3_published() -> list[dict]:
    """Return the 44-entry pre-T3 state: base 31 + the original 13 extension
    entries (the first 13 slugs that 0897a0ff350e seeded).  This is the state
    BEFORE the 38 new C-iii-b entries were appended and is used by the
    test-bites parametrize to prove the balance assertions are not trivially
    vacuous."""
    orig_13 = frozenset(
        {
            "ransomware-on-control-layer",
            "process-view-manipulation",
            "field-instrument-spoofing",
            "oem-remote-maintenance-abuse",
            "grid-protective-relay-manipulation",
            "pipeline-scada-integrity",
            "chemical-process-safety-attack",
            "accidental-insider-exposure",
            "web-app-exploitation",
            "third-party-processor-breach",
            "retail-pos-card-skimming",
            "public-sector-targeted-intrusion",
            "logistics-disruption",
        }
    )
    base = json.loads((_ROOT / "data" / "seed_library_entries.json").read_text())
    ext = json.loads((_ROOT / "data" / "seed_library_entries_extension.json").read_text())
    pre_t3 = base + [e for e in ext if e["slug"] in orig_13]
    return [e for e in pre_t3 if e["status"] == "published"]


# ---------------------------------------------------------------------------
# OT predicate (MB-I1 — exact, with 3-entry rationale)
# ---------------------------------------------------------------------------


def _is_ot(entry: dict) -> bool:
    """Return True iff the entry is an OT archetype.

    Uses ``asset_class in {"ot_systems", "safety_systems"}``.

    The ``threat_event_type.startswith("ot_")`` predicate is prohibited because
    it undercounts by 3 entries whose asset_class is ot_systems but whose
    threat_event_type is a generic (non-OT-prefixed) value:
      - ransomware-on-historian          asset_class=ot_systems, tet=ransomware
      - it-ot-bridge-compromise          asset_class=ot_systems, tet=malware
      - nation-state-ics-supply-chain    asset_class=ot_systems, tet=supply_chain
    """
    return entry["asset_class"] in {"ot_systems", "safety_systems"}


# ---------------------------------------------------------------------------
# Sector predicates
# ---------------------------------------------------------------------------

# Each predicate takes a published entry dict and returns True if the entry
# belongs to the named sector.  Sector membership is derived from
# ``applicable_industries`` (primary) and ``tags`` (supplementary for telecom).
#
# TELECOM NOTE: 4 of the 5 telecom entries carry applicable_industries=['information']
# with 'telecom' in tags rather than a dedicated 'telecom' industry value.
# Only telecom-subscriber-data-breach carries 'telecom' in applicable_industries.
# The 'information' ↔ technology_saas predicate EXCLUDES 'telecom'-tagged entries
# so those 4 do not double-count in the technology_saas cell.

SECTOR_PREDICATES: dict[str, Callable[[dict], bool]] = {
    # 13 core sectors from spec §3 (plan T6 Step 2):
    # manufacturing, energy/utilities, healthcare, financial services,
    # retail/e-commerce, technology/SaaS, government/public, education,
    # professional services, transportation/logistics, telecom, hospitality,
    # food/agriculture
    "manufacturing": lambda e: "manufacturing" in e["applicable_industries"],
    "energy_utilities": lambda e: bool(
        {"energy", "utilities", "mining"} & set(e["applicable_industries"])
    ),
    "healthcare": lambda e: bool(
        {"healthcare", "health_care_and_social_assistance"} & set(e["applicable_industries"])
    ),
    "financial_services": lambda e: bool(
        {"finance_and_insurance", "financial"} & set(e["applicable_industries"])
    ),
    "retail_ecommerce": lambda e: bool(
        {"retail", "retail_trade"} & set(e["applicable_industries"])
    ),
    # 'information' is the NAICS sector containing SaaS/tech companies.
    # Entries with 'telecom' in tags are excluded (they are counted in the
    # telecom sector — see note above).
    "technology_saas": lambda e: (
        bool({"technology", "information"} & set(e["applicable_industries"]))
        and "telecom" not in e.get("tags", [])
    ),
    "government_public": lambda e: bool({"government", "public"} & set(e["applicable_industries"])),
    "education": lambda e: bool(
        {"education", "education_services"} & set(e["applicable_industries"])
    ),
    "professional_services": lambda e: bool(
        {"professional", "professional_and_business_services", "real_estate"}
        & set(e["applicable_industries"])
    ),
    "transportation_logistics": lambda e: bool(
        {"transportation", "transportation_and_warehousing"} & set(e["applicable_industries"])
    ),
    # Telecom: primary check is 'telecom' in applicable_industries; supplementary
    # check is 'telecom' in tags (covers the 4 entries that use the 'information'
    # NAICS value instead of a standalone 'telecom' industry value).
    "telecom": lambda e: "telecom" in e["applicable_industries"] or "telecom" in e.get("tags", []),
    "hospitality": lambda e: "hospitality" in e["applicable_industries"],
    "food_agriculture": lambda e: "agriculture" in e["applicable_industries"],
}


# ---------------------------------------------------------------------------
# Test-bites proof — parametrize over the pre-T3 44-entry state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,check,expect_fail",
    [
        # (a) total published >= 70 — the 44-entry state has only 44, which fails
        (
            "total_published_ge_70",
            lambda entries: len(entries) >= 70,
            True,  # 44 < 70 → should fail
        ),
        # (c) telecom sector >= 2 entries spanning >= 2 threat types — 0 entries in 44-state
        (
            "telecom_sector_ge_2",
            lambda entries: len([e for e in entries if SECTOR_PREDICATES["telecom"](e)]) >= 2,
            True,  # 0 telecom entries in pre-T3 → should fail
        ),
        # (c) food_agriculture sector >= 2 entries spanning >= 2 threat types — 0 in 44-state
        (
            "food_agriculture_sector_ge_2",
            lambda entries: (
                len([e for e in entries if SECTOR_PREDICATES["food_agriculture"](e)]) >= 2
            ),
            True,  # 0 food_agriculture entries in pre-T3 → should fail
        ),
        # (d) data_tampering >= 2 — 0 entries in 44-state
        (
            "data_tampering_ge_2",
            lambda entries: (
                sum(1 for e in entries if e["threat_event_type"] == "data_tampering") >= 2
            ),
            True,  # 0 data_tampering in pre-T3 → should fail
        ),
        # (e) people asset class >= 1 — 0 entries in 44-state
        (
            "people_asset_class_ge_1",
            lambda entries: any(e["asset_class"] == "people" for e in entries),
            True,  # 0 people entries in pre-T3 → should fail
        ),
        # (f) competitors actor >= 1 — 0 entries in 44-state
        (
            "competitors_actor_ge_1",
            lambda entries: any(e["threat_actor_type"] == "competitors" for e in entries),
            True,  # 0 competitors in pre-T3 → should fail
        ),
    ],
)
def test_pre_t3_state_fails_balance_checks(label, check, expect_fail):
    """Prove the balance tests bite: the pre-T3 44-entry state (base 31 +
    original 13) FAILS the listed checks.  This parametrize confirms the
    assertions are non-trivial and that the 38 new C-iii-b entries are
    genuinely necessary to satisfy them.
    """
    pre_t3 = _load_pre_t3_published()
    result = check(pre_t3)
    if expect_fail:
        assert not result, (
            f"EXPECTED the pre-T3 44-entry state to FAIL check '{label}', "
            f"but it passed — the test is not biting correctly"
        )


# ---------------------------------------------------------------------------
# Full balance assertions over the post-T5 82-entry set
# ---------------------------------------------------------------------------


def test_total_published_in_range():
    """(a) Total published entries in [70, 110].

    Raised from 90 to 100 by Epic D-iii-b (#497): the 8 new attested vertical
    entries take the post-D-iii-a 85-entry state to 93 published entries,
    which is > 90 (the pre-D-iii-b upper bound) and would otherwise be a hard
    failure. Raised from 100 to 110 by the attack-coverage gap-fill epic
    (#529): the 9 new entries take the state to 102 published entries, which
    is > 100. 110 leaves headroom for the remaining loss_form_targets.json
    gap-report sub-sectors (14 still needs_fresh_research) without requiring
    another bound bump per batch.
    """
    pub = _load_all_published()
    assert 70 <= len(pub) <= 110, f"Expected 70 ≤ total published ≤ 110, got {len(pub)}"


def test_ot_share_le_032():
    """(b) OT share ≤ 0.32.

    OT predicate (MB-I1 — exact): asset_class in {"ot_systems", "safety_systems"}.
    """
    pub = _load_all_published()
    ot_count = sum(1 for e in pub if _is_ot(e))
    ot_share = ot_count / len(pub)
    assert ot_share <= 0.32, (
        f"OT share {ot_share:.3f} exceeds 0.32 cap ({ot_count} OT of {len(pub)} published)"
    )


@pytest.mark.parametrize("sector", list(SECTOR_PREDICATES))
def test_sector_coverage_ge_2_entries_ge_2_threat_types(sector):
    """(c) Every §3 core sector has ≥2 entries spanning ≥2 threat types."""
    pub = _load_all_published()
    pred = SECTOR_PREDICATES[sector]
    entries = [e for e in pub if pred(e)]
    threat_types = {e["threat_event_type"] for e in entries}
    assert len(entries) >= 2, f"Sector '{sector}': expected ≥2 entries, got {len(entries)}"
    assert len(threat_types) >= 2, (
        f"Sector '{sector}': expected ≥2 distinct threat types, "
        f"got {len(threat_types)}: {sorted(threat_types)}"
    )


@pytest.mark.parametrize(
    "tet",
    [
        "data_tampering",
        "physical_tampering",
        "denial_of_service",
        "social_engineering",
        "insider_misuse",
    ],
)
def test_threat_event_type_coverage_ge_2(tet):
    """(d) Named threat event types each have ≥2 published entries."""
    pub = _load_all_published()
    count = sum(1 for e in pub if e["threat_event_type"] == tet)
    assert count >= 2, f"threat_event_type '{tet}': expected ≥2 entries, got {count}"


@pytest.mark.parametrize(
    "asset_class",
    [
        "people",
        "facilities",
        "business_process_revenue",
        "business_process_cost",
        "cash_or_equivalent",
    ],
)
def test_asset_class_coverage_ge_1(asset_class):
    """(e) Underused asset classes each have ≥1 published entry."""
    pub = _load_all_published()
    count = sum(1 for e in pub if e["asset_class"] == asset_class)
    assert count >= 1, f"asset_class '{asset_class}': expected ≥1 entry, got {count}"


def test_competitors_actor_ge_1():
    """(f) threat_actor_type == 'competitors' has ≥1 published entry."""
    pub = _load_all_published()
    count = sum(1 for e in pub if e["threat_actor_type"] == "competitors")
    assert count >= 1, f"threat_actor_type 'competitors': expected ≥1 entry, got {count}"


def test_full_partition_every_published_entry_maps_to_ge1_sector_bucket():
    """(T6M-1e NTH) Every published entry maps to ≥1 sector bucket.

    Asserts that the zero-bucket set is empty.  The "ghost entry" risk: a new
    entry could be authored with an ``applicable_industries`` value that does
    not match any of the 13 SECTOR_PREDICATES, causing it to satisfy NONE of
    the per-sector coverage assertions — a silent coverage hole.  This guard
    closes that risk by failing loudly whenever such an orphan appears.

    The SECTOR_PREDICATES dict above is the authoritative partition covering
    all 13 §3 core sectors.  Any entry that does not match at least one
    predicate either (a) uses an unrecognised industry value that should be
    added to the relevant predicate, or (b) was authored for a sector not yet
    in the coverage matrix and needs an explicit decision before landing.
    """
    pub = _load_all_published()
    ghost_slugs = [
        e["slug"] for e in pub if not any(pred(e) for pred in SECTOR_PREDICATES.values())
    ]
    assert ghost_slugs == [], (
        f"Published entries with NO matching sector bucket (ghost-entry risk): "
        f"{ghost_slugs}. "
        f"Either add the entry's industry value to the matching SECTOR_PREDICATES "
        f"key, or document why it intentionally sits outside all 13 §3 sectors."
    )


# ---------------------------------------------------------------------------
# Epic D-iii-b (#497): 6-vertical new-attested-entry coverage
# ---------------------------------------------------------------------------

_D_IIIB_SLUG_BY_VERTICAL = {
    "healthcare": "physician-practice-clearinghouse-revenue-disruption",
    "government_public": "law-enforcement-records-extortion-breach",
    "hospitality": "casino-ransomware-operational-disruption",
    "telecom": "telecom-lawful-intercept-nationstate-compromise",
    "professional_services": "law-firm-privileged-data-ransomware-extortion",
    "education": "k12-edtech-vendor-breach",
}


def test_six_verticals_have_new_attested_entry():
    """Design §7: each of the 6 under-represented non-OT verticals earns >=1
    new attested D-iii-b entry (#497). Telecom is identified via
    ``'telecom' in tags`` (the entry resolves to the technology_saas envelope
    sector, per the design's sector-resolution rule -- IND2SEC has no
    ``telecom`` key), NOT via the resolved envelope sector or
    SECTOR_PREDICATES['telecom']. OT share is reported informationally
    elsewhere (test_ot_share_le_032) and is not thresholded here."""
    pub = _load_all_published()
    by_slug = {e["slug"]: e for e in pub}

    for vertical, slug in _D_IIIB_SLUG_BY_VERTICAL.items():
        assert slug in by_slug, f"{vertical}: new D-iii-b entry {slug!r} not found or not published"

    telecom_entry = by_slug[_D_IIIB_SLUG_BY_VERTICAL["telecom"]]
    assert "telecom" in (telecom_entry.get("tags") or []), (
        "telecom D-iii-b entry must carry the 'telecom' tag for §7 coverage keying"
    )
