"""Epic C-iii-b curation invariants for the 38 new library archetypes (batches A/B/C).

ORIGIN: this file was authored under the Epic C-iii-b per-batch LOSS model
(batch A = paginated/lognormal with sigma = ln(p95/p50)/Z; batches B/C =
anecdotal/PERT). Epic D-iii-a (#497) recalibrated ALL 85 entries to the
envelope x share lognormal loss model, which SUPERSEDES that per-batch loss
model wholesale. Per CLAUDE.md "audit old tests, don't blindly re-pin", the
obsolete loss-model assertions (loss_tier == anecdotal/paginated, primary/
secondary is PERT, sigma == anchor-derived, secondary sigma inherited) were
DELETED here -- their replacement lives in:
  - tests/integration/test_library_loss_differentiation.py
    (envelope/share reconstruction + PL distinctness)
  - tests/integration/test_seed_library_lognormal.py (share bounds, Sum <= 1)
  - tests/migrations/test_recalibrate_d_iii_a.py (migration lands the values)

What REMAINS here is the still-valid CURATION layer that the recalibration did
not touch: slug presence, no collisions, total count, vuln_posture presence,
campaign-TEF statements, prospective-attribution transparency, the IRIS
Agriculture-row prohibition, and LibraryEntrySeed schema validation over BOTH
seed files (base 31 + extension 54 = 85).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the project src is importable for LibraryEntrySeed.
_PROJ_ROOT = Path(__file__).parent.parent.parent
if str(_PROJ_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT / "src"))

from idraa.services.seed_library_loader import LibraryEntrySeed  # noqa: E402

_EXTENSION_FILE = "data/seed_library_entries_extension.json"
_BASE_SEED_FILE = "data/seed_library_entries.json"
_ANCHORS_FILE = "data/loss_anchor_tables.json"
_ARCHETYPES_FILE = "data/target_archetypes.json"

# ── Batch-A slug set (enumerate from data files at import time) ─────────────


def _batch_a_slugs() -> frozenset[str]:
    """Enumerate batch-A slugs: new archetypes whose anchor_type == 'quantile_pair'."""
    archetypes = json.loads(Path(_ARCHETYPES_FILE).read_text(encoding="utf-8"))
    anchors = json.loads(Path(_ANCHORS_FILE).read_text(encoding="utf-8"))
    anchor_map = {a["archetype"]: a for a in anchors}
    return frozenset(
        a["archetype"]
        for a in archetypes
        if a.get("keep_or_new") == "new"
        and anchor_map.get(a["archetype"], {}).get("anchor_type") == "quantile_pair"
    )


BATCH_A_SLUGS = _batch_a_slugs()


def _load_extension() -> dict[str, dict]:
    """Load extension file into slug → entry dict."""
    entries = json.loads(Path(_EXTENSION_FILE).read_text(encoding="utf-8"))
    return {e["slug"]: e for e in entries}


# ── (a) Every batch-A slug present + lognormal primary (universal post-D-iii-a) ─


@pytest.mark.parametrize("slug", sorted(BATCH_A_SLUGS))
def test_batch_a_slug_present_in_extension_file(slug: str) -> None:
    """(a) Each batch-A slug must exist in seed_library_entries_extension.json."""
    entries = _load_extension()
    assert slug in entries, (
        f"Batch-A slug {slug!r} not found in extension file. Author it per the C-iii-b Task 3 plan."
    )


@pytest.mark.parametrize("slug", sorted(BATCH_A_SLUGS))
def test_batch_a_primary_loss_shape_by_class(slug: str) -> None:
    """(a) Post-Milestone-B (#loss-pert-overhaul): capped entries carry a
    bounded PERT primary_loss (low == mode < high); only the catastrophic
    shortlist keeps the native envelope-x-share lognormal."""
    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS

    entries = _load_extension()
    if slug not in entries:
        pytest.skip(f"{slug!r} not yet authored")
    pl = entries[slug].get("primary_loss", {})
    if slug in CATASTROPHIC_SLUGS:
        assert str(pl.get("distribution", "")).lower() == "lognormal", (
            f"{slug}: catastrophic primary_loss must be lognormal, got {pl.get('distribution')!r}"
        )
    else:
        assert pl.get("distribution") == "PERT", (
            f"{slug}: capped primary_loss must be PERT, got {pl.get('distribution')!r}"
        )
        assert pl["low"] == pl["mode"] < pl["high"], (slug, pl)


# ── (d) every batch-A entry has calibration_anchor.vuln_posture ──────────────


@pytest.mark.parametrize("slug", sorted(BATCH_A_SLUGS))
def test_batch_a_entry_has_vuln_posture(slug: str) -> None:
    """(d) Each batch-A entry must have calibration_anchor.vuln_posture set."""
    entries = _load_extension()
    if slug not in entries:
        pytest.skip(f"{slug!r} not yet authored")
    anchor = entries[slug].get("calibration_anchor", {})
    vp = anchor.get("vuln_posture")
    assert vp and vp.strip(), (
        f"{slug}: calibration_anchor.vuln_posture must be a non-empty string; got {vp!r}"
    )


# ── (e) all entries in BOTH seed files validate through LibraryEntrySeed ───────


def test_all_extension_entries_validate_through_library_entry_seed() -> None:
    """(e) Every entry in seed_library_entries_extension.json passes LibraryEntrySeed.model_validate."""
    entries_raw = json.loads(Path(_EXTENSION_FILE).read_text(encoding="utf-8"))
    failures: list[str] = []
    for entry in entries_raw:
        slug = entry.get("slug", "<unknown>")
        try:
            LibraryEntrySeed.model_validate(entry)
        except Exception as exc:
            failures.append(f"{slug}: {exc}")
    assert not failures, (
        f"{len(failures)} extension entries failed LibraryEntrySeed.model_validate:\n"
        + "\n".join(failures)
    )


def test_all_base_entries_validate_through_library_entry_seed() -> None:
    """(e') Every entry in the BASE seed file also passes LibraryEntrySeed.model_validate.

    D-iii-a PR-gate architect finding: post-recalibration the base-31 entries
    (recon, ransomware-on-historian, denial-of-control, ...) reached the DB with
    NO LibraryEntrySeed schema gate anywhere -- only the extension file was
    covered. This closes that gap so the recalibrated base loss nodes are
    schema-validated (extra='forbid', LossFormEntry shape, share bounds).
    """
    entries_raw = json.loads(Path(_BASE_SEED_FILE).read_text(encoding="utf-8"))
    failures: list[str] = []
    for entry in entries_raw:
        slug = entry.get("slug", "<unknown>")
        try:
            LibraryEntrySeed.model_validate(entry)
        except Exception as exc:
            failures.append(f"{slug}: {exc}")
    assert not failures, (
        f"{len(failures)} base entries failed LibraryEntrySeed.model_validate:\n"
        + "\n".join(failures)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH B — curation invariants (loss-model assertions superseded by D-iii-a)
# ═══════════════════════════════════════════════════════════════════════════════

_BATCH_B_TELECOM_SLUGS: tuple[str, ...] = (
    "telecom-ddos-core-network",
    "telecom-sim-swap-fraud",
    "telecom-bgp-route-hijack",
    "telecom-field-cabinet-tamper",
)

_BATCH_B_FOOD_AG_SLUGS: tuple[str, ...] = (
    "food-cold-chain-ransomware",
    "food-recall-data-tampering",
    "agri-equipment-physical-tamper",
    "agri-coop-bec-fraud",
    "crop-science-ip-exfiltration",
)

_BATCH_B_HOSPITALITY_SLUGS: tuple[str, ...] = ("hospitality-booking-ddos-peak-season",)

BATCH_B_SLUGS: tuple[str, ...] = (
    _BATCH_B_TELECOM_SLUGS + _BATCH_B_FOOD_AG_SLUGS + _BATCH_B_HOSPITALITY_SLUGS
)


@pytest.mark.parametrize("slug", BATCH_B_SLUGS)
def test_batch_b_slug_present_in_extension_file(slug: str) -> None:
    """(B-a) Each batch-B slug must exist in seed_library_entries_extension.json."""
    entries = _load_extension()
    assert slug in entries, (
        f"Batch-B slug {slug!r} not found in extension file. Author it per the C-iii-b Task 4 plan."
    )


@pytest.mark.parametrize("slug", BATCH_B_SLUGS)
def test_batch_b_entry_has_vuln_posture(slug: str) -> None:
    """(B-f) Each batch-B entry must have calibration_anchor.vuln_posture set (non-empty)."""
    entries = _load_extension()
    if slug not in entries:
        pytest.skip(f"{slug!r} not yet authored")
    anchor = entries[slug].get("calibration_anchor", {})
    vp = anchor.get("vuln_posture")
    assert vp and vp.strip(), (
        f"{slug}: calibration_anchor.vuln_posture must be a non-empty string; got {vp!r}"
    )


# ── (B-h) food_agriculture IRIS-Agriculture-row prohibition ───────────────────
#
# None of the five food_agriculture entries may cite the IRIS Agriculture row.
# See the detection helper docstring for the exact prohibited-signature logic.


def _has_iris_agriculture_row_citation(text: str) -> bool:
    """Return True if text cites the IRIS Agriculture row (prohibited for food_ag entries).

    Detects the Agriculture-row citation by its SECTOR LABEL in combination with
    "IRIS" and/or "Figure A3", NOT by dollar amounts alone (which appear
    legitimately as Manufacturing-bounded PERT values). The word "agriculture"
    alone (in slug/description/industry tags) must NOT trigger a false positive.
    """
    t = text.lower()
    if "iris agriculture" in t:
        return True
    if "figure a3" in t and "agriculture" in t and ("agriculture sector" in t or "iris 2025" in t):
        return True
    if "agriculture sector" in t and "iris" in t:
        return True
    return bool("agriculture row" in t and "iris" in t)


@pytest.mark.parametrize("slug", _BATCH_B_FOOD_AG_SLUGS)
def test_food_ag_entries_do_not_cite_iris_agriculture_row(slug: str) -> None:
    """(B-h) food_agriculture entries must not cite the IRIS Agriculture row.

    The IRIS Agriculture row (p50=$2M, p95=$3M, Figure A3 p.35) is prohibited
    as a loss bound for ALL five food_agriculture entries (sigma ~0.247 near-
    point-mass disqualification + NAICS mismatch + circular analyst-vs-analyst
    bounding). This curation guard survives D-iii-a unchanged.
    """
    entries = _load_extension()
    if slug not in entries:
        pytest.skip(f"{slug!r} not yet authored")
    e = entries[slug]
    for citation in e.get("source_citations", []):
        assert not _has_iris_agriculture_row_citation(citation), (
            f"{slug}: source_citation {citation!r} cites the prohibited IRIS Agriculture row."
        )
    loss_anchor = (e.get("calibration_anchor") or {}).get("loss_anchor") or ""
    assert not _has_iris_agriculture_row_citation(loss_anchor), (
        f"{slug}: calibration_anchor.loss_anchor {loss_anchor!r} cites the prohibited "
        "IRIS Agriculture row."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH C — curation invariants (loss-model assertions superseded by D-iii-a)
# ═══════════════════════════════════════════════════════════════════════════════

BATCH_C_SLUGS: tuple[str, ...] = (
    "education-research-ip-exfiltration",
    "logistics-tms-data-tampering",
    "logistics-warehouse-physical-intrusion",
    "competitor-trade-secret-recruit",
    "datacenter-physical-breach",
    "branch-atm-physical-tamper",
    "financial-transaction-tampering",
    "healthcare-record-alteration",
    "retail-ecommerce-checkout-ddos",
    "saas-revenue-outage-sabotage",
    "professional-office-physical-theft",
    "retail-store-employee-fraud",
    "manufacturing-facility-sabotage",
    "financial-call-center-social-eng",
    "education-campus-facility-tamper",
)

# High-frequency-action archetypes (campaign classes) MUST carry the campaign-TEF
# statement in canonical_fair_gap (T4M-B1 review lesson).
_BATCH_C_CAMPAIGN_TEF_SLUGS: frozenset[str] = frozenset(
    {
        "competitor-trade-secret-recruit",
        "financial-call-center-social-eng",
        "retail-store-employee-fraud",
        "financial-transaction-tampering",
        "saas-revenue-outage-sabotage",
    }
)

# Prospective-attribution archetypes MUST carry an attribution-transparency
# sentence in canonical_fair_gap (T4M-I1 review lesson).
_BATCH_C_PROSPECTIVE_ATTRIBUTION_SLUGS: frozenset[str] = frozenset(
    {
        "education-campus-facility-tamper",
        "education-research-ip-exfiltration",
        "manufacturing-facility-sabotage",
    }
)


@pytest.mark.parametrize("slug", BATCH_C_SLUGS)
def test_batch_c_slug_present_in_extension_file(slug: str) -> None:
    """(C-a) Each batch-C slug must exist in seed_library_entries_extension.json."""
    entries = _load_extension()
    assert slug in entries, (
        f"Batch-C slug {slug!r} not found in extension file. Author it per the C-iii-b Task 5 plan."
    )


@pytest.mark.parametrize("slug", BATCH_C_SLUGS)
def test_batch_c_entry_has_vuln_posture(slug: str) -> None:
    """(C-f) Each batch-C entry must have calibration_anchor.vuln_posture set (non-empty)."""
    entries = _load_extension()
    if slug not in entries:
        pytest.skip(f"{slug!r} not yet authored")
    anchor = entries[slug].get("calibration_anchor", {})
    vp = anchor.get("vuln_posture")
    assert vp and vp.strip(), (
        f"{slug}: calibration_anchor.vuln_posture must be a non-empty string; got {vp!r}"
    )


@pytest.mark.parametrize("slug", sorted(_BATCH_C_CAMPAIGN_TEF_SLUGS))
def test_batch_c_campaign_tef_slug_has_tef_statement(slug: str) -> None:
    """(C-h) High-frequency-action archetypes must carry the campaign-TEF statement."""
    entries = _load_extension()
    if slug not in entries:
        pytest.skip(f"{slug!r} not yet authored")
    gap = entries[slug].get("canonical_fair_gap", "")
    assert "tef is campaign-level" in gap.lower(), (
        f"{slug}: canonical_fair_gap must contain 'TEF is campaign-level: ...' "
        f"statement for high-frequency-action archetypes (T4M-B1). Got: {gap[:200]!r}"
    )


@pytest.mark.parametrize("slug", sorted(_BATCH_C_PROSPECTIVE_ATTRIBUTION_SLUGS))
def test_batch_c_prospective_attribution_slug_has_transparency_sentence(slug: str) -> None:
    """(C-i) Prospective attributions must carry the attribution-transparency sentence."""
    entries = _load_extension()
    if slug not in entries:
        pytest.skip(f"{slug!r} not yet authored")
    gap = entries[slug].get("canonical_fair_gap", "")
    transparency_tokens = [
        "attribution is prospective",
        "prospective, not incident-derived",
        "analyst-judged",
        "no confirmed incident for this actor",
        "no public incident",
    ]
    has_transparency = any(tok in gap.lower() for tok in transparency_tokens)
    assert has_transparency, (
        f"{slug}: canonical_fair_gap must carry an attribution-transparency sentence "
        f"(T4M-I1). Expected one of: {transparency_tokens}. Got: {gap[:300]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FULL-SET curation invariants — all 38 new slugs + collision/count checks
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_NEW_SLUGS: tuple[str, ...] = (
    # Batch A (13 quantile_pair-anchored)
    "telecom-subscriber-data-breach",
    "hospitality-pos-card-skimming",
    "hospitality-loyalty-account-takeover",
    "hospitality-guest-data-insider",
    "education-student-records-insider",
    "gov-citizen-portal-ddos",
    "gov-records-tampering",
    "gov-employee-insider-leak",
    "ip-theft-by-competitor",
    "manufacturing-billing-fraud",
    "healthcare-staff-credential-phish",
    "professional-payroll-bec",
    "energy-billing-system-tamper",
    # Batch B (10)
    "telecom-ddos-core-network",
    "telecom-sim-swap-fraud",
    "telecom-bgp-route-hijack",
    "telecom-field-cabinet-tamper",
    "food-cold-chain-ransomware",
    "food-recall-data-tampering",
    "agri-equipment-physical-tamper",
    "agri-coop-bec-fraud",
    "crop-science-ip-exfiltration",
    "hospitality-booking-ddos-peak-season",
    # Batch C (15)
    *BATCH_C_SLUGS,
)

assert len(_ALL_NEW_SLUGS) == 38, (
    f"ALL_NEW_SLUGS must enumerate exactly 38 slugs; got {len(_ALL_NEW_SLUGS)}"
)


def _load_base_slugs() -> frozenset[str]:
    entries = json.loads(Path(_BASE_SEED_FILE).read_text(encoding="utf-8"))
    return frozenset(e["slug"] for e in entries)


BASE_SLUGS = _load_base_slugs()


def test_all_38_new_slugs_present_in_extension_file() -> None:
    """Full-set: all 38 new slugs must be present in seed_library_entries_extension.json."""
    ext = _load_extension()
    missing = [s for s in _ALL_NEW_SLUGS if s not in ext]
    assert not missing, f"{len(missing)} of 38 new slugs not yet in extension file:\n" + "\n".join(
        f"  {s}" for s in missing
    )


def test_no_slug_collisions_between_base_and_extension() -> None:
    """Full-set: no slug from the extension file may collide with the base-seed slugs."""
    ext = _load_extension()
    ext_slugs = frozenset(ext.keys())
    collisions = BASE_SLUGS & ext_slugs
    assert not collisions, "Slug collision(s) between base seed and extension file:\n" + "\n".join(
        f"  {s}" for s in sorted(collisions)
    )


def test_total_unique_slugs_across_both_files() -> None:
    """Full-set: base file (31) + extension file (71, incl. the 8 D-iii-b
    entries #497 + the 9 attack-coverage entries #529) = 102 unique slugs
    total."""
    ext = _load_extension()
    ext_slugs = frozenset(ext.keys())
    total = len(BASE_SLUGS | ext_slugs)
    assert total == 102, (
        f"Expected 102 unique slugs across both seed files; got {total}. "
        f"Base has {len(BASE_SLUGS)}, extension has {len(ext_slugs)}."
    )
