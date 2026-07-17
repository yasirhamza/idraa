"""Scenario export — Scenario row → the import shapes (CSV-flat / JSON-nested).

The inverse of scenario_import_parsers. CSV_EXPORT_HEADERS is identical to the
importer's CSV_HEADERS so an exported file re-imports cleanly (round-trip).
Export omits import-managed fields (id, source, row_version, organization_id,
timestamps) so a round-tripped scenario lands as a fresh file_import row.

Known limitation (audit-F2, spec 2026-06-10 §F2.7): ``vuln_framing``
provenance does NOT survive the round-trip — a 'legacy_residual' scenario
re-imports as a fresh row stamped 'inherent' (the column default). Accepted
at current scale: import is admin-only and the format omits the stamp.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fastapi import Response

from idraa.models.scenario import Scenario
from idraa.services.scenario_import_parsers import CSV_HEADERS, collapse_num
from idraa.utils.csv_export import csv_response  # I5: has the CSV-injection sanitizer

# Identical to the importer's columns — the round-trip contract depends on it.
CSV_EXPORT_HEADERS: list[str] = list(CSV_HEADERS)

_DIST_GROUPS: list[tuple[str, str]] = [
    ("threat_event_frequency", "tef"),
    ("vulnerability", "vuln"),
    ("primary_loss", "pl"),
    ("secondary_loss", "sl"),
]


def _enum_value(v: Any) -> str:
    if v is None:
        return ""
    return str(v.value) if hasattr(v, "value") else str(v)


def _cell(v: Any) -> str:
    """Stringify a numeric cell, collapsing whole-valued floats to ints.

    I3/Meth-I2: CSV export must NOT emit ``100000.0`` when JSON export emits
    ``100000`` — otherwise the same scenario stores different JSON depending on
    which format it was re-imported from. A value that is integral (``100000.0``
    or ``100000``) serializes as ``"100000"``; a true fractional (``0.35``)
    stays ``"0.35"``. This makes the CSV and JSON round-trips converge.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _dist_cells(dist: dict[str, Any] | None) -> tuple[str, str, str, str]:
    """Return ``(dist, low, mode, high)`` cells for one distribution.

    Epic B #326: lognormal is exported as its derived p5/p95 entry pair in
    ``low``/``high`` (mode blank) so the round-trip re-imports via
    ``lognormal_from_quantiles`` back to the SAME native ``{mean, sigma}``. PERT
    (and any other kind) emits its raw low/mode/high.
    """
    if not dist:
        return ("", "", "", "")
    kind = str(dist.get("distribution", "PERT"))
    if kind.lower() == "lognormal":
        from fair_cam.quantile_pooling import lognormal_quantiles

        lo, hi = lognormal_quantiles(dist["mean"], dist["sigma"], (0.05, 0.95))
        return ("lognormal", _cell(lo), "", _cell(hi))
    return (kind, _cell(dist.get("low")), _cell(dist.get("mode")), _cell(dist.get("high")))


def scenario_to_flat_row(s: Scenario) -> tuple[Any, ...]:
    """Serialize one scenario to a flat row aligned with CSV_EXPORT_HEADERS."""
    # legacy single `distribution` column = TEF's kind (back-compat anchor)
    dist_label = (s.threat_event_frequency or {}).get("distribution", "PERT")
    cells: dict[str, str] = {
        "name": s.name,
        "description": s.description or "",
        "scenario_type": _enum_value(s.scenario_type),
        "threat_category": _enum_value(s.threat_category),
        "threat_actor_type": _enum_value(s.threat_actor_type),
        "attack_vector": s.attack_vector or "",
        "asset_class": _enum_value(s.asset_class),
        "effect": _enum_value(s.effect),
        "version": s.version,
        "status": _enum_value(s.status),
        "distribution": dist_label,
    }
    for field, prefix in _DIST_GROUPS:
        dist, low, mode, high = _dist_cells(getattr(s, field))
        if field != "vulnerability":  # vuln has no vuln_dist column (always PERT)
            cells[f"{prefix}_dist"] = dist
        cells[f"{prefix}_low"], cells[f"{prefix}_mode"], cells[f"{prefix}_high"] = low, mode, high
    # P2 multi-currency: provenance metadata (exported USD values are already USD;
    # re-import carries these as metadata and does NOT re-convert).
    cells["entry_currency"] = s.entry_currency or "USD"
    cells["entry_rate"] = _cell(s.entry_rate)
    return tuple(cells[h] for h in CSV_EXPORT_HEADERS)


def _normalize_dist(dist: dict[str, Any] | None) -> dict[str, Any] | None:
    """Collapse integral floats in low/mode/high so JSON export matches CSV.

    Arch-NTH-1: a scenario authored via the wizard/form stores un-normalized
    floats (e.g. ``high: 2.0``). CSV export's ``_cell`` already collapses these
    to ``2``; without this, the JSON export FILE would show ``2.0`` while CSV
    shows ``2`` for the same scenario. Applying ``collapse_num`` (the shared
    import/export normalizer) to the numeric distribution keys makes both export
    files byte-identical in representation.
    """
    if not dist:
        return dist
    return {k: (collapse_num(v) if k in ("low", "mode", "high") else v) for k, v in dist.items()}


def scenario_to_json_obj(s: Scenario) -> dict[str, Any]:
    """Serialize one scenario to the nested JSON import shape (authored fields only).

    P2 multi-currency: emit entry_currency/entry_rate as provenance metadata
    (mirroring the CSV export) so JSON round-trips preserve currency context
    and the importer does NOT re-convert already-USD distributions.
    entry_rate is emitted as a plain fixed-point string (or null) to match the
    CSV cell representation — avoids Decimal/float serialization artefacts.
    """
    return {
        "name": s.name,
        "description": s.description,
        "scenario_type": _enum_value(s.scenario_type) or "custom",
        "threat_category": _enum_value(s.threat_category),
        "threat_actor_type": _enum_value(s.threat_actor_type) or None,
        "attack_vector": s.attack_vector,
        "asset_class": _enum_value(s.asset_class) or None,
        "effect": _enum_value(s.effect) or None,
        "version": s.version,
        "status": _enum_value(s.status) or "active",
        "threat_event_frequency": _normalize_dist(s.threat_event_frequency),
        "vulnerability": _normalize_dist(s.vulnerability),
        "primary_loss": _normalize_dist(s.primary_loss),
        "secondary_loss": _normalize_dist(s.secondary_loss),
        # Provenance metadata: mirrors CSV export cells.  Import MUST NOT
        # re-convert loss values (they are already USD); these fields are
        # carry-through metadata only.
        "entry_currency": getattr(s, "entry_currency", None) or "USD",
        "entry_rate": _cell(getattr(s, "entry_rate", None)) or None,
        # Issue #475: technique mappings as natural keys (portable across DBs).
        # `source` is intentionally omitted — import always re-tags as 'user'
        # (file provenance claims are not trusted; I4/Sec-I3 precedent).
        "attack_techniques": [
            {
                "domain": m.technique.domain,
                "technique_id": m.technique.technique_id,
                "rationale": m.rationale,
            }
            for m in s.attack_mappings
        ],
    }


def export_csv_response(scenarios: Iterable[Scenario], *, filename: str) -> Response:
    """Stream scenarios as a CSV download via the shared ``csv_response``.

    I5/Arch-N1: reuse ``utils/csv_export.csv_response`` rather than hand-rolling
    — it provides the CSV-formula-injection sanitizer (`=`/`+`/`-`/`@`-leading
    cells in operator-controlled `name`/`description`), RFC-4180 line endings,
    and filename sanitization, all of which a hand-rolled writer would drop.
    """
    return csv_response(
        filename=filename,
        header=CSV_EXPORT_HEADERS,
        rows_iter=(scenario_to_flat_row(s) for s in scenarios),
    )


def export_json_response(scenarios: Iterable[Scenario], *, filename: str) -> Response:
    """Return scenarios as a JSON array download."""
    payload = json.dumps([scenario_to_json_obj(s) for s in scenarios], indent=2)
    return Response(
        content=payload.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
