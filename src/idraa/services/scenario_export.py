"""Scenario export — Scenario row → the import shapes (CSV-flat / JSON-nested).

The inverse of scenario_import_parsers. CSV_EXPORT_HEADERS is identical to the
importer's CSV_HEADERS so an exported file re-imports cleanly (round-trip).
Export omits import-managed fields (id, source, row_version, organization_id,
timestamps) so a round-tripped scenario lands as a fresh file_import row.

Known limitation (audit-F2, spec 2026-06-10 §F2.7): ``vuln_framing``
provenance does NOT survive the round-trip — a 'legacy_residual' scenario
re-imports as a fresh row stamped 'inherent' (the column default). Accepted
at current scale: import is admin-only and the format omits the stamp.

Known limitation (#27 Task 7, PRE-EXISTING — verified at gate, not
introduced by mixture pooling): a wizard-finalized (multi-SME pooled)
scenario's distribution dict carries a ``distribution_fit_metadata``
sidecar key alongside its minimal shape (e.g.
``{distribution, low, mode, high, distribution_fit_metadata}`` for a
collapsed-to-PERT node, ``{distribution, mean, sigma,
distribution_fit_metadata}`` for a native lognormal node, or
``{distribution, components, distribution_fit_metadata}`` for a
lognormal_mixture node). ``scenario_to_json_obj``/``_normalize_dist`` emit
this sidecar VERBATIM by design (it is authored provenance, not a blob to
strip) — but the exported JSON FILE then fails to re-import:
``scenario_import``'s anti-blob exact-key-set structural gate rejects the
extra key on every kind (PERT / lognormal / lognormal_mixture alike). This
asymmetry predates #27 (scalar PERT/lognormal already exhibited it) and is
NOT propagated as a new guarantee by mixture support — it is simply not
fixed here. CSV export never carries the sidecar at all (the flattened
cells have no metadata column), so only JSON export of a metadata-carrying
scenario is affected.

CSV import/export cannot express ``lognormal_mixture`` (JSON-only — see
``scenario_import.generate_template_csv`` and the
``scenario_import_parsers`` module docstring): ``_dist_cells`` flattens a
mixture to its TRUE p5/p95 quantiles under the ``"lognormal"`` kind label
(mirroring the scalar-lognormal flatten), so re-importing a CSV export of a
mixture reconstructs a single APPROXIMATING lognormal anchored at those two
quantiles — not the original mixture. This is an intentional, documented
lossy collapse, not a round-trip bug.
"""

from __future__ import annotations

import json
import math
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

    #27 Task 7: ``lognormal_mixture`` flattens THE SAME WAY — p5/p95 of the
    TRUE mixture CDF land in low/high, mode blank — via fair_cam's
    deterministic ``mixture_quantile_lognorm`` (bisection, no sampling) on an
    UNTRUNCATED rebuild of the components (``min_support=0.0,
    max_support=inf``), mirroring ``app.py``'s
    ``lognormal_mixture_display_rows`` helper and the storage convention it
    documents (catastrophic pl/sl mixture components are native untruncated
    {mean, sigma} pairs). The emitted kind is ``"lognormal"``, NOT
    ``"lognormal_mixture"`` — CSV has no column for a component list (module
    docstring), so labeling it "lognormal" is what makes the flattened row
    re-importable at all (as a single approximating lognormal anchored at
    the true mixture's p5/p95), rather than hard-failing structural
    validation on a components-less "lognormal_mixture" row.
    """
    if not dist:
        return ("", "", "", "")
    kind = str(dist.get("distribution", "PERT"))
    if kind.lower() == "lognormal":
        from fair_cam.quantile_pooling import lognormal_quantiles

        lo, hi = lognormal_quantiles(dist["mean"], dist["sigma"], (0.05, 0.95))
        return ("lognormal", _cell(lo), "", _cell(hi))
    if kind.lower() == "lognormal_mixture":
        from fair_cam.quantile_pooling import (
            LogNormalTruncFit,
            LognormMixture,
            mixture_quantile_lognorm,
        )

        components_raw = dist["components"]
        fits = tuple(
            LogNormalTruncFit(
                meanlog=c["mean"], sdlog=c["sigma"], min_support=0.0, max_support=math.inf
            )
            for c in components_raw
        )
        weights = tuple(c["weight"] for c in components_raw)
        mix = LognormMixture(components=fits, weights=weights)
        lo = mixture_quantile_lognorm(mix, 0.05)
        hi = mixture_quantile_lognorm(mix, 0.95)
        return ("lognormal", _cell(lo), "", _cell(hi))
    return (kind, _cell(dist.get("low")), _cell(dist.get("mode")), _cell(dist.get("high")))


def scenario_to_flat_row(s: Scenario) -> tuple[Any, ...]:
    """Serialize one scenario to a flat row aligned with CSV_EXPORT_HEADERS."""
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
    }
    # legacy single `distribution` column = TEF's FLATTENED kind (back-compat
    # anchor). #27 Task 7: must be the flattened kind, not the raw stored
    # kind — otherwise a TEF lognormal_mixture would export "distribution"
    # ="lognormal_mixture" while "tef_dist" (via _dist_cells) reads the
    # flattened "lognormal", a self-inconsistent row. Populated from the
    # tef iteration below so the two cells can never diverge.
    dist_label = "PERT"
    for field, prefix in _DIST_GROUPS:
        dist, low, mode, high = _dist_cells(getattr(s, field))
        if field == "threat_event_frequency":
            dist_label = dist or "PERT"
        if field != "vulnerability":  # vuln has no vuln_dist column (always PERT)
            cells[f"{prefix}_dist"] = dist
        cells[f"{prefix}_low"], cells[f"{prefix}_mode"], cells[f"{prefix}_high"] = low, mode, high
    cells["distribution"] = dist_label
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

    #27 Task 7: a ``lognormal_mixture`` dict (top-level keys ``distribution``/
    ``components``) passes through UNCHANGED — none of its keys are
    ``low``/``mode``/``high``, so this is a verbatim (JSON-only) export, unlike
    the CSV path which must flatten it (``_dist_cells``). Component-level
    ``mean``/``sigma``/``weight`` values are NOT individually collapsed here
    (the collapse only ever targeted the top-level low/mode/high trio); a
    scenario authored via JSON import keeps whatever numeric representation
    the source file used. Any ``distribution_fit_metadata`` sidecar key also
    passes through verbatim — see the module docstring's asymmetry note.
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
