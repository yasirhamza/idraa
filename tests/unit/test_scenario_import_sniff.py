from __future__ import annotations

from idraa.services.scenario_import_parsers import sniff_format


def test_sniff_by_extension_json() -> None:
    assert sniff_format(filename="x.json", content_type=None, data=b"[]") == "json"


def test_sniff_by_extension_csv() -> None:
    assert sniff_format(filename="x.csv", content_type=None, data=b"name\n") == "csv"


def test_sniff_by_content_type_json() -> None:
    assert sniff_format(filename=None, content_type="application/json", data=b"[]") == "json"


def test_sniff_by_content_peek_json_array() -> None:
    assert sniff_format(filename=None, content_type=None, data=b"  [\n {}") == "json"


def test_sniff_by_content_peek_object_is_json() -> None:
    assert sniff_format(filename=None, content_type=None, data=b"{") == "json"


def test_sniff_defaults_to_csv() -> None:
    assert sniff_format(filename=None, content_type=None, data=b"name,desc\n") == "csv"


def test_sniff_conflicting_extension_vs_content_raises() -> None:
    # .csv whose body is clearly a JSON array → explicit error, no silent guess
    import pytest

    with pytest.raises(ValueError, match="conflict"):
        sniff_format(filename="x.csv", content_type=None, data=b"[{}]")
