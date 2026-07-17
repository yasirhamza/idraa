"""Attack-coverage gap-fill epic (#529) Task 2: ATT&CK technique-mapping rows
for the 9 new library entries (Task 1) plus 3 ICS-twin rows on pre-existing
entries (Sec 6.1 of the design doc).

Mirrors tests/migrations/test_attack_full_mappings_seed.py's schema/catalog/
provenance guards, scoped to the new seed file
data/seed_attack_avgapfill_full.json. This file is data-only -- the migration
that loads it into library_entry_attack_mappings is Task 3 and is NOT tested
here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from idraa.schemas.attack_catalog import EntryAttackMappingSeed

_DATA = Path("data/seed_attack_avgapfill_full.json")
_EXT = Path("data/seed_library_entries_extension.json")
_CATALOG = Path("data/seed_attack_catalog.json")

# The 9 new slugs authored in Task 1 (#529).
_NEW_SLUGS = {
    "edge-ransomware-perimeter-gateway",
    "edge-espionage-nationstate",
    "edge-device-orb-foothold",
    "transient-cyber-asset-ot-intrusion",
    "browser-zeroday-driveby",
    "email-client-zeroclick-espionage",
    "removable-media-airgap-ot",
    "ot-wireless-field-network-compromise",
    "destructive-wiper-nationstate",
}

# The 3 pre-existing entries getting an ICS-twin row, and the technique each
# must carry (design doc Sec 6.1).
_ICS_TWINS = {
    "watering-hole-industry-targeted": "T0817",
    "it-ot-bridge-compromise": "T0865",
    "oem-remote-maintenance-abuse": "T0886",
}

# Mis-cited / unverifiable sources from the #475 gap report (issues #510, #480).
_FORBIDDEN_CITES = ("I-091019-PSA", "15-1433", "AA22-186A", "PREPA", "ICSA-17-181-01")


def _payload() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_DATA.read_text(encoding="utf-8"))
    return result


def _mappings() -> list[dict[str, Any]]:
    return _payload()["mappings"]  # type: ignore[no-any-return]


def _extension_slugs() -> set[str]:
    return {e["slug"] for e in json.loads(_EXT.read_text(encoding="utf-8"))}


def _catalog_techniques() -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (t["domain"], t["technique_id"]): t
        for t in json.loads(_CATALOG.read_text(encoding="utf-8"))["techniques"]
    }


def _strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_strings(v))
    return out


def test_file_has_note_and_nonempty_mappings() -> None:
    payload = _payload()
    assert payload.get("_note")
    assert payload["mappings"], "mappings list must not be empty"


def test_every_row_validates_against_schema() -> None:
    rows = [EntryAttackMappingSeed.model_validate(m) for m in _mappings()]
    assert len(rows) == len(_mappings())


def test_every_new_slug_has_at_least_one_row() -> None:
    mapped_slugs = {m["entry_slug"] for m in _mappings()}
    missing = _NEW_SLUGS - mapped_slugs
    assert not missing, f"new entries with no ATT&CK mapping row: {sorted(missing)}"


def test_ics_twin_rows_present() -> None:
    rows = _mappings()
    for slug, technique_id in _ICS_TWINS.items():
        matches = [m for m in rows if m["entry_slug"] == slug and m["technique_id"] == technique_id]
        assert matches, f"{slug} missing its ICS-twin row for {technique_id}"
        assert all(m["domain"] == "ics" for m in matches), (
            f"{slug}/{technique_id} must be domain=ics"
        )


def test_catalog_integrity_and_domain_match() -> None:
    techniques = _catalog_techniques()
    for m in _mappings():
        key = (m["domain"], m["technique_id"])
        assert key in techniques, f"unknown technique {key} on {m['entry_slug']}"
        assert not techniques[key].get("deprecated"), f"deprecated {key} on {m['entry_slug']}"


def test_provenance_rules_and_no_forbidden_cites() -> None:
    for m in _mappings():
        if m["provenance"] == "cited":
            assert any(c.strip() for c in m["citations"]), (
                f"{m['entry_slug']}/{m['technique_id']}: cited row needs >=1 non-whitespace citation"
            )
        else:
            assert not re.search(r"\bcited\b", m["rationale"], re.IGNORECASE), (
                f"{m['entry_slug']}/{m['technique_id']}: expert-estimate rationale claims 'cited'"
            )
        hits = [t for t in _FORBIDDEN_CITES if any(t in s for s in _strings(m))]
        assert not hits, f"{m['entry_slug']}/{m['technique_id']}: forbidden cite(s) {hits}"


def test_no_iris_envelope_citation_used_as_technique_grounding() -> None:
    """Loss-magnitude/IRIS citations are never valid technique citations
    (schema Sec 7 note) -- cite the incident/technique evidence, not the loss
    envelope."""
    for m in _mappings():
        for c in m["citations"]:
            assert "IRIS 2025 Figure A3" not in c, (
                f"{m['entry_slug']}/{m['technique_id']}: IRIS loss-envelope cite used as technique citation"
            )


def test_new_entry_rows_reference_only_slugs_in_the_extension_seed_library() -> None:
    library = _extension_slugs()
    new_entry_rows = [m for m in _mappings() if m["entry_slug"] in _NEW_SLUGS]
    assert new_entry_rows, "expected at least one row per new entry"
    assert all(m["entry_slug"] in library for m in new_entry_rows)
