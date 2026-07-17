"""Scenario import parsers — CSV (flat columns) + JSON (nested), one output shape.

Both parsers return ``(pairs, hard_stop_errors)``:
- ``pairs is None`` on a hard-stop (encoding / format / header / row-cap);
  ``hard_stop_errors`` is the single-error list explaining the stop.
- otherwise ``pairs`` is ``[(source_line, field_dict), ...]`` where
  ``field_dict`` carries ScenarioForm-shaped keys with the four FAIR
  distributions assembled as nested ``{distribution,low,mode,high}`` dicts,
  and ``hard_stop_errors`` is ``[]``.

Per-row CONTENT validation (enum membership, PERT ordering, dedup) is NOT done
here — it lives in ``scenario_import._validate_rows`` so both the CSV and JSON
paths share one validator. The parsers only do structural/decoding work and
numeric coercion (leaving the raw string in place when coercion fails, so the
downstream Pydantic/FAIR validator produces the row-scoped error).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

# Flat CSV columns. Order-independent (mapped by header name), but this is the
# canonical order for the generated template + export.
CSV_HEADERS: list[str] = [
    "name",
    "description",
    "scenario_type",
    "threat_category",
    "threat_actor_type",
    "attack_vector",
    "asset_class",
    "effect",
    "version",
    "status",
    "distribution",  # legacy single-column kind (back-compat); per-node *_dist override below
    "tef_dist",
    "tef_low",
    "tef_mode",
    "tef_high",
    "vuln_low",
    "vuln_mode",
    "vuln_high",
    "pl_dist",
    "pl_low",
    "pl_mode",
    "pl_high",
    "sl_dist",
    "sl_low",
    "sl_mode",
    "sl_high",
    # Multi-currency P2: pure provenance metadata (exported USD values, NOT
    # entry-currency values). Import carries these as read-only metadata and does
    # NOT call convert_loss_inputs_to_usd — loss cells are already USD.
    "entry_currency",
    "entry_rate",
]

# Columns a CSV may legitimately omit (Epic B back-compat): a legacy pre-Epic-B
# file carries only the single ``distribution`` column and none of the per-node
# ``*_dist`` columns; a fully-stripped file may carry neither. Header validation
# treats these as OPTIONAL so old exports still import.
_OPTIONAL_HEADERS: set[str] = {
    "distribution",
    "tef_dist",
    "pl_dist",
    "sl_dist",
    # P2 multi-currency: old exports omit these; new exports carry them.
    "entry_currency",
    "entry_rate",
    # Slice 1: pre-Slice-1 exports omit the effect column; allow absent.
    "effect",
}

# Matches the overlays importer cap. Scenarios are heavier than controls.
MAX_ROWS: int = 500

# The flat-column groups → nested distribution key.
_DIST_GROUPS: list[tuple[str, str]] = [
    ("threat_event_frequency", "tef"),
    ("vulnerability", "vuln"),
    ("primary_loss", "pl"),
    ("secondary_loss", "sl"),
]


def collapse_num(v: Any) -> Any:
    """Canonical numeric representation shared by CSV + JSON import + export.

    I3/Meth-I2: an integral numeric (``100000`` or ``100000.0``) → ``int``; a
    fractional (``0.35``) → ``float``; non-numeric / None left as-is. Applying
    this on BOTH import paths makes a scenario store identical distribution JSON
    regardless of whether it arrived as CSV or JSON.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    return int(v) if float(v).is_integer() else float(v)


def _num(raw: str) -> Any:
    """Coerce a numeric cell; leave the raw string if it won't parse.

    Leaving the raw string (rather than raising) lets the shared validator emit
    a clean per-row error instead of the parser hard-stopping the whole file on
    one bad cell. Numeric values are normalized via :func:`collapse_num` so CSV
    and JSON imports converge (I3).
    """
    s = (raw or "").strip()
    if s == "":
        return None
    try:
        return collapse_num(float(s))
    except (TypeError, ValueError):
        return raw


def _assemble_distributions(row: dict[str, str]) -> dict[str, Any]:
    """Build the four nested distribution dicts from the flat columns of one row.

    Per-node dispatch (Epic B #326): each of tef/pl/sl reads its own
    ``{prefix}_dist`` column, falling back to the legacy single ``distribution``
    column (then PERT) when blank — so a legacy file with only ``distribution``
    still resolves every node. Vulnerability is ALWAYS PERT (it has no
    ``vuln_dist`` column; lognormal vuln is structurally rejected downstream).

    - lognormal: ``low``/``high`` are the p5/p95 entry pair → converted to the
      native log-space ``{mean, sigma}`` via ``lognormal_from_quantiles``. A
      coercion failure leaves a ``{distribution, low, high}`` marker dict so
      ``_validate_rows`` emits a clean per-row structural error (the marker is
      not a valid lognormal shape, so it never reaches storage).
    - PERT (and unknown kinds): ``{distribution, low, mode, high}`` verbatim,
      letting the §2.5 structural guard reject unknown kinds with a clean error.
    """
    legacy = (row.get("distribution") or "").strip() or "PERT"
    out: dict[str, Any] = {}
    for field, prefix in _DIST_GROUPS:
        if field == "vulnerability":
            kind = "PERT"  # vuln is always PERT (no vuln_dist column)
        else:
            kind = (row.get(f"{prefix}_dist") or "").strip() or legacy
        low = _num(row.get(f"{prefix}_low", ""))
        high = _num(row.get(f"{prefix}_high", ""))
        if kind.lower() == "lognormal":
            if field == "secondary_loss" and low is None and high is None:
                out[field] = None
                continue
            from fair_cam.quantile_pooling import lognormal_from_quantiles

            try:
                out[field] = {
                    "distribution": "lognormal",
                    **lognormal_from_quantiles(float(low), float(high)),
                }
            except (TypeError, ValueError):
                # leave a marker dict so _validate_rows emits a clean per-row
                # error (this shape is not a valid lognormal → action "error").
                out[field] = {"distribution": "lognormal", "low": low, "high": high}
            continue
        mode = _num(row.get(f"{prefix}_mode", ""))
        if field == "secondary_loss" and low is None and mode is None and high is None:
            out[field] = None
            continue
        out[field] = {"distribution": kind, "low": low, "mode": mode, "high": high}
    return out


def _field_dict(row: dict[str, str]) -> dict[str, Any]:
    """Map one flat CSV row dict → the canonical ScenarioForm-shaped field_dict."""
    fd: dict[str, Any] = {
        "name": (row.get("name") or "").strip(),
        "description": (row.get("description") or "").strip() or None,
        "scenario_type": (row.get("scenario_type") or "").strip() or "custom",
        "threat_category": (row.get("threat_category") or "").strip(),
        "threat_actor_type": (row.get("threat_actor_type") or "").strip() or None,
        "attack_vector": (row.get("attack_vector") or "").strip() or None,
        "asset_class": (row.get("asset_class") or "").strip() or None,
        "effect": (row.get("effect") or "").strip() or None,
        "version": (row.get("version") or "").strip() or "1.0",
        "status": (row.get("status") or "").strip() or "active",
    }
    fd.update(_assemble_distributions(row))
    return fd


def parse_csv_flat(
    data: bytes,
) -> tuple[list[tuple[int, dict[str, Any]]] | None, list[dict[str, Any]]]:
    """Parse flat-column CSV bytes → ``(pairs, hard_stop_errors)``."""
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        return None, [
            {"line": 0, "column": "encoding", "reason": f"file is not valid UTF-8: {exc}"}
        ]

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    # Drop blank physical lines but keep a physical-line counter.
    indexed: list[tuple[int, list[str]]] = [
        (i, cells) for i, cells in enumerate(rows, start=1) if any(c.strip() for c in cells)
    ]
    if not indexed:
        return None, [
            {"line": 0, "column": "header", "reason": "CSV is empty or has no header row"}
        ]

    _, header_cells = indexed[0]
    header = [c.strip() for c in header_cells]
    # Lenient header validation (Epic B back-compat): the distribution-type
    # columns (legacy ``distribution`` + per-node ``*_dist``) are OPTIONAL — a
    # legacy file omits the ``*_dist`` columns, a fully-stripped file may omit
    # them all. Every other CSV_HEADERS column is still required, and any column
    # NOT in CSV_HEADERS is still rejected as a genuine mismatch.
    required = set(CSV_HEADERS) - _OPTIONAL_HEADERS
    missing = required - set(header)
    extra = set(header) - set(CSV_HEADERS)
    if missing or extra:
        return None, [
            {
                "line": 1,
                "column": "header",
                "reason": (
                    f"header mismatch — missing: {sorted(missing)}; "
                    f"unexpected: {sorted(extra)}; expected columns {CSV_HEADERS} "
                    f"(optional: {sorted(_OPTIONAL_HEADERS)})"
                ),
            }
        ]

    data_rows = indexed[1:]
    if len(data_rows) > MAX_ROWS:
        return None, [
            {"line": 0, "column": "file", "reason": f"too many rows: maximum {MAX_ROWS} per upload"}
        ]

    pairs: list[tuple[int, dict[str, Any]]] = []
    for physical_line, cells in data_rows:
        padded = list(cells) + [""] * (len(header) - len(cells))
        row = dict(zip(header, padded[: len(header)], strict=True))
        fd = _field_dict(row)
        # P2 multi-currency: carry entry_currency/entry_rate OUTSIDE _field_dict
        # (which is a fixed-key ScenarioForm allowlist). They travel through fd
        # so _validate_rows can pop them before ScenarioForm(**fd) (extra='forbid').
        fd["entry_currency"] = (row.get("entry_currency") or "").strip()
        fd["entry_rate"] = (row.get("entry_rate") or "").strip()
        pairs.append((physical_line, fd))
    return pairs, []


def parse_json_nested(
    data: bytes,
) -> tuple[list[tuple[int, dict[str, Any]]] | None, list[dict[str, Any]]]:
    """Parse a JSON array-of-objects → ``(pairs, hard_stop_errors)``.

    The ``line`` field carries the 0-indexed array position. Per-object content
    validation (enum membership, PERT ordering, dedup, extra-key rejection) is
    deferred to ``_validate_rows`` via ScenarioForm(extra='forbid'); here we
    only enforce array-of-objects structure, UTF-8, and the row cap.
    """
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        return None, [
            {"line": 0, "column": "encoding", "reason": f"file is not valid UTF-8: {exc}"}
        ]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [{"line": exc.lineno, "column": "json", "reason": f"invalid JSON: {exc.msg}"}]
    except (RecursionError, ValueError) as exc:
        # B4 (Sec-B2): deeply-nested input makes json.loads recurse to a
        # RecursionError, which is NOT a JSONDecodeError — without this it
        # escapes to a 500 and (on confirm) leaves the preview row staged.
        return None, [
            {"line": 0, "column": "json", "reason": f"JSON too deeply nested or malformed: {exc}"}
        ]
    if not isinstance(parsed, list):
        return None, [
            {"line": 0, "column": "json", "reason": "expected a JSON array of scenario objects"}
        ]
    if len(parsed) > MAX_ROWS:
        return None, [
            {"line": 0, "column": "file", "reason": f"too many rows: maximum {MAX_ROWS} per upload"}
        ]

    pairs: list[tuple[int, dict[str, Any]]] = []
    for idx, obj in enumerate(parsed):
        if not isinstance(obj, dict):
            return None, [
                {"line": idx, "column": "json", "reason": f"array element {idx} is not an object"}
            ]
        fd = dict(obj)
        fd.setdefault("secondary_loss", None)
        # I3/Meth-I2: normalize distribution numerics so JSON and CSV imports
        # store identical representation (100000.0 → 100000). Only touch the
        # numeric low/mode/high of dict-valued distribution fields; leave
        # everything else (incl. non-dict junk) for _validate_rows to reject.
        for field in ("threat_event_frequency", "vulnerability", "primary_loss", "secondary_loss"):
            d = fd.get(field)
            if isinstance(d, dict):
                fd[field] = {
                    k: (collapse_num(v) if k in ("low", "mode", "high") else v)
                    for k, v in d.items()
                }
        pairs.append((idx, fd))
    return pairs, []


def sniff_format(*, filename: str | None, content_type: str | None, data: bytes) -> str:
    """Return ``"csv"`` or ``"json"``; raise ValueError on a strong conflict.

    Order: (1) filename extension, (2) content-type, (3) content peek (first
    non-whitespace byte ``[`` or ``{`` → json). A strong signal from the
    extension that conflicts with a strong content peek raises rather than
    silently guessing.
    """
    peek = data.lstrip()[:1]
    looks_json = peek in (b"[", b"{")

    ext = None
    if filename:
        lower = filename.lower()
        if lower.endswith(".json"):
            ext = "json"
        elif lower.endswith(".csv"):
            ext = "csv"

    if ext == "csv" and looks_json:
        raise ValueError("format conflict: .csv file but content looks like JSON")
    if ext == "json" and data.strip() and not looks_json:
        raise ValueError("format conflict: .json file but content is not a JSON array/object")
    if ext is not None:
        return ext

    if content_type:
        if "json" in content_type:
            return "json"
        if "csv" in content_type:
            return "csv"

    return "json" if looks_json else "csv"
