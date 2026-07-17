"""Pinning + integrity tests for the exemplar curated mappings (issue #475 T6)."""

from __future__ import annotations

import json
from pathlib import Path

import idraa
from idraa.schemas.attack_catalog import EntryAttackMappingSeed

EXPECTED_ENTRY_SLUGS = {
    "ransomware-on-ehr",
    "phishing-ad-compromise-ransomware",
    "unauthorized-plc-modification",
    "denial-of-control",
    "ddos-extortion-financial",
}


def _data_dir() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent / "data"


def _mappings():
    return json.loads((_data_dir() / "seed_attack_exemplar_mappings.json").read_text())["mappings"]


def _catalog_keys() -> set[tuple[str, str]]:
    payload = json.loads((_data_dir() / "seed_attack_catalog.json").read_text())
    return {(t["domain"], t["technique_id"]) for t in payload["techniques"]}


def _library_slugs() -> set[str]:
    slugs: set[str] = set()
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        payload = json.loads((_data_dir() / name).read_text())
        entries = payload["entries"] if isinstance(payload, dict) else payload
        slugs |= {e["slug"] for e in entries}
    return slugs


def test_every_mapping_validates_and_covers_expected_entries():
    mappings = [EntryAttackMappingSeed.model_validate(m) for m in _mappings()]
    assert {m.entry_slug for m in mappings} == EXPECTED_ENTRY_SLUGS
    # ICS discipline: at least one exemplar maps ICS-domain techniques.
    assert any(m.domain == "ics" for m in mappings)
    # Meth-B1 rule 4: the cited pipeline must be proven on real data.
    assert any(m.provenance == "cited" for m in mappings)
    # Adapter-iteration guard needs an entry with ≥3 mappings (Task 7/10 tests).
    from collections import Counter

    counts = Counter(m.entry_slug for m in mappings)
    assert max(counts.values()) >= 3


def test_mappings_reference_seeded_catalog_and_library():
    catalog = _catalog_keys()
    slugs = _library_slugs()
    for m in _mappings():
        assert (m["domain"], m["technique_id"]) in catalog, m
        assert m["entry_slug"] in slugs, m


def test_no_duplicate_claims():
    keys = [(m["entry_slug"], m["domain"], m["technique_id"]) for m in _mappings()]
    assert len(keys) == len(set(keys))
