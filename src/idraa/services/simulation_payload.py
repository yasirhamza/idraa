"""Partition the Monte Carlo engine payload into a slim summary (kept on the
run) and the heavy per-iteration sample arrays (moved to run_samples).

Topology (run_executor.py): SINGLE has base_risk/residual_risk one level deep;
AGGREGATE adds aggregate_with/without_controls (one level) and
per_scenario[i].base_risk/residual_risk (two levels, under a list). The walk
targets ONLY these known containers so unrelated dicts are never mis-moved;
compound path keys make merge order-independent. (#294 / #297.)
"""

from __future__ import annotations

import copy as _copy
from typing import Any

SAMPLE_ARRAY_KEY = "simulation_results"

# Version stamp written INTO run.simulation_results at the persist site
# (run_executor, AFTER split_simulation_payload — split/merge stay pure so
# the run_samples backfill round-trip remains lossless). Additive key: every
# existing reader does named-key .get() lookups, so nothing unwraps.
#
# Policy: bump when a key is RENAMED, REMOVED, or its meaning changes;
# purely ADDITIVE keys do NOT need a bump. Legacy rows (pre-stamp) lack the
# key and read back as version 0 via results_schema_version().
SIMULATION_RESULTS_SCHEMA_VERSION = 1


def results_schema_version(summary: dict[str, Any]) -> int:
    """Schema version of a persisted ``run.simulation_results`` payload.

    0 == legacy row written before the stamp existed.
    """
    return int(summary.get("schema_version", 0))


_RISK_CONTAINERS = (
    "base_risk",
    "residual_risk",
    "aggregate_with_controls",
    "aggregate_without_controls",
)


def _pop_array(container: Any, path: str, out: dict[str, Any]) -> None:
    if isinstance(container, dict) and SAMPLE_ARRAY_KEY in container:
        out[path] = container.pop(SAMPLE_ARRAY_KEY)


def split_simulation_payload(
    payload: dict[str, Any], *, copy: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = _copy.deepcopy(payload) if copy else payload
    arrays: dict[str, Any] = {}
    for key in _RISK_CONTAINERS:
        _pop_array(summary.get(key), key, arrays)
    per_scenario = summary.get("per_scenario")
    if isinstance(per_scenario, list):
        for i, entry in enumerate(per_scenario):
            if isinstance(entry, dict):
                _pop_array(entry.get("base_risk"), f"per_scenario/{i}/base_risk", arrays)
                _pop_array(entry.get("residual_risk"), f"per_scenario/{i}/residual_risk", arrays)
    return summary, arrays


def merge_simulation_payload(
    summary: dict[str, Any], arrays: dict[str, Any] | None
) -> dict[str, Any]:
    merged = _copy.deepcopy(summary)
    if not arrays:
        return merged
    for path, array in arrays.items():
        parts = path.split("/")
        target: Any = merged
        for p in parts:
            target = target[int(p)] if p.isdigit() else target[p]
        target[SAMPLE_ARRAY_KEY] = array
    return merged
