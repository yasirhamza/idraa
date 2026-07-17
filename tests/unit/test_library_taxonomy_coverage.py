"""Taxonomy <-> library content coverage guard.

Ensures that every SCALAR taxonomy value used in scenario library browse/filter
has at least one published seed entry — or is explicitly allowlisted with a
one-line justification. When an enum value has ZERO seed entries, the browse
filter silently hides that option; this guard makes the gap a visible,
tracked decision rather than silent drift.

Design precedent: tests/unit/test_no_raw_markup_outside_macros.py — closed
allowlist with per-entry justification.

Guarded dimensions (fully-covered scalar taxonomies):
  - AssetClass       (JSON field: "asset_class")
  - ThreatActorType  (JSON field: "threat_actor_type")
  - ThreatCategory   (JSON field: "threat_event_type")

NOT hard-guarded (open taxonomies with many legitimately-uncovered values):
  - IndustryType     (applicable_industries — list field, many values uncovered)
  - IndustrySubSector (applicable_sub_sectors — list field, many values uncovered)
  Those two dimensions ARE included in the informational coverage report
  (test_coverage_report_smoke).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory

# ---------------------------------------------------------------------------
# Seed loading
# ---------------------------------------------------------------------------

_SEED_FILES = (
    Path("data/seed_library_entries.json"),
    Path("data/seed_library_entries_extension.json"),
)


def _load_all_entries() -> list[dict]:
    entries: list[dict] = []
    for f in _SEED_FILES:
        entries.extend(json.loads(f.read_text(encoding="utf-8")))
    return entries


def _published(entries: list[dict]) -> list[dict]:
    """Return only entries with status == 'published' (mirrors browse filter)."""
    return [e for e in entries if e.get("status") == "published"]


# ---------------------------------------------------------------------------
# Coverage counters — keyed on JSON field values
# ---------------------------------------------------------------------------


def _build_coverage(entries: list[dict]) -> dict[str, Counter]:
    pub = _published(entries)
    return {
        "asset_class": Counter(e.get("asset_class") for e in pub),
        "threat_actor_type": Counter(e.get("threat_actor_type") for e in pub),
        "threat_event_type": Counter(e.get("threat_event_type") for e in pub),
        "applicable_industries": Counter(
            ind for e in pub for ind in (e.get("applicable_industries") or [])
        ),
        "applicable_sub_sectors": Counter(
            ss for e in pub for ss in (e.get("applicable_sub_sectors") or [])
        ),
    }


# ---------------------------------------------------------------------------
# ALLOWLIST — closed set; every entry requires a one-line justification comment.
#
#   AssetClass.OTHER
#       Catch-all sentinel: scenarios are authored against concrete asset
#       classes, not "other". No seed entry should ever target this value;
#       it exists so the ORM / form can express "unknown / mixed" without
#       being forced into a specific class.
#
#   ThreatCategory.MISCELLANEOUS
#       Open catch-all category retained in the enum for import round-trips
#       and future edge cases. No canonical seed template maps to it because
#       every curated scenario belongs to a named FAIR threat category.
#       Curation decision: allowlisted until a genuine scenario class is
#       identified that doesn't fit any named category.
#
# ---------------------------------------------------------------------------

ALLOWLIST: dict[object, str] = {
    AssetClass.OTHER: (
        "catch-all sentinel; scenarios are authored against concrete classes, not 'other'"
    ),
    ThreatCategory.MISCELLANEOUS: (
        "open catch-all for import round-trips / future edge cases; "
        "every curated seed scenario maps to a named FAIR threat category"
    ),
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_scalar_taxonomy_value_has_coverage_or_is_allowlisted() -> None:
    """Every AssetClass / ThreatActorType / ThreatCategory member must have
    ≥ 1 published seed entry, OR appear in the closed ALLOWLIST with a
    one-line justification.

    Failure means: either author a seed entry for the uncovered value, or
    add it to ALLOWLIST with an explicit justification comment.
    """
    entries = _load_all_entries()
    cov = _build_coverage(entries)

    uncovered: list[str] = []

    # --- AssetClass ---
    for member in AssetClass:
        if member in ALLOWLIST:
            continue
        if cov["asset_class"].get(member.value, 0) == 0:
            uncovered.append(
                f"AssetClass.{member.name} (value={member.value!r}) has 0 published "
                "seed entries — author a seed entry or add to ALLOWLIST with justification"
            )

    # --- ThreatActorType ---
    for member in ThreatActorType:
        if member in ALLOWLIST:
            continue
        if cov["threat_actor_type"].get(member.value, 0) == 0:
            uncovered.append(
                f"ThreatActorType.{member.name} (value={member.value!r}) has 0 published "
                "seed entries — author a seed entry or add to ALLOWLIST with justification"
            )

    # --- ThreatCategory (JSON field: threat_event_type) ---
    for member in ThreatCategory:
        if member in ALLOWLIST:
            continue
        if cov["threat_event_type"].get(member.value, 0) == 0:
            uncovered.append(
                f"ThreatCategory.{member.name} (value={member.value!r}) has 0 published "
                "seed entries — author a seed entry or add to ALLOWLIST with justification"
            )

    assert not uncovered, (
        "The following taxonomy values have zero published seed coverage:\n  "
        + "\n  ".join(uncovered)
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every ALLOWLIST member must actually have zero published seed coverage.

    If a now-covered value is still allowlisted, the allowlist silently hides
    the fact that coverage exists — mirroring the 'allowlist doesn't grow
    silently' discipline from test_no_raw_markup_outside_macros.py.

    Fail here means: a previously-uncovered value now HAS seed entries, so
    remove it from the ALLOWLIST (the guard will then pass automatically).
    """
    entries = _load_all_entries()
    cov = _build_coverage(entries)

    stale: list[str] = []

    for member, _justification in ALLOWLIST.items():
        if isinstance(member, AssetClass):
            count = cov["asset_class"].get(member.value, 0)
            field = "asset_class"
        elif isinstance(member, ThreatActorType):
            count = cov["threat_actor_type"].get(member.value, 0)
            field = "threat_actor_type"
        elif isinstance(member, ThreatCategory):
            count = cov["threat_event_type"].get(member.value, 0)
            field = "threat_event_type"
        else:
            continue

        if count > 0:
            stale.append(
                f"{type(member).__name__}.{member.name} is allowlisted but now has "
                f"{count} published seed entries (field={field!r}). "
                "Remove it from ALLOWLIST — the coverage guard will pass automatically."
            )

    assert not stale, (
        "Stale ALLOWLIST entries found (values now have seed coverage):\n  " + "\n  ".join(stale)
    )


def test_coverage_report_smoke() -> None:
    """Informational: build a per-dimension coverage dict and assert it is
    non-empty. This is NOT a hard gate on open taxonomies (IndustryType /
    IndustrySubSector have many legitimately-uncovered values); it exists so
    curators can run this test with -s and see the full coverage breakdown.

    Coverage numbers are also printed to stdout so they're visible in CI logs.
    """
    entries = _load_all_entries()
    cov = _build_coverage(entries)

    report: dict[str, dict] = {}
    for dim, counter in cov.items():
        report[dim] = dict(sorted(counter.items()))

    # Print informational report (visible with pytest -s or in CI logs)
    print("\n\n=== Taxonomy Coverage Report ===")
    for dim, counts in report.items():
        total = sum(counts.values())
        print(f"\n[{dim}] — {len(counts)} distinct values, {total} total entries")
        for val, cnt in counts.items():
            print(f"  {val}: {cnt}")

    # Hard assertion: the report must be non-empty (smoke check only)
    assert report, "Coverage report is empty — seed files may be missing or unparseable"
    assert sum(len(v) for v in report.values()) > 0, (
        "All coverage counters are empty — no published seed entries found"
    )
