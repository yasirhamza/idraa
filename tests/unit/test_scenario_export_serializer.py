from __future__ import annotations

from typing import Any, ClassVar

from idraa.services.scenario_export import (
    CSV_EXPORT_HEADERS,
    scenario_to_flat_row,
    scenario_to_json_obj,
)


class _S:  # minimal Scenario stand-in (duck-typed)
    name = "Exp"
    description = "d,with,commas"
    scenario_type = type("E", (), {"value": "custom"})()
    threat_category = type("E", (), {"value": "ransomware"})()
    threat_actor_type = type("E", (), {"value": "cybercriminals"})()
    attack_vector = "phish"
    asset_class = type("E", (), {"value": "systems"})()
    effect = None
    version = "1.0"
    status = type("E", (), {"value": "active"})()
    threat_event_frequency: ClassVar[dict[str, Any]] = {
        "distribution": "PERT",
        "low": 0.1,
        "mode": 0.5,
        "high": 2,
    }
    vulnerability: ClassVar[dict[str, Any]] = {
        "distribution": "PERT",
        "low": 0.2,
        "mode": 0.35,
        "high": 0.6,
    }
    primary_loss: ClassVar[dict[str, Any]] = {
        "distribution": "PERT",
        "low": 100000,
        "mode": 1000000,
        "high": 15000000,
    }
    secondary_loss = None
    entry_currency = "USD"
    entry_rate = None
    # Issue #475 T12: scenario_to_json_obj iterates attack_mappings.
    attack_mappings: ClassVar[list[Any]] = []


def test_csv_export_headers_match_import_headers() -> None:
    # CRITICAL round-trip invariant: export columns are identical (names + order)
    # to the importer's CSV_HEADERS, or an exported file won't re-import cleanly.
    from idraa.services.scenario_import_parsers import CSV_HEADERS

    assert CSV_EXPORT_HEADERS == CSV_HEADERS


def test_flat_row_matches_header_arity() -> None:
    row = scenario_to_flat_row(_S())
    assert len(row) == len(CSV_EXPORT_HEADERS)


def test_flat_row_blank_secondary_loss() -> None:
    row = dict(zip(CSV_EXPORT_HEADERS, scenario_to_flat_row(_S()), strict=False))
    assert row["sl_low"] == "" and row["sl_mode"] == "" and row["sl_high"] == ""
    assert row["pl_high"] in ("15000000", "15000000.0")


def test_flat_row_collapses_integral_floats() -> None:
    # I3/Meth-I2: 100000.0 (float) must serialize to "100000", not "100000.0",
    # so the CSV path converges with the JSON path on re-import.
    class _F:
        name = "F"
        description = ""
        scenario_type = type("E", (), {"value": "custom"})()
        threat_category = type("E", (), {"value": "ransomware"})()
        threat_actor_type = None
        attack_vector = None
        asset_class = None
        effect = None
        version = "1.0"
        status = type("E", (), {"value": "active"})()
        threat_event_frequency: ClassVar[dict[str, Any]] = {
            "distribution": "PERT",
            "low": 1.0,
            "mode": 2.0,
            "high": 3.0,
        }
        vulnerability: ClassVar[dict[str, Any]] = {
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.2,
            "high": 0.3,
        }
        primary_loss: ClassVar[dict[str, Any]] = {
            "distribution": "PERT",
            "low": 100000.0,
            "mode": 1000000.0,
            "high": 15000000.0,
        }
        secondary_loss = None
        entry_currency = "USD"
        entry_rate = None

    row = dict(zip(CSV_EXPORT_HEADERS, scenario_to_flat_row(_F()), strict=False))
    assert row["pl_low"] == "100000"
    assert row["pl_high"] == "15000000"
    assert row["vuln_mode"] == "0.2"  # true fractional stays fractional


def test_json_obj_excludes_managed_fields() -> None:
    obj = scenario_to_json_obj(_S())
    for managed in ("id", "source", "row_version", "organization_id", "created_at"):
        assert managed not in obj
    assert obj["threat_event_frequency"]["mode"] == 0.5
    assert obj["secondary_loss"] is None


def test_json_obj_collapses_integral_floats() -> None:  # Arch-NTH-1
    # A scenario authored via the wizard/form stores un-normalized floats like
    # high: 2.0. JSON export must collapse integral floats to int so the JSON
    # and CSV export FILES are byte-identical in representation (CSV already
    # collapses via _cell). A true fractional stays fractional.
    class _F:
        name = "F"
        description = None
        scenario_type = type("E", (), {"value": "custom"})()
        threat_category = type("E", (), {"value": "ransomware"})()
        threat_actor_type = None
        attack_vector = None
        asset_class = None
        effect = None
        version = "1.0"
        status = type("E", (), {"value": "active"})()
        threat_event_frequency: ClassVar[dict[str, Any]] = {
            "distribution": "PERT",
            "low": 1.0,
            "mode": 2.0,
            "high": 3.0,
        }
        vulnerability: ClassVar[dict[str, Any]] = {
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.2,
            "high": 0.3,
        }
        primary_loss: ClassVar[dict[str, Any]] = {
            "distribution": "PERT",
            "low": 100000.0,
            "mode": 1000000.0,
            "high": 15000000.0,
        }
        secondary_loss = None
        attack_mappings: ClassVar[list[Any]] = []

    obj = scenario_to_json_obj(_F())
    tef = obj["threat_event_frequency"]
    assert tef["mode"] == 2 and isinstance(tef["mode"], int)
    pl = obj["primary_loss"]
    assert pl["low"] == 100000 and isinstance(pl["low"], int)
    vuln = obj["vulnerability"]
    assert vuln["mode"] == 0.2 and isinstance(vuln["mode"], float)  # fractional stays
    assert tef["distribution"] == "PERT"  # non-numeric keys untouched


def test_empty_set_serializes_to_header_only_and_empty_array() -> None:  # SC-I9
    import csv as _csv
    import io as _io

    from idraa.services.scenario_export import export_csv_response, export_json_response

    csv_resp = export_csv_response([], filename="s.csv")
    # csv_response returns a plain Response with the bytes in .body.
    text = csv_resp.body.decode("utf-8")
    reader = list(_csv.reader(_io.StringIO(text)))
    assert reader == [CSV_EXPORT_HEADERS]  # header row only, no data rows

    json_resp = export_json_response([], filename="s.json")
    import json as _json

    assert _json.loads(json_resp.body) == []


def test_csv_formula_injection_sanitized() -> None:
    # The shared csv_response sanitizer must prefix-escape a formula-leading
    # name. This asserts the sanitizer fires WITHOUT coupling it to the
    # round-trip fixtures (which deliberately avoid leading =/+/-/@).
    from idraa.services.scenario_export import export_csv_response

    class _Inj(_S):
        name = "=cmd|calc"

    resp = export_csv_response([_Inj()], filename="s.csv")
    assert "'=cmd|calc" in resp.body.decode("utf-8")


# --- Epic B (#326): per-node *_dist + lognormal round-trip --------------------


def _scenario(**kwargs: Any) -> Any:
    """Duck-typed Scenario stand-in with overridable distribution dicts and effect.

    Defaults to all-PERT; pass ``primary_loss=...`` etc. to override one node,
    or ``effect=ScenarioEffect.AVAILABILITY`` to set the CIA effect.
    """
    defaults: dict[str, Any] = {
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 100, "mode": 200, "high": 300},
        "secondary_loss": None,
        "effect": None,
    }
    defaults.update(kwargs)

    class _Sc:
        name = "S"
        description = None
        scenario_type = type("E", (), {"value": "custom"})()
        threat_category = type("E", (), {"value": "ransomware"})()
        threat_actor_type = None
        attack_vector = None
        asset_class = None
        effect = defaults["effect"]
        version = "1.0"
        status = type("E", (), {"value": "active"})()
        threat_event_frequency = defaults["threat_event_frequency"]
        vulnerability = defaults["vulnerability"]
        primary_loss = defaults["primary_loss"]
        secondary_loss = defaults["secondary_loss"]
        entry_currency = "USD"
        entry_rate = None
        # Issue #475 T12: scenario_to_json_obj iterates attack_mappings.
        attack_mappings: ClassVar[list[Any]] = []

    return _Sc()


def test_csv_lognormal_roundtrip() -> None:
    import csv
    import io
    import math

    import pytest

    from idraa.services.scenario_import_parsers import parse_csv_flat

    s = _scenario(primary_loss={"distribution": "lognormal", "mean": math.log(1000), "sigma": 1.0})
    row = scenario_to_flat_row(s)
    cells = dict(zip(CSV_EXPORT_HEADERS, row, strict=True))
    # export emits pl_dist=lognormal, pl_low≈p5, pl_high≈p95, pl_mode blank
    assert cells["pl_dist"] == "lognormal" and cells["pl_mode"] == ""
    assert cells["pl_low"] != "" and cells["pl_high"] != ""
    # re-import reproduces native {mean, sigma}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_EXPORT_HEADERS)
    w.writerow(row)
    pairs, errs = parse_csv_flat(buf.getvalue().encode())
    assert errs == []
    assert pairs is not None
    pl = pairs[0][1]["primary_loss"]
    assert pl["distribution"] == "lognormal"
    assert pl["mean"] == pytest.approx(math.log(1000), abs=1e-6)
    assert pl["sigma"] == pytest.approx(1.0, abs=1e-6)


def test_mixed_distribution_roundtrip_both_nodes() -> None:
    import csv
    import io
    import json
    import math

    from idraa.services.scenario_export import scenario_to_json_obj
    from idraa.services.scenario_import_parsers import parse_csv_flat, parse_json_nested

    s = _scenario(
        threat_event_frequency={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        primary_loss={"distribution": "lognormal", "mean": math.log(1000), "sigma": 1.0},
    )
    # CSV: both nodes survive
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_EXPORT_HEADERS)
    w.writerow(scenario_to_flat_row(s))
    pairs, errs = parse_csv_flat(buf.getvalue().encode())
    assert errs == []
    assert pairs is not None
    assert pairs[0][1]["threat_event_frequency"] == {
        "distribution": "PERT",
        "low": 1,
        "mode": 2,
        "high": 3,
    }
    assert pairs[0][1]["primary_loss"]["distribution"] == "lognormal"
    # JSON: both nodes survive verbatim
    pairs2, errs2 = parse_json_nested(json.dumps([scenario_to_json_obj(s)]).encode())
    assert errs2 == []
    assert pairs2 is not None
    assert pairs2[0][1]["threat_event_frequency"]["distribution"].upper() == "PERT"
    assert pairs2[0][1]["primary_loss"]["distribution"] == "lognormal"
    assert pairs2[0][1]["primary_loss"]["mean"] == math.log(1000)


def test_legacy_distribution_column_in_export_matches_tef_kind() -> None:
    # The legacy single `distribution` column is preserved and equals TEF's kind.
    import math

    s = _scenario(
        threat_event_frequency={"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        primary_loss={"distribution": "lognormal", "mean": math.log(1000), "sigma": 1.0},
    )
    cells = dict(zip(CSV_EXPORT_HEADERS, scenario_to_flat_row(s), strict=True))
    assert cells["distribution"] == "PERT"
    assert cells["tef_dist"] == "PERT"


# --- Slice 1: effect (C/I/A) field serialization ----------------------------


def test_export_flat_row_includes_effect() -> None:
    from idraa.models.enums import ScenarioEffect

    s = _scenario(effect=ScenarioEffect.AVAILABILITY)
    row = dict(zip(CSV_EXPORT_HEADERS, scenario_to_flat_row(s), strict=True))
    assert row["effect"] == "availability"


def test_export_flat_row_effect_none_is_empty_string() -> None:
    s = _scenario()  # effect=None by default
    row = dict(zip(CSV_EXPORT_HEADERS, scenario_to_flat_row(s), strict=True))
    assert row["effect"] == ""


def test_export_json_obj_includes_effect_none() -> None:
    obj = scenario_to_json_obj(_S())
    assert "effect" in obj
    assert obj["effect"] is None


def test_export_json_obj_includes_effect_value() -> None:
    from idraa.models.enums import ScenarioEffect

    s = _scenario(effect=ScenarioEffect.CONFIDENTIALITY)
    obj = scenario_to_json_obj(s)
    assert obj["effect"] == "confidentiality"
