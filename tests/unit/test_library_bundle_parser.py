from __future__ import annotations

import json

from idraa.services.library_bundle_import import MAX_ENTRIES, parse_bundle


def _entry(slug: str = "s1") -> dict:
    return {
        "slug": slug,
        "name": "N",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
        "description": "d" * 25,
        "canonical_fair_gap": "g" * 25,
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
    }


def test_array_of_objects_parses() -> None:
    pairs, errors = parse_bundle(json.dumps([_entry("a"), _entry("b")]).encode())
    assert errors == []
    assert pairs is not None and [p[0] for p in pairs] == [0, 1]
    assert pairs[0][1]["slug"] == "a"


def test_top_level_object_rejected() -> None:
    pairs, errors = parse_bundle(json.dumps(_entry()).encode())
    assert pairs is None and errors and "array" in errors[0]["reason"].lower()


def test_malformed_json_rejected() -> None:
    pairs, errors = parse_bundle(b"[{bad}]")
    assert pairs is None and errors and errors[0]["field"] == "json"


def test_non_object_element_rejected() -> None:
    pairs, errors = parse_bundle(json.dumps([_entry(), 7]).encode())
    assert pairs is None and "object" in errors[0]["reason"].lower()


def test_deeply_nested_json_does_not_crash() -> None:
    pairs, errors = parse_bundle((b"[" * 20000) + (b"]" * 20000))
    assert pairs is None and errors and errors[0]["field"] == "json"


def test_non_utf8_rejected() -> None:
    pairs, errors = parse_bundle(b"\xff\xfe")
    assert pairs is None and errors and errors[0]["field"] == "encoding"


def test_entry_cap_enforced() -> None:
    pairs, errors = parse_bundle(
        json.dumps([_entry(f"s{i}") for i in range(MAX_ENTRIES + 1)]).encode()
    )
    assert pairs is None and errors and errors[0]["field"] == "file"
