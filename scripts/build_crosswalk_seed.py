"""OFFLINE converter: FAIR Institute NIST CSF 1.1 + CIS 8.0 -> FAIR-CAM xlsx into
data/seed_framework_crosswalk.json. Dev-only (openpyxl). Run:
    uv run python scripts/build_crosswalk_seed.py
Re-run when the source .xlsx change; commit the regenerated JSON.

Deterministic per-sheet column maps (verified against the sheets' header bands;
see the plan's "Verified sheet structure"). Each mapped column's header label is
cross-checked against resolve_label as a drift guard, and the mapped-column count
is asserted == 25 per sheet so a layout change FAILS LOUDLY rather than dropping
X-marks (gate Arch-B1/B2, M1/M2/M3/M5)."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl

from idraa.models.enums import FairCamSubFunction as F
from idraa.services.crosswalk_reconciliation import resolve_label

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "seed_framework_crosswalk.json"

# Explicit column->FairCamSubFunction maps (0-indexed). 25 function columns each.
_NIST_COLS: dict[int, F] = {
    7: F.LEC_PREV_AVOIDANCE,
    8: F.LEC_PREV_DETERRENCE,
    9: F.LEC_PREV_RESISTANCE,
    10: F.LEC_DET_VISIBILITY,
    11: F.LEC_DET_MONITORING,
    12: F.LEC_DET_RECOGNITION,
    13: F.LEC_RESP_EVENT_TERMINATION,
    14: F.LEC_RESP_RESILIENCE,
    15: F.LEC_RESP_LOSS_REDUCTION,
    17: F.VMC_PREV_REDUCE_CHANGE_FREQ,
    18: F.VMC_PREV_REDUCE_VARIANCE_PROB,
    19: F.VMC_ID_THREAT_INTELLIGENCE,
    20: F.VMC_ID_CONTROL_MONITORING,
    21: F.VMC_CORR_TREATMENT_SELECTION,
    22: F.VMC_CORR_IMPLEMENTATION,
    24: F.DSC_PREV_DEFINED_EXPECTATIONS,
    25: F.DSC_PREV_COMMUNICATION,
    26: F.DSC_PREV_SA_DATA_ASSET,
    27: F.DSC_PREV_SA_DATA_THREAT,
    28: F.DSC_PREV_SA_DATA_CONTROLS,
    29: F.DSC_PREV_SA_ANALYSIS,
    30: F.DSC_PREV_SA_REPORTING,
    31: F.DSC_PREV_ENSURE_CAPABILITY,
    32: F.DSC_PREV_INCENTIVES,
    33: F.DSC_ID_MISALIGNED,
}
_CIS_COLS: dict[int, F] = {
    12: F.LEC_PREV_AVOIDANCE,
    13: F.LEC_PREV_DETERRENCE,
    14: F.LEC_PREV_RESISTANCE,
    15: F.LEC_DET_VISIBILITY,
    16: F.LEC_DET_MONITORING,
    17: F.LEC_DET_RECOGNITION,
    18: F.LEC_RESP_EVENT_TERMINATION,
    19: F.LEC_RESP_RESILIENCE,
    20: F.LEC_RESP_LOSS_REDUCTION,
    22: F.VMC_PREV_REDUCE_CHANGE_FREQ,
    23: F.VMC_PREV_REDUCE_VARIANCE_PROB,
    24: F.VMC_ID_THREAT_INTELLIGENCE,
    25: F.VMC_ID_CONTROL_MONITORING,
    26: F.VMC_CORR_TREATMENT_SELECTION,
    27: F.VMC_CORR_IMPLEMENTATION,
    29: F.DSC_PREV_DEFINED_EXPECTATIONS,
    30: F.DSC_PREV_COMMUNICATION,
    31: F.DSC_PREV_SA_DATA_ASSET,
    32: F.DSC_PREV_SA_DATA_THREAT,
    33: F.DSC_PREV_SA_DATA_CONTROLS,
    34: F.DSC_PREV_SA_ANALYSIS,
    35: F.DSC_PREV_SA_REPORTING,
    36: F.DSC_PREV_ENSURE_CAPABILITY,
    37: F.DSC_PREV_INCENTIVES,
    38: F.DSC_ID_MISALIGNED,
}

# Per-entry attribution to the FAIR Institute as the REFERENCE SOURCE for the
# factual mapping relationships. No copyright/license stamp: the entries record
# facts (framework code -> FAIR-CAM function), not the source documents' prose or
# expression, so no third-party license governs them. See the NOTICE + the
# _attribution.basis field emitted below.
_CITATION = {
    "source": "FAIR Institute",
    "faircam_version": "1.0",
    "accessed": "2026-06-01",
}

SOURCES = [
    {
        "framework": "nist_csf",
        "framework_version": "1.1",
        "path": ROOT / "docs" / "reference" / "NIST CSF 1.1 to FAIR-CAM 1.0 Mapping_Final.xlsx",
        "sheet": "NIST CSF 1.1 to FAIR-CAM",
        "data_start": 5,
        "code_col": 2,
        "title_col": 2,
        "asset_type_col": None,
        "security_function_col": None,
        "cols": _NIST_COLS,
        "leaf_header_rows": (1, 2, 3, 4),
        "citation": {
            **_CITATION,
            "document": "NIST CSF 1.1 to FAIR-CAM 1.0 Mapping",
            "framework_version": "NIST CSF 1.1",
        },
    },
    {
        "framework": "cis",
        "framework_version": "8.0",
        "path": ROOT / "docs" / "CIS 8.0 to FAIR-CAM Mapping V1.0.xlsx",
        "sheet": "CIS v8.0 to FAIR-CAM",
        "data_start": 9,
        "code_col": 2,
        "title_col": 5,
        "asset_type_col": 3,
        "security_function_col": 4,
        "cols": _CIS_COLS,
        "leaf_header_rows": (5, 6, 7, 8),
        "citation": {
            **_CITATION,
            "document": "CIS 8.0 to FAIR-CAM Mapping V1.0",
            "framework_version": "CIS 8.0",
        },
    },
]


def _cell_value(row, col):
    """row is a tuple of ReadOnlyCell objects; return the cell's .value or None."""
    if col is None or col >= len(row):
        return None
    return row[col].value


def _verify_columns(rows, src):
    """Drift guard: each mapped column's header cell (searched across the sheet's
    leaf-header rows) must resolve via resolve_label to the SAME function we mapped.
    Raises on any mismatch or if the mapped-column count != 25."""
    if len(src["cols"]) != 25:
        raise AssertionError(
            f"{src['framework']}: expected 25 function columns, got {len(src['cols'])}"
        )
    for col, expected in src["cols"].items():
        found = None
        for hr in src["leaf_header_rows"]:
            cell = _cell_value(rows[hr], col) if hr < len(rows) else None
            if cell:
                try:
                    found = resolve_label(str(cell))
                    break
                except KeyError:
                    continue
        if found != expected:
            raise AssertionError(
                f"{src['framework']} col {col}: header resolves to {found}, expected {expected} "
                f"(sheet layout changed — update _NIST_COLS/_CIS_COLS)"
            )


def _code_str(cell) -> str | None:
    """Render a code cell faithfully. NIST codes are strings ('ID.AM-1: ...').
    CIS sub-category codes are stored as floats ('1.1', '3.11') — and a handful
    of trailing-zero codes (3.10, 4.10, 8.10, 13.10, 16.10) collapse to the SAME
    IEEE float as their .1 sibling (3.10 == 3.1), distinguished ONLY by a 2-decimal
    number format. Honour the cell's number_format so '3.10' is not corrupted to
    '3.1' (which would also collide with the real 3.1). String codes pass through."""
    v = cell.value
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, float):
        # 2-decimal number formats (e.g. '0.00', '#,##0.00') mark trailing-zero
        # codes: float 3.1 displayed as '3.10'. Render to exactly 2 decimals so
        # 3.10/4.10/8.10/13.10/16.10 survive (without it they'd corrupt to '3.1'
        # and collide with the real 3.1 sub-category).
        fmt = (cell.number_format or "").replace("#", "").replace(",", "")
        if ".00" in fmt:
            return f"{v:.2f}"
        # General-format float: emit the natural minimal decimal (3.1, 3.11, 10.2).
        return repr(v)
    return str(v).strip() or None


def _split_code_title(code_cell: str, title_cell: str):
    if ":" in code_cell:  # NIST "ID.AM-1: Physical devices ..."
        code, _, rest = code_cell.partition(":")
        return code.strip(), (rest.strip() or title_cell.strip())
    return code_cell.strip(), title_cell.strip()


def _extract(src):
    wb = openpyxl.load_workbook(src["path"], read_only=True, data_only=True)
    ws = wb[src["sheet"]]
    rows = list(ws.iter_rows())  # cell objects: need .number_format for CIS float codes
    _verify_columns(rows, src)
    out = []
    for r in rows[src["data_start"] :]:
        code_cell = r[src["code_col"]] if src["code_col"] < len(r) else None
        code_raw = _code_str(code_cell) if code_cell is not None else None
        if not (code_raw and code_raw.strip()):
            continue
        title_cell = _cell_value(r, src["title_col"])
        code, title = _split_code_title(code_raw, str(title_cell or ""))
        funcs = sorted(
            {
                fn.value
                for col, fn in src["cols"].items()
                if col < len(r)
                and isinstance(r[col].value, str)
                and r[col].value.strip().upper() == "X"
            }
        )
        if not funcs:
            continue  # a row with zero FAIR-CAM mappings contributes nothing
        out.append(
            {
                "framework": src["framework"],
                "framework_version": src["framework_version"],
                "code": code,
                "title": title,
                "asset_type": _cell_value(r, src["asset_type_col"]),
                "security_function": _cell_value(r, src["security_function_col"]),
                "citation": src["citation"],
                "fair_cam_functions": funcs,
            }
        )
    return out


def _preserve_extensions(entries):
    """Carry the RiskFlow extension overlay across a source rebuild (#449).

    ``riskflow_extension_functions`` + ``citation.riskflow_extension`` are
    hand-curated methodology decisions that do NOT exist in the FAIR-Institute
    xlsx — a naive rebuild would silently wipe them. Merge them forward from the
    committed JSON by (framework, code). The base ``fair_cam_functions`` layer is
    never touched: it must stay a faithful transcription of the source X-marks.
    """
    if not OUT.exists():
        return
    prior = {
        (e["framework"], e["code"]): e
        for e in json.loads(OUT.read_text(encoding="utf-8"))["entries"]
    }
    carried = 0
    for entry in entries:
        old = prior.get((entry["framework"], entry["code"]))
        if not old or not old.get("riskflow_extension_functions"):
            continue
        entry["riskflow_extension_functions"] = old["riskflow_extension_functions"]
        # citation is a per-source shared template dict — copy before augmenting.
        entry["citation"] = {
            **entry["citation"],
            "riskflow_extension": old["citation"]["riskflow_extension"],
        }
        carried += 1
    print(f"preserved RiskFlow extension overlay on {carried} entries")


def main():
    entries = []
    for src in SOURCES:
        rows = _extract(src)
        print(
            f"{src['framework']}: {len(rows)} mapped subcategories, "
            f"{sum(len(e['fair_cam_functions']) for e in rows)} links"
        )
        entries.extend(rows)
    _preserve_extensions(entries)
    payload = {
        "_attribution": {
            "source": "FAIR Institute",
            "documents": [
                "NIST CSF 1.1 to FAIR-CAM 1.0 Mapping",
                "CIS 8.0 to FAIR-CAM Mapping V1.0",
            ],
            "basis": (
                "These entries record factual framework-to-FAIR-CAM mapping "
                "RELATIONSHIPS referenced from the FAIR Institute's published NIST "
                "CSF 1.1 and CIS 8.0 -> FAIR-CAM 1.0 crosswalks, and independently "
                "expressed as structured data. Framework subcategory text is "
                "published by NIST (NIST CSF; US-government public domain) and the "
                "Center for Internet Security (CIS Controls). The mapping "
                "relationships are recorded as facts; no copyright or license claim "
                "is made or implied over them. Attribution to the FAIR Institute is "
                "provided as the reference source. RiskFlow-added extension functions "
                "(see per-entry citation.riskflow_extension) are RiskFlow methodology "
                "decisions, not part of the FAIR Institute source documents. See "
                "data/seed_framework_crosswalk.NOTICE.md."
            ),
            "extensions": (
                "5 CIS safeguard entries carry an additional RiskFlow-added FAIR-CAM "
                "function in 'riskflow_extension_functions' (per-entry rationale in that "
                "entry's citation.riskflow_extension). These are RiskFlow methodology "
                "decisions, NOT part of the FAIR Institute source documents; "
                "'fair_cam_functions' remains a faithful transcription of the source "
                "X-marks."
            ),
        },
        "entries": entries,
    }
    # Trailing newline: keeps the generated file idempotent under the repo's
    # end-of-file-fixer pre-commit hook (a re-run won't dirty the working tree).
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(entries)} entries to {OUT}")


if __name__ == "__main__":
    main()
