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


# --- #27 Task 7: lognormal_mixture import/export -----------------------------

# Worked A/B pair (issue #27 Task 2/6): equal-weight mixture of meanlog
# 8.06/sigma 0.70 and meanlog 15.77/sigma 1.19. Same oracle
# (fair_cam.quantile_pooling.mixture_quantile_lognorm) pinned in
# tests/integration/test_scenario_routes.py's detail-view test, which
# documents p5=1,290.67 / p95=32,444,657.93 (rounded to cents by the DISPLAY
# formatter, format_money_input). scenario_export._cell does not round, so
# this test compares the raw cell value against those anchors with a tight
# absolute tolerance rather than expecting the rounded string.
_MIXTURE_AB = {
    "distribution": "lognormal_mixture",
    "components": [
        {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
        {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
    ],
}


def test_csv_lognormal_mixture_flattens_to_worked_pair_p5_p95() -> None:
    import pytest

    s = _scenario(primary_loss=_MIXTURE_AB)
    row = scenario_to_flat_row(s)
    cells = dict(zip(CSV_EXPORT_HEADERS, row, strict=True))
    # #27 Task 7: mirrors the scalar-lognormal flatten exactly — kind label
    # "lognormal" (not "lognormal_mixture", CSV has no components column),
    # mode blank, low/high = the TRUE mixture's p5/p95.
    assert cells["pl_dist"] == "lognormal"
    assert cells["pl_mode"] == ""
    assert float(cells["pl_low"]) == pytest.approx(1290.67, abs=0.01)
    assert float(cells["pl_high"]) == pytest.approx(32444657.93, abs=0.01)


def test_csv_lognormal_mixture_flatten_reimports_as_approximating_lognormal() -> None:
    """The flattened CSV row is NOT a round-trip of the mixture (documented
    lossy collapse) — but it must re-import cleanly as a scalar lognormal
    anchored at the true mixture's p5/p95, not error or silently corrupt."""
    import csv
    import io

    import pytest

    from idraa.services.scenario_import_parsers import parse_csv_flat

    s = _scenario(primary_loss=_MIXTURE_AB)
    row = scenario_to_flat_row(s)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_EXPORT_HEADERS)
    w.writerow(row)
    pairs, errs = parse_csv_flat(buf.getvalue().encode())
    assert errs == []
    assert pairs is not None
    pl = pairs[0][1]["primary_loss"]
    assert pl["distribution"] == "lognormal"
    assert "components" not in pl
    # lognormal_from_quantiles reconstructs {mean, sigma} anchored at the
    # SAME p5/p95 the mixture flatten emitted — a lossy but valid single
    # lognormal, not the original two-component mixture.
    from fair_cam.quantile_pooling import lognormal_quantiles

    lo, hi = lognormal_quantiles(pl["mean"], pl["sigma"], (0.05, 0.95))
    assert lo == pytest.approx(1290.67, abs=0.01)
    assert hi == pytest.approx(32444657.93, abs=0.01)


def test_json_normalize_dist_passes_mixture_through_verbatim() -> None:
    from idraa.services.scenario_export import _normalize_dist

    out = _normalize_dist(_MIXTURE_AB)
    assert out == _MIXTURE_AB
    # Component-level numerics are untouched (not individually collapsed) —
    # only the top-level low/mode/high trio is ever a collapse target, and a
    # mixture dict has none of those keys.
    assert out["components"][0]["mean"] == 8.06
    assert out["components"][1]["weight"] == 0.5


def test_json_obj_emits_mixture_verbatim_for_all_three_allowed_nodes() -> None:
    # Component-iteration check on the 2-component _MIXTURE_AB fixture:
    # the "components" list must survive scenario_to_json_obj verbatim for
    # each of the three nodes lognormal_mixture is allowed on. (The N>=3
    # adapter-iteration contract required by CLAUDE.md lives in the
    # export->import round-trip test, which uses a 3-component mixture.)
    for field in ("threat_event_frequency", "primary_loss", "secondary_loss"):
        s = _scenario(**{field: _MIXTURE_AB})
        obj = scenario_to_json_obj(s)
        assert obj[field] == _MIXTURE_AB
        assert len(obj[field]["components"]) == 2


def test_legacy_distribution_column_matches_flattened_tef_kind_for_mixture() -> None:
    # #27 Task 7: TEF as a lognormal_mixture must not diverge the legacy
    # `distribution` column from the flattened `tef_dist` cell (both must
    # read "lognormal", never the raw "lognormal_mixture").
    s = _scenario(threat_event_frequency=_MIXTURE_AB)
    cells = dict(zip(CSV_EXPORT_HEADERS, scenario_to_flat_row(s), strict=True))
    assert cells["distribution"] == "lognormal"
    assert cells["tef_dist"] == "lognormal"
    assert cells["distribution"] == cells["tef_dist"]


def test_json_export_metadata_carrying_mixture_emits_verbatim_but_fails_reimport() -> None:
    """#27 Task 7 (Sec-I1/Spec-I1): a wizard-finalized mixture's
    distribution_fit_metadata sidecar exports verbatim (by design — it is
    authored provenance) but the resulting JSON object then fails the
    anti-blob structural gate on re-import, mirroring the PRE-EXISTING
    scalar-lognormal asymmetry (module docstring)."""
    from idraa.services.scenario_import import _structural_dist_problem

    metadata_carrying = {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
            {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
        ],
        "distribution_fit_metadata": {"schema_version": 3, "n_smes": 2},
    }
    s = _scenario(primary_loss=metadata_carrying)
    obj = scenario_to_json_obj(s)
    # Verbatim: the sidecar key survives export unchanged.
    assert obj["primary_loss"] == metadata_carrying

    # Re-import rejection: the exact-key-set gate sees a 3rd key.
    mixture_problem = _structural_dist_problem(
        "primary_loss", obj["primary_loss"], allow_lognormal=True
    )
    assert mixture_problem is not None
    assert "components" in mixture_problem or "exactly keys" in mixture_problem

    # Mirrors the scalar-lognormal case (same gate, same failure mode).
    scalar_metadata_carrying = {
        "distribution": "lognormal",
        "mean": 8.06,
        "sigma": 0.70,
        "distribution_fit_metadata": {"schema_version": 3, "n_smes": 1},
    }
    scalar_problem = _structural_dist_problem(
        "primary_loss", scalar_metadata_carrying, allow_lognormal=True
    )
    assert scalar_problem is not None


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
