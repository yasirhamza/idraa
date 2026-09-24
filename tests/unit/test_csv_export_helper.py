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


def test_json_exports_build_disposition_through_the_sanitiser() -> None:
    """Advisory C9: the scenario JSON and library-bundle exports interpolated
    ``filename`` raw into Content-Disposition (safe only because every caller
    passes a literal or a UUID). They now share attachment_disposition."""
    from idraa.services.library_bundle_export import export_bundle_response
    from idraa.services.scenario_export import export_json_response

    evil = "x\"; filename*=UTF-8''evil.html\r\nSet-Cookie: a=b\\.json"
    for resp in (
        export_json_response([], filename=evil),
        export_bundle_response([], filename=evil),
    ):
        dispo = resp.headers["content-disposition"]
        assert dispo.startswith('attachment; filename="') and dispo.endswith('"')
        inner = dispo[len('attachment; filename="') : -1]
        assert not any(c in inner for c in '";\\\r\n')
    assert (
        export_json_response([], filename="scenarios.json").headers["content-disposition"]
        == 'attachment; filename="scenarios.json"'
    )


def test_attachment_disposition_strips_every_control_character() -> None:
    from idraa.utils.csv_export import attachment_disposition

    assert attachment_disposition("a\x00b\x1fc\x7fd.csv") == 'attachment; filename="a_b_c_d.csv"'


def test_every_content_disposition_goes_through_the_one_builder() -> None:
    """Advisory C9 guard: no source file builds a ``Content-Disposition``
    header by hand — every download calls utils.download.attachment_disposition."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "idraa"
    offenders = []
    checked = 0
    for path in src.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""["']Content-Disposition["']\s*:""", line):
                checked += 1
                if (
                    "attachment_disposition(" not in line
                    and "disposition" not in line.split(":", 1)[1]
                ):
                    offenders.append(f"{path.relative_to(src)}:{lineno}")
    assert checked >= 9, "guard found too few Content-Disposition headers; it may be vacuous"
    assert not offenders, f"hand-built Content-Disposition header(s): {offenders}"
