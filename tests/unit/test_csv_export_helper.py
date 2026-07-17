"""F13: csv_response — streaming CSV with formula-injection escape (Sec-1)
and filename sanitiser (Sec-5)."""

from __future__ import annotations

from idraa.utils.csv_export import csv_response


def _body(resp) -> str:
    if hasattr(resp, "body_iterator"):
        return b"".join(resp.body_iterator).decode()
    return resp.body.decode()


def test_csv_response_streams_header_and_rows() -> None:
    resp = csv_response(
        filename="controls.csv",
        header=["name", "domain"],
        rows_iter=iter([("AV/EDR", "V·R"), ("Backups", "R")]),
    )
    body = _body(resp)
    assert body.startswith("name,domain\r\n")
    assert "AV/EDR,V·R\r\n" in body
    assert "Backups,R\r\n" in body
    assert resp.headers["content-disposition"] == 'attachment; filename="controls.csv"'


def test_csv_response_quotes_values_containing_commas() -> None:
    resp = csv_response(
        filename="x.csv",
        header=["a", "b"],
        rows_iter=iter([("v, with comma", 'with "quotes"')]),
    )
    body = _body(resp)
    assert '"v, with comma"' in body
    assert '"with ""quotes"""' in body


def test_csv_response_prefix_escapes_formula_triggers() -> None:
    """Plan-gate Sec-1: cells starting with =/+/-/@/\\t/\\r get single-quote prefix."""
    resp = csv_response(
        filename="x.csv",
        header=["name"],
        rows_iter=iter(
            [
                ('=HYPERLINK("http://evil/x","Click")',),
                ("+ATTACK",),
                ("-EXPLOIT",),
                ("@SHEET",),
                ("\tTAB",),
            ]
        ),
    )
    body = _body(resp)
    assert "'=HYPERLINK" in body
    assert "'+ATTACK" in body
    assert "'-EXPLOIT" in body
    assert "'@SHEET" in body
    assert "'\tTAB" in body


def test_csv_response_sanitizes_unsafe_filename() -> None:
    """Plan-gate Sec-5: filename cannot break out of Content-Disposition header."""
    resp = csv_response(
        filename='evil"; drop="x.csv',
        header=["a"],
        rows_iter=iter([("v",)]),
    )
    dispo = resp.headers["content-disposition"]
    assert '";' not in dispo
    assert "\r" not in dispo and "\n" not in dispo


def test_csv_response_emits_preamble_comment_lines() -> None:
    """Plan-gate M-1: matrix CSV uses preamble to warn about multiplicative composition."""
    resp = csv_response(
        filename="x.csv",
        header=["a"],
        rows_iter=iter([("v",)]),
        preamble=["controls compose multiplicatively — row sums ≠ ALE reduction"],
    )
    body = _body(resp)
    assert body.startswith("# controls compose multiplicatively")
