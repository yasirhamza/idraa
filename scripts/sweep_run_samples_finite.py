"""One-off READ-ONLY overflow sweep of run_samples.arrays_codec (idraa#131).

The float32 codec BLOB was the one durable write channel with no non-finite
backstop until PR #99 (strict_json_dumps already fail-closes the JSON
channel). Per the "code fix is half the fix" convention (#346 precedent),
this sweeps every legacy codec row and reports any array containing a
non-finite value (inf / -inf / nan) — the corruption class #99's encoder now
rejects at write time.

Usage:
    python scripts/sweep_run_samples_finite.py /path/to/idraa.db

Strictly read-only: the DB is opened with SQLite URI mode=ro, so the sweep
is safe against prod or a backup copy. Legacy JSON-channel rows
(arrays populated, arrays_codec NULL) are counted but not decoded — that
channel was always guarded by strict_json_dumps.

Exit codes: 0 = all codec rows finite; 1 = corrupt row(s) found (repair per
the #346 gated-migration pattern is a SEPARATE, deliberate step); 2 = usage.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np

from idraa.services.sample_codec import decode_sample_arrays_np


def sweep(db_path: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT run_id, arrays_codec IS NOT NULL, arrays IS NOT NULL FROM run_samples"
        ).fetchall()
        total = len(rows)
        codec_rows = [(r[0],) for r in rows if r[1]]
        json_only = sum(1 for r in rows if not r[1] and r[2])

        corrupt: list[tuple[str, str]] = []  # (run_id, description)
        decode_failures: list[tuple[str, str]] = []
        for (run_id,) in codec_rows:
            blob = conn.execute(
                "SELECT arrays_codec FROM run_samples WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            try:
                arrays = decode_sample_arrays_np(blob)
            except Exception as exc:  # decode failure is itself a finding
                decode_failures.append((str(run_id), f"{type(exc).__name__}: {exc}"))
                continue
            for key, arr in arrays.items():
                bad = int(np.count_nonzero(~np.isfinite(arr)))
                if bad:
                    corrupt.append((str(run_id), f"{key}: {bad}/{arr.size} non-finite"))

        print(f"run_samples rows:        {total}")
        print(f"  codec rows swept:      {len(codec_rows)}")
        print(
            f"  legacy JSON-only rows: {json_only} (channel guarded by strict_json_dumps; skipped)"
        )
        print(f"  decode failures:       {len(decode_failures)}")
        print(f"  non-finite findings:   {len(corrupt)}")
        for run_id, desc in decode_failures:
            print(f"  DECODE-FAIL run {run_id}: {desc}")
        for run_id, desc in corrupt:
            print(f"  NON-FINITE  run {run_id}: {desc}")
        if corrupt or decode_failures:
            print(
                "VERDICT: corrupt rows present — repair/annotate per the #346 gated-migration pattern."
            )
            return 1
        print("VERDICT: clean — no legacy codec row carries a non-finite value.")
        return 0
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    db_path = Path(argv[1])
    if not db_path.exists():
        print(f"no such file: {db_path}")
        return 2
    return sweep(db_path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
