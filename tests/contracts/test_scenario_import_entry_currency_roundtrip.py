"""Contract: entry_currency + entry_rate columns appear in BOTH import and export headers.

The round-trip contract depends on CSV_HEADERS == CSV_EXPORT_HEADERS (the
exporter does ``CSV_EXPORT_HEADERS = list(CSV_HEADERS)``), so asserting both
is belt-and-suspenders. Also pin the subset direction.
"""

from __future__ import annotations

from idraa.services.scenario_export import CSV_EXPORT_HEADERS
from idraa.services.scenario_import_parsers import CSV_HEADERS


def test_entry_currency_columns_in_both_header_lists() -> None:
    for h in ("entry_currency", "entry_rate"):
        assert h in CSV_HEADERS, f"{h!r} missing from CSV_HEADERS"
        assert h in CSV_EXPORT_HEADERS, f"{h!r} missing from CSV_EXPORT_HEADERS"
    assert set(CSV_HEADERS) <= set(CSV_EXPORT_HEADERS)
