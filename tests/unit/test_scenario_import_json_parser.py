from __future__ import annotations

import json

from idraa.services.scenario_import_parsers import parse_json_nested


def _obj(**over: object) -> dict[str, object]:
    base = {
        "name": "J1",
        "threat_category": "ransomware",
        "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
        "primary_loss": {"distribution": "PERT", "low": 100000, "mode": 1000000, "high": 15000000},
    }
    base.update(over)
    return base


def test_array_of_objects_passthrough() -> None:
    pairs, errors = parse_json_nested(json.dumps([_obj(), _obj(name="J2")]).encode())
    assert errors == []
    assert pairs is not None and len(pairs) == 2
    assert pairs[0][0] == 0 and pairs[1][0] == 1  # array indices as "line"
    assert pairs[0][1]["name"] == "J1"
    assert pairs[0][1]["threat_event_frequency"]["mode"] == 0.5
    # secondary_loss omitted → normalized to None
    assert pairs[0][1]["secondary_loss"] is None


def test_secondary_loss_present_passthrough() -> None:
    obj = _obj(secondary_loss={"distribution": "PERT", "low": 1, "mode": 2, "high": 3})
    pairs, _errors = parse_json_nested(json.dumps([obj]).encode())
    assert pairs is not None
    assert pairs[0][1]["secondary_loss"] == {"distribution": "PERT", "low": 1, "mode": 2, "high": 3}


def test_top_level_object_rejected() -> None:
    pairs, errors = parse_json_nested(json.dumps(_obj()).encode())
    assert pairs is None
    assert errors and "array" in errors[0]["reason"].lower()


def test_malformed_json_rejected() -> None:
    pairs, errors = parse_json_nested(b"[{not json}]")
    assert pairs is None
    assert errors and errors[0]["column"] == "json"


def test_non_object_array_element_rejected() -> None:
    pairs, errors = parse_json_nested(json.dumps([_obj(), 42]).encode())
    assert pairs is None
    assert errors and "object" in errors[0]["reason"].lower()


def test_row_cap_enforced_for_json() -> None:
    from idraa.services.scenario_import_parsers import MAX_ROWS

    pairs, errors = parse_json_nested(json.dumps([_obj() for _ in range(MAX_ROWS + 1)]).encode())
    assert pairs is None
    assert errors and errors[0]["column"] == "file"


def test_non_utf8_json_rejected() -> None:
    pairs, errors = parse_json_nested(b"\xff\xfe")
    assert pairs is None
    assert errors and errors[0]["column"] == "encoding"


def test_deeply_nested_json_does_not_crash() -> None:
    # B4 (Sec-B2): pathological nesting must become a clean hard-stop, not a 500.
    blob = (b"[" * 20000) + (b"]" * 20000)
    pairs, errors = parse_json_nested(blob)
    assert pairs is None
    assert errors and errors[0]["column"] == "json"


# --- Epic B (#326): lognormal JSON passthrough -------------------------------


def test_json_without_effect_key_still_parses() -> None:
    """An old JSON export with NO effect key must parse successfully with effect → None.

    Mirrors test_csv_without_effect_column_still_parses for the JSON path.
    Relies on ScenarioForm.effect having a None default (Task 2 back-compat).
    """
    obj = _obj()  # _obj() never sets 'effect'; this is the old-schema shape
    assert "effect" not in obj

    pairs, errors = parse_json_nested(json.dumps([obj]).encode())
    assert errors == []
    assert pairs is not None and len(pairs) == 1
    # effect absent from JSON → normalized to None by the parser
    assert pairs[0][1].get("effect") is None


def test_json_lognormal_passthrough() -> None:
    obj = {
        "name": "S",
        "threat_category": "ransomware",
        "threat_event_frequency": {"distribution": "pert", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "lognormal", "mean": 10.0, "sigma": 1.2},
    }
    pairs, errs = parse_json_nested(json.dumps([obj]).encode())
    assert errs == []
    assert pairs is not None
    assert pairs[0][1]["primary_loss"] == {
        "distribution": "lognormal",
        "mean": 10.0,
        "sigma": 1.2,
    }
