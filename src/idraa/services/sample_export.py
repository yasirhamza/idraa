"""Raw Monte Carlo sample export — wide gzipped CSV (#109).

Pure functions between the run_samples stores and the streaming route:
column mapping (index -> scenario id, pinned by regression test), provenance
preamble, and a chunked gzip CSV generator that never materialises the full
CSV. Spec: docs/superpowers/specs/2026-07-23-run-samples-export-design.md.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
import zlib
from collections.abc import Iterator
from typing import Any

import numpy as np

from idraa.models.risk_analysis_run import RiskAnalysisRun
from idraa.models.run_samples import RunSamples
from idraa.services.sample_codec import decode_sample_arrays_np
from idraa.utils.csv_export import sanitize_cell

_TOP_LEVEL_ORDER = (
    "base_risk",
    "residual_risk",
    "aggregate_without_controls",
    "aggregate_with_controls",
)
# [0-9] not \d (Sec3-N1): \d matches Unicode decimal digits and int() accepts
# them, so a crafted path like per_scenario/٣/… would alias ASCII index 3 —
# silent column overwrite, the mislabeling class this exporter must never risk.
_PS_PATH = re.compile(r"^per_scenario/([0-9]+)/(base_risk|residual_risk)$")
_DEFLATE_LEVEL = 6
_GZIP_WBITS = 31  # gzip framing (pd.read_csv-compatible), NOT the codec's raw zlib

PRECISION_NOTE = (
    "precision: values exported as float32 (codec rows store float32; legacy "
    "rows are downcast at export); in-app metrics were computed from float64 "
    "at run time — expect ~1e-6 relative agreement, not exact equality"
)


def samples_row_to_arrays(row: RunSamples) -> dict[str, np.ndarray]:
    """Both stores a run_samples row can hold (issue #109 Q2); never reaches
    into run.simulation_results."""
    if row.arrays_codec is not None:
        return decode_sample_arrays_np(row.arrays_codec)
    return {k: np.asarray(v, dtype=np.float32) for k, v in (row.arrays or {}).items()}


# Sec-B2 + Sec2-B1: ALLOWLIST, not denylist. Legend lines are mid-line and
# unquoted, so CSV quoting can't protect them — Excel parses '#' lines as
# data and splits on the SYSTEM list separator: ',' in en locales, ';' in
# most European locales. A name like 'Backup;=HYPERLINK(...)' would open a
# fresh cell holding a live formula. The allowlist kills every separator
# and every formula trigger except '-' (legitimate in names; prefix-escaped
# below when leading). Lossy is fine: legend lines are for humans, the hex
# id is the machine key.
_COMMENT_DISALLOWED = re.compile(r"[^A-Za-z0-9 _.()\-]")


def _flatten_comment_text(value: str) -> str:
    """Flatten a scenario name for the '# ' comment frame (see allowlist
    rationale above), then prefix-escape any surviving leading trigger."""
    return str(sanitize_cell(_COMMENT_DISALLOWED.sub(" ", value)))


def build_export_columns(
    summary: dict[str, Any], arrays: dict[str, np.ndarray]
) -> tuple[list[tuple[str, np.ndarray]], list[str]]:
    """Canonically ordered (header, array) pairs + scenario legend lines.

    per_scenario/{i}/... maps to summary["per_scenario"][i] BY CONSTRUCTION:
    split_simulation_payload pops arrays from the very entries it walks.
    A missing/malformed summary entry degrades to the honest positional
    header per_scenario_<i>_<kind> — never a guessed scenario id.
    """
    columns: list[tuple[str, np.ndarray]] = []
    legend: list[str] = []
    consumed: set[str] = set()
    for key in _TOP_LEVEL_ORDER:
        if key in arrays:
            columns.append((key, arrays[key]))
            consumed.add(key)

    per_scenario_meta = summary.get("per_scenario")
    ps: dict[int, dict[str, np.ndarray]] = {}
    for path, arr in arrays.items():
        m = _PS_PATH.match(path)
        if m:
            idx, kind = int(m.group(1)), m.group(2)
            if kind in ps.get(idx, {}):  # Sec3-N1: leading-zero aliases collide here
                raise ValueError(f"duplicate per-scenario array path: {path}")
            ps.setdefault(idx, {})[kind] = arr
            consumed.add(path)

    # SWE2-I1: an array path this mapper doesn't recognise must fail loudly,
    # not vanish — the export's whole claim is "every persisted number is
    # independently checkable". A new path added upstream forces a deliberate
    # exporter update (the unit test pins this tripwire).
    unknown = sorted(set(arrays) - consumed)
    if unknown:
        raise ValueError(f"unrecognised sample array paths: {unknown}")
    for i in sorted(ps):
        entry = (
            per_scenario_meta[i]
            if isinstance(per_scenario_meta, list)
            and i < len(per_scenario_meta)
            and isinstance(per_scenario_meta[i], dict)
            else None
        )
        sid_raw = entry.get("scenario_id") if entry else None
        try:
            sid = uuid.UUID(str(sid_raw)) if sid_raw else None
        except ValueError:
            sid = None
        for kind in ("base_risk", "residual_risk"):
            if kind not in ps[i]:
                continue
            header = f"scenario_{sid.hex}_{kind}" if sid else f"per_scenario_{i}_{kind}"
            columns.append((header, ps[i][kind]))
        if sid and entry:
            name = _flatten_comment_text(str(entry.get("scenario_name", "")))
            legend.append(f"column scenario_{sid.hex}_*: scenario_id={sid} name={name}")

    lengths = {a.shape[0] for _, a in columns}
    if len(lengths) > 1:
        raise ValueError(f"sample arrays have inconsistent lengths: {sorted(lengths)}")
    return columns, legend


def build_preamble(
    *,
    run: RiskAnalysisRun,
    derived_seed_keys: dict[str, int] | None,
    app_version: str,
    legend_lines: list[str],
) -> list[str]:
    generated_at = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    seed = "none" if run.random_seed is None else str(run.random_seed)
    # Sec3-N2: re-derive the mapping through validating constructors instead of
    # trusting a DB JSON column verbatim — a crafted key can't reach the CSV.
    dsk = (
        "none"
        if not derived_seed_keys
        else json.dumps(
            {str(uuid.UUID(k)): int(v) for k, v in derived_seed_keys.items()},
            sort_keys=True,
        )
    )
    return [
        "idraa raw Monte Carlo samples export",
        "schema: samples-export/1",
        f"run_id: {run.id}",
        f"run_type: {run.run_type.value}",
        f"mc_iterations: {run.mc_iterations}",
        f"random_seed: {seed}",
        f"inputs_hash: {run.inputs_hash}",
        f"derived_seed_keys: {dsk}",
        f"app_version: {app_version}",
        f"generated_at: {generated_at}",
        PRECISION_NOTE,
        "iteration index is 0-based",
        *legend_lines,
    ]


def iter_csv_gz(
    preamble: list[str],
    columns: list[tuple[str, np.ndarray]],
    *,
    chunk_rows: int = 10_000,
) -> Iterator[bytes]:
    """SYNC generator of gzip-compressed CSV bytes (Starlette threadpools sync
    iterators, keeping numpy formatting off the event loop). %.9g round-trips
    float32 exactly; \\r\\n per RFC 4180. Data cells are numeric-only by
    construction; injection sanitisation applies to header/preamble text."""
    headers = ["iteration"] + [str(sanitize_cell(h)) for h, _ in columns]
    arrays = [a for _, a in columns]
    n = int(arrays[0].shape[0]) if arrays else 0
    row_fmt = "%d," + ",".join(["%.9g"] * len(arrays)) + "\r\n"

    def plain() -> Iterator[bytes]:
        for line in preamble:
            prefix = "" if line.startswith("#") else "# "
            yield (prefix + line + "\r\n").encode("utf-8")
        yield (",".join(headers) + "\r\n").encode("utf-8")
        for start in range(0, n, chunk_rows):
            end = min(start + chunk_rows, n)
            chunk = [row_fmt % ((i, *(float(a[i]) for a in arrays))) for i in range(start, end)]
            yield "".join(chunk).encode("ascii")

    co = zlib.compressobj(_DEFLATE_LEVEL, zlib.DEFLATED, _GZIP_WBITS)
    for piece in plain():
        out = co.compress(piece)
        if out:
            yield out
    yield co.flush()
