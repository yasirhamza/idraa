from __future__ import annotations

import pytest

from idraa.services.scenario_import import _validate_rows, generate_template_csv
from idraa.services.scenario_import_parsers import CSV_HEADERS, MAX_ROWS, parse_csv_flat


def _csv(rows: list[str], header: str | None = None) -> bytes:
    h = header if header is not None else ",".join(CSV_HEADERS)
    return ("\n".join([h, *rows]) + "\n").encode("utf-8")


def test_generated_template_parses_and_validates_clean() -> None:  # I-1
    """I-1 regression: the downloadable CSV template's example row must align
    with the 28-column CSV_HEADERS (Epic B added tef_dist/pl_dist/sl_dist; Task 3 added effect; P2 added entry_currency/entry_rate) and
    round-trip through parse_csv_flat → _validate_rows with zero errors.

    A misaligned 22-value row (pre-fix) would either shift cell values into the
    wrong columns or hard-stop the parser on a column-count mismatch.
    """
    body = generate_template_csv()

    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None and len(pairs) == 1
    _line, fd = pairs[0]

    # Each per-node *_dist cell lands as PERT and the triplet aligns.
    assert fd["threat_event_frequency"] == {
        "distribution": "PERT",
        "low": 0.1,
        "mode": 0.5,
        "high": 2.0,
    }
    assert fd["vulnerability"] == {
        "distribution": "PERT",
        "low": 0.2,
        "mode": 0.35,
        "high": 0.6,
    }
    assert fd["primary_loss"] == {
        "distribution": "PERT",
        "low": 100000.0,
        "mode": 1000000.0,
        "high": 15000000.0,
    }
    assert fd["secondary_loss"] == {
        "distribution": "PERT",
        "low": 50000.0,
        "mode": 500000.0,
        "high": 5000000.0,
    }

    # Full row-level validation passes (action create, no errors).
    preview, val_errors, forms, _, _am = _validate_rows(pairs, existing_active_names=set())
    assert val_errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None


def test_happy_row_assembles_four_pert_dicts() -> None:
    body = _csv(
        [
            # effect (C/I/A) column added after asset_class — empty here (optional).
            "Phishing AD,desc,custom,ransomware,cybercriminals,phish,systems,,1.0,active,"
            "PERT,,0.1,0.5,2,0.2,0.35,0.6,,100000,1000000,15000000,,50000,500000,5000000"
        ]
    )
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None and len(pairs) == 1
    line, fd = pairs[0]
    assert line == 2  # header is physical line 1
    assert fd["name"] == "Phishing AD"
    assert fd["threat_category"] == "ransomware"
    assert fd["threat_event_frequency"] == {
        "distribution": "PERT",
        "low": 0.1,
        "mode": 0.5,
        "high": 2.0,
    }
    assert fd["primary_loss"] == {
        "distribution": "PERT",
        "low": 100000.0,
        "mode": 1000000.0,
        "high": 15000000.0,
    }
    assert fd["secondary_loss"] == {
        "distribution": "PERT",
        "low": 50000.0,
        "mode": 500000.0,
        "high": 5000000.0,
    }


def test_blank_secondary_loss_columns_yield_none() -> None:
    body = _csv(["NoSL,,custom,malware,,,,,1.0,active,PERT,,1,2,3,0.1,0.2,0.3,,10,20,30,,,,"])
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None
    assert pairs[0][1]["secondary_loss"] is None


def test_distribution_defaults_to_pert_when_blank() -> None:
    # effect column (after asset_class) is blank → optional, defaults to None.
    body = _csv(["Def,,custom,malware,,,,,1.0,active,,,1,2,3,0.1,0.2,0.3,,10,20,30,,,,"])
    pairs, _errors = parse_csv_flat(body)
    assert pairs is not None
    assert pairs[0][1]["threat_event_frequency"]["distribution"] == "PERT"


def test_missing_required_header_is_hard_stop() -> None:
    # drop tef_low by using a truncated header
    bad_header = ",".join([h for h in CSV_HEADERS if h != "tef_low"])
    pairs, errors = parse_csv_flat(_csv(["x"], header=bad_header))
    assert pairs is None
    assert errors and errors[0]["column"] == "header"
    assert "tef_low" in errors[0]["reason"]


def test_unknown_extra_header_is_hard_stop() -> None:
    bad_header = ",".join([*CSV_HEADERS, "surprise_column"])
    pairs, errors = parse_csv_flat(_csv(["x"], header=bad_header))
    assert pairs is None
    assert errors and errors[0]["column"] == "header"


def test_non_numeric_pert_value_is_row_error_not_hardstop() -> None:
    # effect column added after asset_class — blank (optional).
    body = _csv(
        ["Bad,,custom,malware,,,,,1.0,active,PERT,,notanumber,2,3,0.1,0.2,0.3,,10,20,30,,,,"]
    )
    pairs, _errors = parse_csv_flat(body)
    # numeric-coercion failure is reported per-row; pairs returns with the row
    # flagged so _validate_rows surfaces a clean error. Parser emits the row
    # with a sentinel that downstream Pydantic rejects — assert the row is kept
    # but its tef_low is the raw string (validation happens in Task 4).
    assert pairs is not None and len(pairs) == 1
    assert pairs[0][1]["threat_event_frequency"]["low"] == "notanumber"


def test_empty_file_is_hard_stop() -> None:
    pairs, errors = parse_csv_flat(b"")
    assert pairs is None
    assert errors and errors[0]["column"] in {"header", "encoding"}


def test_non_utf8_is_encoding_hard_stop() -> None:
    pairs, errors = parse_csv_flat(b"\xff\xfe\x00bad")
    assert pairs is None
    assert errors and errors[0]["column"] == "encoding"


def test_row_cap_enforced() -> None:
    rows = [
        f"S{i},,custom,malware,,,,1.0,active,PERT,,1,2,3,0.1,0.2,0.3,,10,20,30,,,,"
        for i in range(MAX_ROWS + 1)
    ]
    pairs, errors = parse_csv_flat(_csv(rows))
    assert pairs is None
    assert errors and errors[0]["column"] == "file"


def test_header_order_independent() -> None:
    # reverse the header order; values must still map by name
    header = ",".join(reversed(CSV_HEADERS))
    # build a row matching the reversed order
    fields = {
        "name": "Rev",
        "description": "",
        "scenario_type": "custom",
        "threat_category": "malware",
        "threat_actor_type": "",
        "attack_vector": "",
        "asset_class": "",
        "effect": "",  # Slice 1: optional C/I/A column
        "version": "1.0",
        "status": "active",
        "distribution": "PERT",
        "tef_dist": "",
        "tef_low": "1",
        "tef_mode": "2",
        "tef_high": "3",
        "vuln_low": "0.1",
        "vuln_mode": "0.2",
        "vuln_high": "0.3",
        "pl_dist": "",
        "pl_low": "10",
        "pl_mode": "20",
        "pl_high": "30",
        "sl_dist": "",
        "sl_low": "",
        "sl_mode": "",
        "sl_high": "",
        # P2 multi-currency: optional provenance columns (added to CSV_HEADERS).
        "entry_currency": "",
        "entry_rate": "",
    }
    row = ",".join(fields[h] for h in reversed(CSV_HEADERS))
    pairs, errors = parse_csv_flat((header + "\n" + row + "\n").encode())
    assert errors == []
    assert pairs is not None
    assert pairs[0][1]["name"] == "Rev"
    assert pairs[0][1]["threat_event_frequency"]["low"] == 1.0


def test_crlf_line_endings_parse() -> None:  # SC-I7
    body = _csv(["CRLF,,custom,malware,,,,1.0,active,PERT,1,2,3,0.1,0.2,0.3,10,20,30,,,"]).replace(
        b"\n", b"\r\n"
    )
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None and pairs[0][1]["name"] == "CRLF"


def test_utf8_bom_stripped() -> None:  # SC-I7 (Excel exports prepend a BOM)
    body = b"\xef\xbb\xbf" + _csv(
        ["BOM,,custom,malware,,,,1.0,active,PERT,1,2,3,0.1,0.2,0.3,10,20,30,,,"]
    )
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None and pairs[0][1]["name"] == "BOM"


def test_header_only_file_yields_zero_rows_no_error() -> None:  # SC-I7
    pairs, errors = parse_csv_flat((",".join(CSV_HEADERS) + "\n").encode())
    assert errors == []
    assert pairs == []


# --- Epic B (#326): per-node *_dist + lognormal + legacy back-compat ---------


def test_old_single_distribution_column_still_imports() -> None:
    # legacy file = the PRE-Epic-B CSV_HEADERS verbatim (no *_dist columns).
    import csv
    import io

    legacy_header = [
        "name",
        "description",
        "scenario_type",
        "threat_category",
        "threat_actor_type",
        "attack_vector",
        "asset_class",
        "version",
        "status",
        "distribution",
        "tef_low",
        "tef_mode",
        "tef_high",
        "vuln_low",
        "vuln_mode",
        "vuln_high",
        "pl_low",
        "pl_mode",
        "pl_high",
        "sl_low",
        "sl_mode",
        "sl_high",
    ]
    row = [
        "S",
        "",
        "custom",
        "ransomware",
        "",
        "",
        "",
        "1.0",
        "active",
        "PERT",
        "1",
        "2",
        "3",
        "0.1",
        "0.2",
        "0.3",
        "100",
        "200",
        "300",
        "",
        "",
        "",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(legacy_header)
    w.writerow(row)
    pairs, errs = parse_csv_flat(buf.getvalue().encode())
    assert errs == []
    assert pairs is not None
    assert pairs[0][1]["primary_loss"] == {
        "distribution": "PERT",
        "low": 100,
        "mode": 200,
        "high": 300,
    }


def test_per_node_dist_lognormal_assembles_native_mean_sigma() -> None:
    import math

    body = _csv(
        [
            # effect column (after asset_class) is blank — optional.
            "L1,,custom,ransomware,,,,,1.0,active,,"
            "PERT,1,2,3,"  # tef_dist, tef_low/mode/high
            "0.1,0.2,0.3,"  # vuln_low/mode/high
            "lognormal,100,,10000,"  # pl_dist, pl_low, pl_mode(blank), pl_high
            ",,,"  # sl_dist, sl_low, sl_mode/high
        ],
        header=",".join(CSV_HEADERS),
    )
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None
    pl = pairs[0][1]["primary_loss"]
    assert pl["distribution"] == "lognormal"
    # mean of a lognormal whose p5=100, p95=10000 is log of geometric mean = log(1000)
    assert pl["mean"] == pytest.approx(math.log(1000), abs=1e-6)
    assert "low" not in pl and "high" not in pl


def test_vuln_dist_column_absent_vuln_always_pert() -> None:
    # vuln has no vuln_dist column; even with a legacy distribution=lognormal,
    # vulnerability stays PERT.
    body = _csv(
        [
            "V1,,custom,ransomware,,,,1.0,active,lognormal,"
            "PERT,1,2,3,"
            "0.1,0.2,0.3,"
            "PERT,100,200,300,"
            ",,,"
        ],
        header=",".join(CSV_HEADERS),
    )
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None
    assert pairs[0][1]["vulnerability"]["distribution"] == "PERT"


# --- Slice 1: effect (C/I/A) field parsing -----------------------------------


def test_field_dict_reads_effect() -> None:
    """_field_dict maps the effect column to the effect key."""
    from idraa.services.scenario_import_parsers import _field_dict

    assert _field_dict({"effect": "availability"})["effect"] == "availability"
    assert _field_dict({"effect": ""})["effect"] is None  # blank → None
    assert _field_dict({})["effect"] is None  # absent → None


def test_csv_with_effect_column_parses_cleanly() -> None:
    """A CSV carrying an 'effect' column is accepted; value flows through."""
    body = _csv(
        [
            "Phish AD,desc,custom,ransomware,cybercriminals,phish,systems,availability,"
            "1.0,active,PERT,"
            "PERT,0.1,0.5,2,"
            "0.2,0.35,0.6,"
            "PERT,100000,1000000,15000000,"
            "PERT,50000,500000,5000000"
        ],
        header=",".join(CSV_HEADERS),
    )
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None
    assert pairs[0][1]["effect"] == "availability"


def test_csv_without_effect_column_still_parses() -> None:
    """A CSV WITHOUT the effect column still imports fine (optional back-compat)."""
    headers_no_effect = [h for h in CSV_HEADERS if h != "effect"]
    body = _csv(
        [
            "Old scenario,desc,custom,ransomware,,phish,systems,"
            "1.0,active,PERT,"
            "PERT,0.1,0.5,2,"
            "0.2,0.35,0.6,"
            "PERT,100000,1000000,15000000,"
            ",,,"
        ],
        header=",".join(headers_no_effect),
    )
    pairs, errors = parse_csv_flat(body)
    assert errors == []
    assert pairs is not None
