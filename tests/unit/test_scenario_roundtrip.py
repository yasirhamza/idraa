from __future__ import annotations

import json

from idraa.services.scenario_export import (
    CSV_EXPORT_HEADERS,
    scenario_to_flat_row,
    scenario_to_json_obj,
)
from idraa.services.scenario_import import _validate_rows
from idraa.services.scenario_import_parsers import parse_csv_flat, parse_json_nested


def _make(name: str, with_sl: bool, effect: object = None) -> object:
    sl = {"distribution": "PERT", "low": 1, "mode": 2, "high": 3} if with_sl else None
    return type(
        "S",
        (),
        {
            "name": name,
            "description": "round, trip",
            "scenario_type": type("E", (), {"value": "custom"})(),
            "threat_category": type("E", (), {"value": "ransomware"})(),
            "threat_actor_type": type("E", (), {"value": "cybercriminals"})(),
            "attack_vector": "phish",
            "asset_class": type("E", (), {"value": "systems"})(),
            "effect": effect,
            "version": "1.0",
            "status": type("E", (), {"value": "active"})(),
            "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
            "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
            "primary_loss": {
                "distribution": "PERT",
                "low": 100000,
                "mode": 1000000,
                "high": 15000000,
            },
            "secondary_loss": sl,
            "entry_currency": "USD",
            "entry_rate": None,
            # Issue #475 T12: scenario_to_json_obj now iterates attack_mappings;
            # this fake stand-in carries none for these round-trip fixtures.
            "attack_mappings": [],
        },
    )()


_SCENARIOS = [_make("RT1", True), _make("RT2", False), _make("RT3", True)]


def _norm(d):
    # I3: compare distributions type-exactly via the canonical representation.
    from idraa.services.scenario_import_parsers import collapse_num

    if d is None:
        return None
    return {k: (collapse_num(v) if k in ("low", "mode", "high") else v) for k, v in d.items()}


def _assert_authored_equal(form, src) -> None:
    # I3/SC-I9: compare ALL authored fields, type-exact on the distributions.
    assert form.name == src.name
    assert (form.description or None) == (src.description or None)
    assert form.scenario_type == src.scenario_type.value
    assert form.threat_category == src.threat_category.value
    assert (form.threat_actor_type or None) == (
        src.threat_actor_type.value if src.threat_actor_type else None
    )
    assert (form.attack_vector or None) == (src.attack_vector or None)
    assert (form.asset_class or None) == (src.asset_class.value if src.asset_class else None)
    assert form.version == src.version
    assert form.status == src.status.value
    for field in ("threat_event_frequency", "vulnerability", "primary_loss", "secondary_loss"):
        got = getattr(form, field)
        want = _norm(getattr(src, field))
        assert got == want, f"{field}: {got!r} != {want!r}"
        # type-exact: same low/mode/high python types so stored JSON is identical
        if got and want:
            for k in ("low", "mode", "high"):
                assert type(got[k]) is type(want[k]), f"{field}.{k} type drift"


def test_json_roundtrip() -> None:
    blob = json.dumps([scenario_to_json_obj(s) for s in _SCENARIOS]).encode()
    pairs, errors = parse_json_nested(blob)
    assert errors == []
    preview, verrors, forms, _, _am = _validate_rows(pairs, existing_active_names=set())
    assert verrors == []
    assert [p["action"] for p in preview] == ["create", "create", "create"]
    for form, src in zip([f for f in forms if f], _SCENARIOS, strict=True):
        _assert_authored_equal(form, src)


def test_csv_roundtrip() -> None:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_EXPORT_HEADERS)
    for s in _SCENARIOS:
        w.writerow(scenario_to_flat_row(s))
    pairs, errors = parse_csv_flat(buf.getvalue().encode())
    assert errors == []
    preview, verrors, forms, _, _am = _validate_rows(pairs, existing_active_names=set())
    assert verrors == []
    for form, src in zip([f for f in forms if f], _SCENARIOS, strict=True):
        _assert_authored_equal(form, src)


def test_csv_and_json_export_store_identical_distributions() -> None:
    # I3/Meth-I2: the SAME scenario must round-trip to identical distribution
    # JSON whether via CSV or JSON — the convergence guarantee.
    import csv
    import io

    s = _SCENARIOS[0]
    # JSON path
    jpairs, _ = parse_json_nested(json.dumps([scenario_to_json_obj(s)]).encode())
    _, _, jforms, _, _am = _validate_rows(jpairs, existing_active_names=set())
    # CSV path
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_EXPORT_HEADERS)
    w.writerow(scenario_to_flat_row(s))
    cpairs, _ = parse_csv_flat(buf.getvalue().encode())
    _, _, cforms, _, _am2 = _validate_rows(cpairs, existing_active_names=set())
    assert jforms[0].primary_loss == cforms[0].primary_loss
    for k in ("low", "mode", "high"):
        assert type(jforms[0].primary_loss[k]) is type(cforms[0].primary_loss[k])


# --- Slice 1: effect (C/I/A) round-trip -------------------------------------


def test_effect_roundtrip_csv() -> None:
    """CSV export → import preserves a non-null effect value."""
    import csv
    import io

    from idraa.models.enums import ScenarioEffect

    s = _make("EffectCSVRT", False, effect=ScenarioEffect.CONFIDENTIALITY)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_EXPORT_HEADERS)
    w.writerow(scenario_to_flat_row(s))
    pairs, errors = parse_csv_flat(buf.getvalue().encode())
    assert errors == []
    assert pairs is not None
    _, fd = pairs[0]
    assert fd["effect"] == "confidentiality"
    # validate that the field_dict passes _validate_rows cleanly
    preview, verrors, forms, _, _am = _validate_rows(pairs, existing_active_names=set())
    assert verrors == []
    assert forms[0] is not None
    assert forms[0].effect == "confidentiality"


def test_effect_roundtrip_json() -> None:
    """JSON export → import preserves a non-null effect value."""
    from idraa.models.enums import ScenarioEffect

    s = _make("EffectJSONRT", False, effect=ScenarioEffect.INTEGRITY)
    blob = json.dumps([scenario_to_json_obj(s)]).encode()
    pairs, errors = parse_json_nested(blob)
    assert errors == []
    assert pairs is not None
    _, fd = pairs[0]
    assert fd.get("effect") == "integrity"
    preview, verrors, forms, _, _am = _validate_rows(pairs, existing_active_names=set())
    assert verrors == []
    assert forms[0] is not None
    assert forms[0].effect == "integrity"


def test_effect_none_roundtrip_csv() -> None:
    """CSV export → import: None effect stays None (column present, value blank)."""
    import csv
    import io

    s = _make("EffectNoneCSV", False, effect=None)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_EXPORT_HEADERS)
    w.writerow(scenario_to_flat_row(s))
    pairs, errors = parse_csv_flat(buf.getvalue().encode())
    assert errors == []
    assert pairs is not None
    _, fd = pairs[0]
    assert fd["effect"] is None
