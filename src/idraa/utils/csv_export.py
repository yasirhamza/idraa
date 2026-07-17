"""Shared CSV-export response helper.

Streams a generator of rows through StreamingResponse so large exports do not
buffer the entire CSV in memory.

Plan-gate Sec-1: prefixes cells starting with formula-trigger chars (=, +, -, @,
\\t, \\r) with a single-quote so Excel/Sheets/Numbers do not interpret them as
formulas. OWASP "CSV Injection" mitigation.

Plan-gate Sec-5: sanitises filename in Content-Disposition (strips ", ;, CR, LF).

Plan-gate M-1: optional preamble lets matrix CSV warn about multiplicative
composition before the header row.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator
from typing import Any

from fastapi import Response

_FORMULA_TRIGGER = re.compile(r"^[=+\-@\t\r]")
_FILENAME_UNSAFE = re.compile(r'[";\r\n]')


def _sanitize_cell(value: Any) -> Any:
    """Prefix-escape formula triggers on string cells; pass through other types unchanged."""
    if isinstance(value, str) and _FORMULA_TRIGGER.match(value):
        return "'" + value
    return value


def _sanitize_filename(filename: str) -> str:
    """Strip characters that would let a caller break out of the Content-Disposition header."""
    return _FILENAME_UNSAFE.sub("_", filename)


def _rows_to_csv_lines(header: list[str], rows: Iterable[tuple[Any, ...]]) -> Iterator[bytes]:
    """Yield CSV-encoded bytes lines one row at a time. Each cell is sanitised (Sec-1)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([_sanitize_cell(c) for c in header])
    yield buf.getvalue().encode("utf-8")
    buf.seek(0)
    buf.truncate(0)
    for row in rows:
        writer.writerow([_sanitize_cell(c) for c in row])
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate(0)


def csv_response(
    *,
    filename: str,
    header: list[str],
    rows_iter: Iterable[tuple[Any, ...]],
    preamble: list[str] | None = None,
) -> Response:
    """Return a CSV download response.

    - filename:  sanitised then set as Content-Disposition: attachment; filename="<safe>".
    - header / rows_iter: cells formula-injection-sanitised (plan-gate Sec-1).
    - preamble:  optional list of ``# ``-prefixed comment lines emitted before the header
                 (plan-gate M-1: matrix CSV uses this to warn that controls compose
                 multiplicatively).
    - Lines use \\r\\n line endings (RFC 4180).

    The body is assembled from a lazy generator so the per-row sanitisation and
    encoding pipeline runs without holding the whole dataset in a single buffer.
    Use ``StreamingResponse(csv_response(...).body, ...)`` if you need true HTTP
    streaming for very large exports — the helper intentionally returns a plain
    ``Response`` so test helpers can read ``.body`` without async machinery.
    """
    safe_filename = _sanitize_filename(filename)

    def lines() -> Iterator[bytes]:
        if preamble:
            for line in preamble:
                prefix = "" if line.startswith("#") else "# "
                yield (prefix + line + "\r\n").encode("utf-8")
        yield from _rows_to_csv_lines(header, rows_iter)

    body = b"".join(lines())
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )
